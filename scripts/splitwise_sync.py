#!/usr/bin/env python3
"""Two-way Splitwise sync.

Direction 1 (pull): Fetch user's expenses from Splitwise API, write to raw_transaction.
Direction 2 (push): Finance transactions tagged 'splitwise' without a sync log entry
                    -> create in Splitwise via API.

Usage:
    python scripts/splitwise_sync.py
    python scripts/splitwise_sync.py --pull-only
    python scripts/splitwise_sync.py --push-only
    python scripts/splitwise_sync.py --dry-run
    python scripts/splitwise_sync.py --since 2026-01-01
"""

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2

from config.settings import settings
from src.ingestion.splitwise import (
    create_expense,
    fetch_expenses,
    get_current_user,
    get_expense,
    get_group,
    get_original_currency,
    get_user_share,
    map_finance_category,
)


def pull_from_splitwise(
    conn, user_id: int, since: date | None = None, dry_run: bool = False,
) -> dict:
    """Match Splitwise expenses I paid to finance transactions, tag them 'splitwise'.

    For each SW expense where I'm the payer, find a matching finance transaction
    by abs(amount), currency, and date (±1 day). If found and not already tagged,
    add the 'splitwise' tag and a sync log entry.

    Returns {"matched": n, "skipped": n, "unmatched": n, "unmatched_details": [...]}.
    """
    dated_after = datetime.combine(
        since or (date.today() - timedelta(days=30)),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )

    # Pull from all groups — don't filter by default group
    expenses = fetch_expenses(dated_after=dated_after)

    cur = conn.cursor()
    matched = 0
    skipped = 0
    unmatched = 0
    unmatched_details = []

    for expense in expenses:
        expense_id = expense["id"]

        # Skip if already synced
        cur.execute(
            "SELECT 1 FROM splitwise_sync_log WHERE splitwise_expense_id = %s",
            (expense_id,),
        )
        if cur.fetchone():
            skipped += 1
            continue

        # Only expenses I paid
        user_share = get_user_share(expense, user_id)
        if user_share is None:
            continue

        # The cost field is what was paid — match on that
        cost = Decimal(expense.get("cost", "0"))
        if cost == 0:
            skipped += 1
            continue

        expense_date = expense.get("date", "")[:10]  # YYYY-MM-DD
        description = expense.get("description", "")
        currency = expense.get("currency_code", "GBP")

        # Find matching finance transaction by amount + currency + date ±2 days.
        # Try two strategies:
        #   1. Direct match: transaction currency & abs(amount) match SW cost
        #   2. Local currency match: Monzo raw_data local_currency & local_amount match SW cost
        # This handles cases where you paid in CHF/EUR via Monzo (stored as GBP in
        # finance, but raw_data has local_amount/local_currency).
        cur.execute("""
            SELECT rt.id, ssl.id AS sync_id, ssl.splitwise_expense_id AS existing_sw_id
            FROM active_transaction rt
            LEFT JOIN splitwise_sync_log ssl ON ssl.raw_transaction_id = rt.id
            WHERE rt.posted_at BETWEEN (%s::date - interval '2 days') AND (%s::date + interval '2 days')
              AND rt.institution != 'splitwise'
              AND (
                  (rt.currency = %s AND abs(rt.amount) = %s)
                  OR (
                      rt.raw_data->>'local_currency' = %s
                      AND abs((rt.raw_data->>'local_amount')::numeric) = %s * 100
                  )
              )
            ORDER BY abs(rt.posted_at - %s::date) ASC
            LIMIT 1
        """, (expense_date, expense_date, currency, cost, currency, cost, expense_date))

        row = cur.fetchone()

        # Fallback: if no match and SW expense was currency-converted,
        # fetch full expense to get original currency from comments
        if not row:
            full_expense = get_expense(expense_id)
            original = get_original_currency(full_expense)
            if original:
                orig_ccy, orig_amt = original
                orig_cost = Decimal(orig_amt)
                cur.execute("""
                    SELECT rt.id, ssl.id AS sync_id, ssl.splitwise_expense_id AS existing_sw_id
                    FROM active_transaction rt
                    LEFT JOIN splitwise_sync_log ssl ON ssl.raw_transaction_id = rt.id
                    WHERE rt.posted_at BETWEEN (%s::date - interval '2 days') AND (%s::date + interval '2 days')
                      AND rt.institution != 'splitwise'
                      AND (
                          (rt.currency = %s AND abs(rt.amount) = %s)
                          OR (
                              rt.raw_data->>'local_currency' = %s
                              AND abs((rt.raw_data->>'local_amount')::numeric) = %s * 100
                          )
                      )
                    ORDER BY abs(rt.posted_at - %s::date) ASC
                    LIMIT 1
                """, (expense_date, expense_date, orig_ccy, orig_cost, orig_ccy, orig_cost, expense_date))
                row = cur.fetchone()

        if not row:
            unmatched += 1
            unmatched_details.append(f"{expense_date}  {cost:>10} {currency}  {description}")
            continue

        txn_id, sync_id, existing_sw_id = row

        # Already linked to this exact SW expense
        if existing_sw_id == expense_id:
            skipped += 1
            continue

        if dry_run:
            label = "match & tag" if sync_id is None else "link (already tagged)"
            print(f"    [DRY RUN] Would {label}: {expense_date}  {cost:>10} {currency}  {description}")
            matched += 1
            continue

        # Add 'splitwise' tag if not already present
        cur.execute("""
            INSERT INTO transaction_tag (raw_transaction_id, tag)
            VALUES (%s, 'splitwise')
            ON CONFLICT DO NOTHING
        """, (str(txn_id),))

        if sync_id is not None:
            # Update existing sync log with real SW expense ID
            cur.execute("""
                UPDATE splitwise_sync_log
                SET splitwise_expense_id = %s
                WHERE id = %s
            """, (expense_id, str(sync_id)))
        else:
            # Create new sync log entry
            cur.execute("""
                INSERT INTO splitwise_sync_log
                    (raw_transaction_id, splitwise_expense_id, direction)
                VALUES (%s, %s, 'pull')
                ON CONFLICT DO NOTHING
            """, (str(txn_id), expense_id))

        matched += 1

    if not dry_run:
        conn.commit()

    return {"matched": matched, "skipped": skipped, "unmatched": unmatched, "unmatched_details": unmatched_details}


def fetch_unsynced_tagged_transactions(
    conn, since: date | None = None,
) -> list[dict]:
    """Query finance transactions tagged 'splitwise' not yet in splitwise_sync_log."""
    cur = conn.cursor()

    since_clause = ""
    params: list = []
    if since:
        since_clause = "AND rt.posted_at >= %s"
        params.append(since)

    cur.execute(f"""
        SELECT
            rt.id,
            rt.posted_at,
            rt.amount,
            rt.currency,
            rt.raw_merchant,
            rt.raw_memo,
            COALESCE(cm_override.display_name, cm_override.name,
                     cm.display_name, cm.name) AS merchant_name,
            COALESCE(tcat.full_path, cat_override.full_path, cat.full_path) AS category_path,
            tn.note
        FROM active_transaction rt
        LEFT JOIN cleaned_transaction ct ON ct.raw_transaction_id = rt.id
        LEFT JOIN merchant_raw_mapping mrm ON mrm.cleaned_merchant = ct.cleaned_merchant
        LEFT JOIN canonical_merchant cm ON cm.id = mrm.canonical_merchant_id
        LEFT JOIN transaction_merchant_override tmo ON tmo.raw_transaction_id = rt.id
        LEFT JOIN canonical_merchant cm_override ON cm_override.id = tmo.canonical_merchant_id
        LEFT JOIN category cat ON cat.full_path = cm.category_hint
        LEFT JOIN category cat_override ON cat_override.full_path = cm_override.category_hint
        LEFT JOIN transaction_category_override tco ON tco.raw_transaction_id = rt.id
        LEFT JOIN category tcat ON tcat.full_path = tco.category_path
        LEFT JOIN transaction_note tn ON tn.raw_transaction_id = rt.id
        JOIN transaction_tag tt
            ON tt.raw_transaction_id = rt.id AND lower(tt.tag) = 'splitwise'
        LEFT JOIN splitwise_sync_log ssl ON ssl.raw_transaction_id = rt.id
        WHERE ssl.id IS NULL
          {since_clause}
        ORDER BY rt.posted_at, rt.id
    """, params)

    columns = [desc[0] for desc in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def push_to_splitwise(
    conn,
    user_id: int,
    group: dict,
    since: date | None = None,
    dry_run: bool = False,
) -> dict:
    """Create Splitwise expenses for unsynced tagged finance transactions.

    Returns {"pushed": n, "skipped": n, "failed": n, "errors": [...]}.
    """
    txns = fetch_unsynced_tagged_transactions(conn, since=since)

    if not txns:
        return {"pushed": 0, "skipped": 0, "failed": 0, "errors": []}

    print(f"  Found {len(txns)} unsynced splitwise-tagged transactions.")

    group_id = group["id"]
    members = group.get("members", [])

    cur = conn.cursor()
    pushed = 0
    skipped = 0
    failed = 0
    errors = []

    for txn in txns:
        amount = abs(float(txn["amount"]))
        if amount == 0:
            skipped += 1
            continue

        merchant = txn["merchant_name"] or txn["raw_merchant"] or "Unknown"
        category_id = map_finance_category(txn["category_path"])
        currency = txn["currency"]
        txn_date = txn["posted_at"].strftime("%Y-%m-%dT%H:%M:%SZ")

        # Build details from raw_memo and note
        detail_parts = []
        if txn["raw_memo"]:
            detail_parts.append(str(txn["raw_memo"]))
        if txn["note"]:
            detail_parts.append(str(txn["note"]))
        details = " | ".join(detail_parts) if detail_parts else None

        # Equal split across all group members
        cost_str = f"{amount:.2f}"
        member_count = len(members)
        per_person = amount / member_count if member_count > 0 else amount
        per_person_str = f"{per_person:.2f}"

        splits = []
        if member_count > 1:
            for member in members:
                mid = member.get("id")
                splits.append({"user_id": mid, "owed_share": per_person_str})
        else:
            # Single member (e.g. non-group expenses) — full cost to payer
            splits.append({"user_id": user_id, "owed_share": cost_str})

        if dry_run:
            print(f"    [DRY RUN] Would push: {txn['posted_at'].strftime('%Y-%m-%d')}  "
                  f"{cost_str:>10} {currency}  {merchant[:40]:<40s}  cat={category_id}")
            pushed += 1
            continue

        try:
            created = create_expense(
                cost=cost_str,
                description=merchant,
                date=txn_date,
                currency_code=currency,
                category_id=category_id,
                group_id=group_id,
                payer_user_id=user_id,
                splits=splits,
                details=details,
            )
            splitwise_expense_id = created["id"]

            cur.execute("""
                INSERT INTO splitwise_sync_log
                    (raw_transaction_id, splitwise_expense_id, direction)
                VALUES (%s, %s, 'push')
                ON CONFLICT DO NOTHING
            """, (str(txn["id"]), splitwise_expense_id))
            conn.commit()
            pushed += 1

        except Exception as e:
            errors.append(f"{txn['posted_at'].strftime('%Y-%m-%d')} {merchant}: {e}")
            failed += 1

    return {"pushed": pushed, "skipped": skipped, "failed": failed, "errors": errors}


def normalise_splitwise_tags(conn) -> int:
    """Normalise 'Splitwise' tags to lowercase 'splitwise'."""
    cur = conn.cursor()
    # Update capitalised variants to lowercase, skipping if lowercase already exists
    cur.execute("""
        UPDATE transaction_tag
        SET tag = 'splitwise'
        WHERE lower(tag) = 'splitwise' AND tag != 'splitwise'
          AND NOT EXISTS (
              SELECT 1 FROM transaction_tag t2
              WHERE t2.raw_transaction_id = transaction_tag.raw_transaction_id
                AND t2.tag = 'splitwise'
          )
    """)
    updated = cur.rowcount
    # Delete remaining uppercase duplicates (where lowercase already exists)
    cur.execute("""
        DELETE FROM transaction_tag
        WHERE lower(tag) = 'splitwise' AND tag != 'splitwise'
    """)
    conn.commit()
    return updated


def sync_splitwise(
    since: date | None = None,
    dry_run: bool = False,
    pull_only: bool = False,
    push_only: bool = False,
) -> dict:
    """Main entry point. Called by daily_sync.py."""
    user = get_current_user()
    user_id = user["id"]

    group_id = settings.splitwise_default_group_id
    if group_id is None:
        raise RuntimeError(
            "SPLITWISE_DEFAULT_GROUP_ID not configured in .env"
        )

    conn = psycopg2.connect(settings.dsn)
    try:
        # Normalise tag casing
        normalised = normalise_splitwise_tags(conn)
        if normalised:
            print(f"  Normalised {normalised} 'Splitwise' tags to lowercase.")

        result = {"pull": {"matched": 0, "skipped": 0, "unmatched": 0, "unmatched_details": []}, "push": {"pushed": 0, "skipped": 0, "failed": 0, "errors": []}}

        if not push_only:
            result["pull"] = pull_from_splitwise(conn, user_id, since=since, dry_run=dry_run)

        if not pull_only:
            group = get_group(group_id)
            result["push"] = push_to_splitwise(conn, user_id, group, since=since, dry_run=dry_run)

        return result
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Two-way Splitwise sync")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be synced")
    parser.add_argument("--pull-only", action="store_true", help="Only pull from Splitwise")
    parser.add_argument("--push-only", action="store_true", help="Only push to Splitwise")
    parser.add_argument("--since", type=str, help="Only sync from this date (YYYY-MM-DD)")
    args = parser.parse_args()

    since = date.fromisoformat(args.since) if args.since else None

    print("=== Splitwise Sync ===\n")
    result = sync_splitwise(
        since=since,
        dry_run=args.dry_run,
        pull_only=args.pull_only,
        push_only=args.push_only,
    )
    pull = result["pull"]
    print(f"\nPull: {pull['matched']} matched & tagged, {pull['skipped']} already synced, {pull['unmatched']} unmatched")
    if pull.get("unmatched_details"):
        print("\nUnmatched SW expenses (no finance txn found):")
        for detail in pull["unmatched_details"]:
            print(f"    {detail}")
    print(f"Push: {result['push']['pushed']} created, {result['push']['skipped']} skipped, "
          f"{result['push']['failed']} failed")
    if result["push"].get("errors"):
        print("\nErrors:")
        for err in result["push"]["errors"]:
            print(f"  {err}")


if __name__ == "__main__":
    main()
