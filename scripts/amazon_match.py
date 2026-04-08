#!/usr/bin/env python3
"""Amazon order-to-transaction matcher.

Reads Amazon order totals from the stuff database, finds Amazon-like bank
transactions in the finance database, and creates matches in amazon_order_match.

CSV loading is now handled by the stuff app's upload endpoint.

Usage:
    python scripts/amazon_match.py
    python scripts/amazon_match.py --dry-run
"""

import argparse
import sys
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2

from config.settings import settings


def build_order_totals() -> Dict[str, dict]:
    """Group amazon_order_item by order_id, compute total per order.

    Reads from the stuff database (cross-DB).
    """
    conn = psycopg2.connect(settings.stuff_dsn)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT order_id, order_date,
                   SUM(unit_price * quantity) as total,
                   COUNT(*) as item_count,
                   array_agg(DISTINCT category) FILTER (WHERE category IS NOT NULL) as categories
            FROM amazon_order_item
            WHERE unit_price IS NOT NULL
            GROUP BY order_id, order_date
            ORDER BY order_date DESC
        """)

        orders = {}
        for row in cur.fetchall():
            orders[row[0]] = {
                "order_id": row[0],
                "order_date": row[1],
                "total": row[2],
                "item_count": row[3],
                "categories": row[4] or [],
            }
        return orders
    finally:
        conn.close()


def find_amazon_transactions(conn) -> List[dict]:
    """Find all active bank transactions that look like Amazon charges."""
    cur = conn.cursor()
    cur.execute("""
        SELECT id, posted_at, amount, currency, raw_merchant, institution
        FROM active_transaction
        WHERE (
            raw_merchant ILIKE '%%AMZN%%'
            OR raw_merchant ILIKE '%%AMAZON%%'
            OR raw_merchant ILIKE '%%AMZ %%'
            OR raw_merchant ILIKE '%%Amazon.co%%'
        )
        AND amount < 0
        ORDER BY posted_at DESC
    """)

    txns = []
    for row in cur.fetchall():
        txns.append({
            "id": row[0],
            "posted_at": row[1],
            "amount": abs(row[2]),  # make positive for matching
            "currency": row[3],
            "raw_merchant": row[4],
            "institution": row[5],
        })

    return txns


def match_orders_to_transactions(
    orders: Dict[str, dict],
    txns: List[dict],
    conn,
    dry_run: bool = False,
) -> Dict[str, int]:
    """Match Amazon orders to bank transactions by date + amount.

    Strategy:
    1. Exact match: order total == transaction amount, within ±5 days
    2. Close match: order total within 5% of transaction amount, within ±5 days
       (accounts for shipping, discounts, etc.)
    """
    cur = conn.cursor()
    stats = {"exact": 0, "close": 0, "skipped_existing": 0}
    date_window = timedelta(days=5)

    # Index orders by date for efficient lookup
    orders_by_date = defaultdict(list)
    for order in orders.values():
        orders_by_date[order["order_date"]].append(order)

    matched_order_ids = set()

    for txn in txns:
        txn_date = txn["posted_at"]
        txn_amount = txn["amount"]

        # Check existing matches for this transaction
        cur.execute("""
            SELECT order_id FROM amazon_order_match
            WHERE raw_transaction_id = %s
        """, (txn["id"],))
        existing = {r[0] for r in cur.fetchall()}

        # Collect candidate orders within date window
        candidates = []
        check_date = txn_date - date_window
        while check_date <= txn_date + date_window:
            for order in orders_by_date.get(check_date, []):
                if order["order_id"] not in existing:
                    candidates.append(order)
            check_date += timedelta(days=1)

        if not candidates:
            continue

        # Try exact match first (within 1p tolerance)
        for order in candidates:
            if order["total"] is None:
                continue
            diff = abs(order["total"] - txn_amount)
            if diff <= Decimal("0.01"):
                if not dry_run:
                    _insert_match(cur, order["order_id"], txn["id"],
                                  Decimal("0.95"), "date_amount_exact",
                                  f"Exact: order {order['total']} == txn {txn_amount}")
                matched_order_ids.add(order["order_id"])
                stats["exact"] += 1

        # Try close match (within 5% — shipping, rounding)
        for order in candidates:
            if order["total"] is None or order["order_id"] in matched_order_ids:
                continue
            diff = abs(order["total"] - txn_amount)
            if diff <= txn_amount * Decimal("0.05") and diff > Decimal("0.01"):
                confidence = Decimal("0.70") - (diff / txn_amount)
                confidence = max(Decimal("0.50"), min(Decimal("0.85"), confidence))
                if not dry_run:
                    _insert_match(cur, order["order_id"], txn["id"],
                                  confidence, "date_amount_close",
                                  f"Close: order {order['total']} vs txn {txn_amount} (diff {diff})")
                matched_order_ids.add(order["order_id"])
                stats["close"] += 1

    if not dry_run:
        conn.commit()

    return stats


def _insert_match(cur, order_id: str, txn_id, confidence, method: str, notes: str):
    """Insert a match row, ignoring duplicates."""
    cur.execute("""
        INSERT INTO amazon_order_match (order_id, raw_transaction_id,
                                        match_confidence, match_method, notes)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (order_id, raw_transaction_id) DO NOTHING
    """, (order_id, txn_id, confidence, method, notes))


def fixup_suppressed_matches(conn, dry_run: bool = False) -> Dict[str, int]:
    """Re-point matches from suppressed transactions to their preferred counterparts."""
    cur = conn.cursor()

    cur.execute("""
        SELECT am.order_id, am.raw_transaction_id, am.match_confidence, am.match_method
        FROM amazon_order_match am
        WHERE NOT EXISTS (
            SELECT 1 FROM active_transaction at WHERE at.id = am.raw_transaction_id
        )
    """)
    stale = cur.fetchall()

    if not stale:
        return {"fixed": 0, "orphaned": 0}

    fixed = 0
    orphaned = 0

    for order_id, old_txn_id, confidence, method in stale:
        cur.execute("""
            SELECT dgm2.raw_transaction_id
            FROM dedup_group_member dgm1
            JOIN dedup_group_member dgm2 ON dgm2.dedup_group_id = dgm1.dedup_group_id
            WHERE dgm1.raw_transaction_id = %s
              AND dgm2.is_preferred = true
              AND dgm2.raw_transaction_id != %s
        """, (str(old_txn_id), str(old_txn_id)))
        row = cur.fetchone()

        if row:
            new_txn_id = row[0]
            if not dry_run:
                cur.execute("""
                    INSERT INTO amazon_order_match
                        (order_id, raw_transaction_id, match_confidence, match_method, notes)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (order_id, raw_transaction_id) DO NOTHING
                """, (order_id, str(new_txn_id), confidence, method,
                      f"Fixup: re-pointed from suppressed {old_txn_id}"))
                cur.execute("""
                    DELETE FROM amazon_order_match
                    WHERE order_id = %s AND raw_transaction_id = %s
                """, (order_id, str(old_txn_id)))
            fixed += 1
        else:
            orphaned += 1

    if not dry_run:
        conn.commit()

    return {"fixed": fixed, "orphaned": orphaned}


def main():
    parser = argparse.ArgumentParser(description="Match Amazon orders to bank transactions")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no DB writes")
    args = parser.parse_args()

    print("=== Amazon Order Matcher ===\n")

    # Step 1: Get order totals from stuff DB
    print("Step 1: Reading order totals from stuff database...")
    orders = build_order_totals()
    print(f"  Orders: {len(orders)}")

    # Step 2: Find Amazon transactions in finance DB
    conn = psycopg2.connect(settings.dsn)

    try:
        print("\nStep 2: Finding Amazon bank transactions...")
        txns = find_amazon_transactions(conn)
        print(f"  Amazon bank transactions: {len(txns)}")

        if txns and orders:
            print("\nStep 3: Matching orders to transactions...")
            match_stats = match_orders_to_transactions(orders, txns, conn, args.dry_run)
            print(f"  Matches: {match_stats['exact']} exact, {match_stats['close']} close")

            if not args.dry_run:
                print("\nStep 4: Fixing up suppressed matches...")
                fixup_stats = fixup_suppressed_matches(conn)
                print(f"  Fixed: {fixup_stats['fixed']}, Orphaned: {fixup_stats['orphaned']}")

                # Summary
                cur = conn.cursor()
                cur.execute("SELECT count(DISTINCT order_id) FROM amazon_order_match")
                matched_orders = cur.fetchone()[0]
                cur.execute("""
                    SELECT count(DISTINCT am.raw_transaction_id)
                    FROM amazon_order_match am
                    JOIN active_transaction at ON at.id = am.raw_transaction_id
                """)
                matched_txns = cur.fetchone()[0]
                print(f"\n  Total matched: {matched_orders} orders <-> {matched_txns} active transactions")

                cur.execute("""
                    SELECT count(*) FROM active_transaction
                    WHERE (raw_merchant ILIKE '%%AMZN%%' OR raw_merchant ILIKE '%%AMAZON%%'
                           OR raw_merchant ILIKE '%%AMZ %%' OR raw_merchant ILIKE '%%Amazon.co%%')
                    AND amount < 0
                """)
                total_amazon = cur.fetchone()[0]
                if total_amazon > 0:
                    print(f"  Coverage: {matched_txns}/{total_amazon} Amazon transactions matched "
                          f"({100*matched_txns/total_amazon:.0f}%)")

        print("\n=== Done ===")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
