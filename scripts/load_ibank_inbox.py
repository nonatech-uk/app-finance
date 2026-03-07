#!/usr/bin/env python3
"""Load iBank transactions from NonaFinance.bank8.

NonaFinance.bank8 is a separate Bankivity database with First Direct
transactions (sole account 5682 + credit card 8897).

Uses the same approach as load_ibank_transactions.py.

Usage:
    python scripts/load_ibank_inbox.py --dry-run
    python scripts/load_ibank_inbox.py
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2
import sqlite3

from config.settings import settings

IBANK_PATH = "/Users/stu/Documents/01 Filing/01 Finance/11 iBank/NonaFinance.bank8/StoreContent/core.sql"
COREDATA_EPOCH = datetime(2001, 1, 1)

# Account map for NonaFinance.bank8
# Account names differ from iBank-Mac: sort code+number and masked card number
ACCOUNT_MAP = {
    "40478790245682":                       ("first_direct", "fd_5682"),
    "XXXX XXXX XXXX 8897":                  ("first_direct", "fd_8897"),
}


def coredata_to_date(timestamp: float) -> Optional[str]:
    if timestamp is None:
        return None
    dt = COREDATA_EPOCH + timedelta(seconds=timestamp)
    return dt.strftime("%Y-%m-%d")


def extract_transactions(ibank_conn) -> List[dict]:
    """Extract all non-void bank account transaction legs."""
    cur = ibank_conn.cursor()

    cur.execute("""
        SELECT t.Z_PK, t.ZPTITLE, t.ZPDATE, t.ZPUNIQUEID, t.ZPNOTE,
               t.ZPCLEARED, t.ZPVOID,
               li.ZPTRANSACTIONAMOUNT, li.ZPMEMO, li.ZPUNIQUEID as li_uid,
               a.ZPNAME as acct_name, a.ZPACCOUNTCLASS as acct_class,
               a.Z_PK as acct_pk
        FROM ZTRANSACTION t
        JOIN ZLINEITEM li ON li.ZPTRANSACTION = t.Z_PK
        JOIN ZACCOUNT a ON li.ZPACCOUNT = a.Z_PK
        WHERE a.ZPACCOUNTCLASS NOT IN (6000, 7000)
        ORDER BY t.ZPDATE DESC
    """)

    txn_bank_legs = defaultdict(list)
    txn_meta = {}

    for row in cur.fetchall():
        txn_pk = row[0]
        if txn_pk not in txn_meta:
            txn_meta[txn_pk] = {
                "title": row[1],
                "date": row[2],
                "unique_id": row[3],
                "note": row[4],
                "cleared": row[5],
                "void": row[6],
            }
        txn_bank_legs[txn_pk].append({
            "amount": row[7],
            "memo": row[8],
            "li_uid": row[9],
            "acct_name": row[10],
            "acct_class": row[11],
            "acct_pk": row[12],
        })

    # Category legs
    cur.execute("""
        SELECT li.ZPTRANSACTION, a.ZPNAME, a.ZPFULLNAME,
               li.ZPTRANSACTIONAMOUNT
        FROM ZLINEITEM li
        JOIN ZACCOUNT a ON li.ZPACCOUNT = a.Z_PK
        WHERE a.ZPACCOUNTCLASS IN (6000, 7000)
    """)

    txn_categories = defaultdict(list)
    for row in cur.fetchall():
        txn_categories[row[0]].append({
            "category_name": row[1],
            "category_full": row[2],
            "amount": row[3],
        })

    results = []
    for txn_pk, bank_legs in txn_bank_legs.items():
        meta = txn_meta[txn_pk]

        if meta["void"] == 1:
            continue

        posted_at = coredata_to_date(meta["date"])
        if not posted_at:
            continue

        categories = txn_categories.get(txn_pk, [])

        cat_parts = []
        for cat in categories:
            full = cat.get("category_full") or cat.get("category_name") or ""
            if full:
                cat_parts.append(full)
        ibank_category = " | ".join(cat_parts) if cat_parts else None

        is_transfer = len(bank_legs) > 1 and not categories

        for leg in bank_legs:
            acct_name = leg["acct_name"]
            mapping = ACCOUNT_MAP.get(acct_name)
            if not mapping:
                continue

            institution, account_ref = mapping

            transaction_ref = leg["li_uid"] or meta["unique_id"]
            if not transaction_ref:
                continue

            amount = Decimal(str(leg["amount"])) if leg["amount"] is not None else None
            if amount is None:
                continue

            currency = "GBP"
            if "CHF" in acct_name:
                currency = "CHF"
            elif "EUR" in acct_name:
                currency = "EUR"
            elif "USD" in acct_name or acct_name in ("Citi Savings (US)", "Fidelity GS",
                                                      "Computershare (Citi)"):
                currency = "USD"
            elif "PLN" in acct_name:
                currency = "PLN"

            raw_merchant = meta["title"] or ""

            raw_data = {
                "ibank_txn_pk": txn_pk,
                "ibank_title": meta["title"],
                "ibank_note": meta["note"],
                "ibank_cleared": meta["cleared"],
                "ibank_memo": leg["memo"],
                "ibank_account": acct_name,
                "ibank_category": ibank_category,
                "ibank_is_transfer": is_transfer,
                "ibank_source_db": "NonaFinance.bank8",
            }

            if is_transfer:
                other_legs = [l for l in bank_legs if l["acct_pk"] != leg["acct_pk"]]
                if other_legs:
                    raw_data["ibank_transfer_to"] = other_legs[0]["acct_name"]

            results.append({
                "institution": institution,
                "account_ref": account_ref,
                "transaction_ref": transaction_ref,
                "posted_at": posted_at,
                "amount": amount,
                "currency": currency,
                "raw_merchant": raw_merchant,
                "raw_memo": leg["memo"] or meta["note"] or None,
                "raw_data": raw_data,
                "ibank_category": ibank_category,
            })

    return results


def write_transactions(txns: List[dict], pg_conn) -> Dict[str, int]:
    """Write transactions to raw_transaction. Batched for speed, idempotent."""
    cur = pg_conn.cursor()

    from psycopg2.extras import execute_values

    rows = [
        (
            'ibank',
            txn["institution"],
            txn["account_ref"],
            txn["transaction_ref"],
            txn["posted_at"],
            str(txn["amount"]),
            txn["currency"],
            txn["raw_merchant"],
            txn["raw_memo"],
            False,
            json.dumps(txn["raw_data"]),
        )
        for txn in txns
    ]

    # Batch insert with ON CONFLICT, 500 rows at a time
    inserted = 0
    batch_size = 500
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        result = execute_values(
            cur,
            """INSERT INTO raw_transaction (
                source, institution, account_ref, transaction_ref,
                posted_at, amount, currency,
                raw_merchant, raw_memo, is_dirty, raw_data
            ) VALUES %s
            ON CONFLICT (institution, account_ref, transaction_ref)
                WHERE transaction_ref IS NOT NULL
            DO NOTHING
            RETURNING id""",
            batch,
            page_size=batch_size,
            fetch=True,
        )
        inserted += len(result)
        print(f"    Batch {i//batch_size + 1}: {len(result)} inserted")

    pg_conn.commit()
    return {"inserted": inserted, "skipped": len(txns) - inserted}


def main():
    parser = argparse.ArgumentParser(description="Load iBank transactions from Inbox backup")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report only")
    args = parser.parse_args()

    print("=== iBank Inbox Backup Loader ===")
    print(f"  Source: {IBANK_PATH}\n")

    ibank_conn = sqlite3.connect(IBANK_PATH)
    txns = extract_transactions(ibank_conn)
    ibank_conn.close()

    print(f"  Extracted: {len(txns)} transaction legs\n")

    by_account = Counter((t["institution"], t["account_ref"]) for t in txns)
    for (inst, ref), count in sorted(by_account.items()):
        print(f"    {inst}/{ref}: {count}")

    with_cat = sum(1 for t in txns if t["ibank_category"])
    if txns:
        print(f"\n  With category: {with_cat} ({100*with_cat/len(txns):.0f}%)")
    else:
        print("\n  No transactions found.")
        return

    if args.dry_run:
        print("\n  [DRY RUN] No data written.")
        return

    pg_conn = psycopg2.connect(settings.dsn)
    try:
        result = write_transactions(txns, pg_conn)
        print(f"\n  Written: {result['inserted']} new, {result['skipped']} duplicates/overlaps.")
        print("\n=== Done ===")
    finally:
        pg_conn.close()


if __name__ == "__main__":
    main()
