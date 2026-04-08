#!/usr/bin/env python3
"""Amazon enrichment: write item descriptions as transaction notes.

Reads amazon_order_match + amazon_order_item and writes formatted notes
to transaction_note for matched active transactions. Also tags them
with 'amazon-matched' for easy filtering.

Idempotent: uses ON CONFLICT and only overwrites notes with source='amazon_match'.

Usage:
    python scripts/amazon_enrich_notes.py
    python scripts/amazon_enrich_notes.py --dry-run
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2

from config.settings import settings


def build_notes(conn) -> dict:
    """Build note text for each matched active transaction.

    Returns {raw_transaction_id: {"note": str, "order_ids": [str]}}

    Reads matches from finance DB and order items from stuff DB (cross-DB).
    """
    cur = conn.cursor()

    # Step 1: Get matches from finance DB
    cur.execute("""
        SELECT am.raw_transaction_id, am.order_id
        FROM amazon_order_match am
        JOIN active_transaction at ON at.id = am.raw_transaction_id
        ORDER BY am.raw_transaction_id, am.order_id
    """)
    matches = cur.fetchall()
    if not matches:
        return {}

    # Collect unique order IDs
    order_ids = list({row[1] for row in matches})

    # Step 2: Fetch order items from stuff DB (cross-DB)
    stuff_conn = psycopg2.connect(settings.stuff_dsn)
    try:
        stuff_cur = stuff_conn.cursor()
        stuff_cur.execute("""
            SELECT order_id, description, unit_price, quantity, currency
            FROM amazon_order_item
            WHERE order_id = ANY(%s) AND unit_price IS NOT NULL
            ORDER BY order_id, description
        """, (order_ids,))
        items_by_order = defaultdict(list)
        for order_id, desc, price, qty, currency in stuff_cur.fetchall():
            items_by_order[order_id].append({
                "description": desc,
                "price": price,
                "quantity": qty,
                "currency": currency or "GBP",
            })
    finally:
        stuff_conn.close()

    # Step 3: Combine matches with items
    txn_orders = defaultdict(lambda: defaultdict(list))
    for txn_id, order_id in matches:
        for item in items_by_order.get(order_id, []):
            txn_orders[str(txn_id)][order_id].append(item)

    results = {}
    for txn_id, orders in txn_orders.items():
        order_ids = sorted(orders.keys())
        all_items = []
        for oid in order_ids:
            all_items.extend(orders[oid])

        note = _format_note(all_items, order_ids)
        results[txn_id] = {"note": note, "order_ids": order_ids}

    return results


def _format_note(items: list, order_ids: list) -> str:
    """Format items into a readable note string."""
    currency_symbols = {"GBP": "£", "USD": "$", "EUR": "€"}

    lines = []
    for item in items:
        sym = currency_symbols.get(item["currency"], item["currency"] + " ")
        price_str = f"{sym}{item['price']:.2f}"
        if item["quantity"] > 1:
            lines.append(f"{item['description']} x{item['quantity']} ({price_str} each)")
        else:
            lines.append(f"{item['description']} ({price_str})")

    order_ref = ", ".join(order_ids)

    if len(lines) <= 3:
        # Compact: single line
        items_str = ", ".join(lines)
        return f"Amazon: {items_str}\nOrder: {order_ref}"
    else:
        # Multi-line for readability
        header = f"Amazon order {order_ref}:" if len(order_ids) == 1 else f"Amazon orders {order_ref}:"
        item_lines = "\n".join(f"- {l}" for l in lines)
        return f"{header}\n{item_lines}"


def write_notes(notes: dict, conn, dry_run: bool = False) -> dict:
    """Write notes to transaction_note.

    - No existing note: insert with source='amazon_match'
    - Existing amazon_match note: replace
    - Existing note from other source: append Amazon info, keep original source
    """
    cur = conn.cursor()
    written = 0
    appended = 0

    for txn_id, data in notes.items():
        if dry_run:
            written += 1
            continue

        # Check if a note already exists
        cur.execute(
            "SELECT note, source FROM transaction_note WHERE raw_transaction_id = %s",
            (txn_id,),
        )
        row = cur.fetchone()

        if row and row[1] not in ("amazon_match",):
            # Append Amazon info to existing note, keep original source
            existing_note = row[0] or ""
            amazon_suffix = f"\n\n{data['note']}"
            # Avoid double-appending if already present
            if data["note"] not in existing_note:
                combined = existing_note + amazon_suffix
                cur.execute("""
                    UPDATE transaction_note
                    SET note = %s, updated_at = now()
                    WHERE raw_transaction_id = %s
                """, (combined, txn_id))
                appended += 1
        else:
            # Insert new or replace existing amazon_match note
            cur.execute("""
                INSERT INTO transaction_note (raw_transaction_id, note, source)
                VALUES (%s, %s, 'amazon_match')
                ON CONFLICT (raw_transaction_id)
                DO UPDATE SET note = EXCLUDED.note,
                              source = 'amazon_match',
                              updated_at = now()
            """, (txn_id, data["note"]))
            written += 1

    if not dry_run:
        conn.commit()

    return {"written": written, "appended": appended}


def write_tags(txn_ids: list, conn, dry_run: bool = False) -> int:
    """Tag matched transactions with 'amazon-matched'."""
    if dry_run:
        return len(txn_ids)

    cur = conn.cursor()
    tagged = 0
    for txn_id in txn_ids:
        cur.execute("""
            INSERT INTO transaction_tag (raw_transaction_id, tag, source)
            VALUES (%s, 'amazon-matched', 'amazon_match')
            ON CONFLICT (raw_transaction_id, tag) DO NOTHING
            RETURNING raw_transaction_id
        """, (txn_id,))
        if cur.fetchone():
            tagged += 1

    conn.commit()
    return tagged


def main():
    parser = argparse.ArgumentParser(description="Enrich Amazon-matched transactions with item notes")
    parser.add_argument("--dry-run", action="store_true", help="Preview notes without writing")
    args = parser.parse_args()

    print("=== Amazon Note Enrichment ===\n")

    conn = psycopg2.connect(settings.dsn)

    try:
        # Build notes
        notes = build_notes(conn)
        print(f"  Transactions with Amazon matches: {len(notes)}")

        if not notes:
            print("  No matches found. Run amazon_load.py --match-only first.")
            return

        # Preview a few
        print("\n  Sample notes:")
        for txn_id, data in list(notes.items())[:3]:
            print(f"    --- Transaction {txn_id[:8]}... ---")
            for line in data["note"].split("\n"):
                print(f"    {line}")
            print()

        if args.dry_run:
            print(f"  [DRY RUN] Would write {len(notes)} notes and {len(notes)} tags.")
            return

        # Write notes
        result = write_notes(notes, conn)
        print(f"  Notes written: {result['written']}, appended to existing: {result['appended']}")

        # Write tags
        txn_ids = list(notes.keys())
        tagged = write_tags(txn_ids, conn)
        print(f"  Tags added: {tagged}")

        print("\n=== Done ===")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
