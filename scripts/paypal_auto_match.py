#!/usr/bin/env python3
"""Auto-match PayPal transactions to bank-side raw_transactions.

Matches PayPal transfer records to raw_transactions where:
  - raw_merchant contains 'paypal' (case-insensitive)
  - amounts match exactly (PayPal transfer amount = bank debit amount)
  - dates within ±3 days

Also tags matched raw_transactions with 'paypal'.

Usage:
    python scripts/paypal_auto_match.py
    python scripts/paypal_auto_match.py --dry-run
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2

from config.settings import settings

DATE_TOLERANCE_DAYS = 5


def auto_match(conn, dry_run: bool) -> dict:
    cur = conn.cursor()
    stats = {"exact": 0, "tagged": 0, "skipped": 0}

    # Get unmatched PayPal transfers (the payment-out side, negative amounts)
    cur.execute("""
        SELECT pt.id, pt.paypal_transaction_id, pt.amount, pt.currency,
               pt.transaction_date::date, pt.description
        FROM paypal_transaction pt
        WHERE pt.transaction_type = 'transfer'
          AND pt.amount < 0
          AND NOT EXISTS (
              SELECT 1 FROM paypal_transaction_match pm
              WHERE pm.paypal_transaction_id = pt.id
          )
        ORDER BY pt.transaction_date DESC
    """)
    unmatched = cur.fetchall()
    print(f"Unmatched PayPal transfers: {len(unmatched)}")

    for pt_id, pp_txn_id, pp_amount, pp_currency, pp_date, pp_desc in unmatched:
        if pp_date is None or pp_amount is None:
            stats["skipped"] += 1
            continue

        # Find matching bank-side transaction
        # PayPal amount is negative (payment out), bank amount is also negative (debit)
        cur.execute("""
            SELECT rt.id, rt.posted_at, rt.amount, rt.raw_merchant
            FROM raw_transaction rt
            WHERE rt.raw_merchant ILIKE '%%paypal%%'
              AND rt.amount = %s
              AND rt.currency = %s
              AND rt.posted_at BETWEEN %s - INTERVAL '%s days' AND %s + INTERVAL '%s days'
              AND NOT EXISTS (
                  SELECT 1 FROM paypal_transaction_match pm
                  WHERE pm.raw_transaction_id = rt.id
              )
            ORDER BY ABS(rt.posted_at - %s::date)
            LIMIT 1
        """, (pp_amount, pp_currency, pp_date, DATE_TOLERANCE_DAYS,
              pp_date, DATE_TOLERANCE_DAYS, pp_date))

        row = cur.fetchone()
        if not row:
            stats["skipped"] += 1
            continue

        rt_id, rt_date, rt_amount, rt_merchant = row

        if dry_run:
            print(f"  [dry-run] match: {pp_desc} ({pp_amount} {pp_currency} {pp_date}) → {rt_merchant} ({rt_amount} {rt_date})")
        else:
            # Create match
            cur.execute("""
                INSERT INTO paypal_transaction_match (paypal_transaction_id, raw_transaction_id, match_confidence)
                VALUES (%s, %s, 0.95)
                ON CONFLICT DO NOTHING
            """, (str(pt_id), str(rt_id)))

            # Tag with 'paypal'
            cur.execute("""
                INSERT INTO transaction_tag (raw_transaction_id, tag, source)
                VALUES (%s, 'paypal', 'auto')
                ON CONFLICT DO NOTHING
            """, (str(rt_id),))
            stats["tagged"] += 1

        stats["exact"] += 1

    if not dry_run:
        conn.commit()

    return stats


def main():
    parser = argparse.ArgumentParser(description="Auto-match PayPal transactions")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = psycopg2.connect(settings.dsn)
    conn.autocommit = False

    try:
        stats = auto_match(conn, args.dry_run)
        prefix = "[dry-run] " if args.dry_run else ""
        print(f"{prefix}Matched: {stats['exact']}, Tagged: {stats['tagged']}, Skipped: {stats['skipped']}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
