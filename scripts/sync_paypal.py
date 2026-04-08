#!/usr/bin/env python3
"""Sync PayPal transaction history into the local paypal_transaction cache.

Uses PayPal Reporting/Transactions API (REST, JSON).

Usage:
    python scripts/sync_paypal.py
    python scripts/sync_paypal.py --dry-run
    python scripts/sync_paypal.py --days-back=90
"""

import argparse
import base64
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import psycopg2

from config.settings import settings

HC_UUID = settings.hc_paypal_sync
HC_BASE = "https://hc.mees.st/ping"

TOKEN_URL_LIVE = "https://api-m.paypal.com/v1/oauth2/token"
TOKEN_URL_SANDBOX = "https://api-m.sandbox.paypal.com/v1/oauth2/token"
API_BASE_LIVE = "https://api-m.paypal.com"
API_BASE_SANDBOX = "https://api-m.sandbox.paypal.com"

# PayPal limits transaction search to 31 days per request
MAX_RANGE_DAYS = 31


def ping_hc(suffix: str = ""):
    if not HC_UUID:
        return
    try:
        httpx.get(f"{HC_BASE}/{HC_UUID}{suffix}", timeout=5)
    except Exception:
        pass


def get_api_base() -> str:
    return API_BASE_SANDBOX if settings.paypal_environment == "sandbox" else API_BASE_LIVE


def get_token_url() -> str:
    return TOKEN_URL_SANDBOX if settings.paypal_environment == "sandbox" else TOKEN_URL_LIVE


def get_access_token() -> str:
    """Get an access token using client credentials grant."""
    credentials = base64.b64encode(
        f"{settings.paypal_client_id}:{settings.paypal_client_secret}".encode()
    ).decode()

    resp = httpx.post(
        get_token_url(),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {credentials}",
        },
        data={"grant_type": "client_credentials"},
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"PayPal token failed: {resp.status_code} {resp.text}")

    return resp.json()["access_token"]


def sync_transactions(token: str, cursor, days_back: int, dry_run: bool) -> int:
    """Fetch PayPal transactions in 31-day chunks."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days_back)
    api_base = get_api_base()
    total_synced = 0

    with httpx.Client(timeout=30) as client:
        # Loop in 31-day chunks from start to now
        chunk_start = start
        while chunk_start < now:
            chunk_end = min(chunk_start + timedelta(days=MAX_RANGE_DAYS), now)

            start_iso = chunk_start.strftime("%Y-%m-%dT%H:%M:%SZ")
            end_iso = chunk_end.strftime("%Y-%m-%dT%H:%M:%SZ")

            page = 1
            while True:
                # Retry with backoff on 429
                resp = None
                for attempt in range(5):
                    resp = client.get(
                        f"{api_base}/v1/reporting/transactions",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Accept": "application/json",
                        },
                        params={
                            "start_date": start_iso,
                            "end_date": end_iso,
                            "fields": "all",
                            "page_size": 500,
                            "page": page,
                        },
                    )
                    if resp.status_code == 429:
                        wait = 2 ** (attempt + 1)
                        print(f"  Rate limited, waiting {wait}s...")
                        time.sleep(wait)
                        continue
                    break

                if resp is None or resp.status_code != 200:
                    msg = resp.text[:200] if resp else "No response"
                    print(f"  PayPal API error: {resp.status_code if resp else '?'} {msg}")
                    break

                data = resp.json()
                txns = data.get("transaction_details", [])
                if not txns:
                    break

                for txn in txns:
                    info = txn.get("transaction_info", {})
                    payer = txn.get("payer_info", {})
                    cart = txn.get("cart_info", {})

                    txn_id = info.get("transaction_id", "")
                    if not txn_id:
                        continue

                    order_id = info.get("invoice_id") or info.get("paypal_reference_id")

                    # Transaction type code → readable
                    txn_event_code = info.get("transaction_event_code", "")
                    txn_type = "payment"
                    if txn_event_code.startswith("T11"):
                        txn_type = "refund"
                    elif txn_event_code.startswith("T00"):
                        txn_type = "transfer"
                    elif txn_event_code.startswith("T03"):
                        txn_type = "bank_withdrawal"
                    elif txn_event_code.startswith("T04"):
                        txn_type = "bank_deposit"
                    elif txn_event_code.startswith("T02"):
                        txn_type = "received"

                    # Description — try item name first, then subject/note
                    item_details = cart.get("item_details", [])
                    item_name = item_details[0].get("item_name", "") if item_details else ""
                    description = (
                        info.get("transaction_subject")
                        or item_name
                        or info.get("transaction_note")
                        or payer.get("payer_name", {}).get("alternate_full_name", "")
                        or txn_event_code
                        or "PayPal transaction"
                    )

                    # Amount
                    amount_info = info.get("transaction_amount", {})
                    amount = None
                    currency = "GBP"
                    if amount_info:
                        try:
                            amount = float(amount_info.get("value", 0))
                        except (ValueError, TypeError):
                            pass
                        currency = amount_info.get("currency_code", "GBP")

                    # Fee
                    fee = None
                    fee_info = info.get("fee_amount", {})
                    if fee_info:
                        try:
                            fee = float(fee_info.get("value", 0))
                        except (ValueError, TypeError):
                            pass

                    # Net amount
                    net_amount = None
                    if amount is not None:
                        net_amount = amount + (fee or 0)  # fee is typically negative

                    # Counterparty
                    counterparty = payer.get("payer_name", {}).get("alternate_full_name")
                    counterparty_email = payer.get("email_address")

                    # Date
                    txn_date = info.get("transaction_initiation_date")

                    # Status
                    status = info.get("transaction_status")

                    if dry_run:
                        print(f"  [dry-run] {txn_type}: {description} {currency} {amount} ({txn_id})")
                    else:
                        cursor.execute("""
                            INSERT INTO paypal_transaction (
                                paypal_transaction_id, paypal_order_id, transaction_type,
                                description, amount, fee, net_amount, currency,
                                counterparty, counterparty_email, transaction_date,
                                status, raw_json, synced_at
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                            ON CONFLICT (paypal_transaction_id) DO UPDATE SET
                                description = EXCLUDED.description,
                                amount = EXCLUDED.amount,
                                fee = EXCLUDED.fee,
                                net_amount = EXCLUDED.net_amount,
                                status = EXCLUDED.status,
                                synced_at = now()
                        """, (
                            txn_id, order_id, txn_type, description,
                            amount, fee, net_amount, currency,
                            counterparty, counterparty_email, txn_date,
                            status, json.dumps(txn),
                        ))

                    total_synced += 1

                # Pagination
                total_pages = data.get("total_pages", 1)
                if page >= total_pages:
                    break
                page += 1

            chunk_start = chunk_end

    return total_synced


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync PayPal transactions")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing to DB")
    parser.add_argument("--days-back", type=int, default=7, help="How many days back to sync (default: 7)")
    args = parser.parse_args()

    if not settings.paypal_client_id:
        print("ERROR: PAYPAL_CLIENT_ID not set in .env", file=sys.stderr)
        sys.exit(1)

    if not args.dry_run:
        ping_hc("/start")

    rc = 0
    try:
        print("Getting PayPal access token...")
        token = get_access_token()
        print("Token obtained")

        conn = psycopg2.connect(settings.dsn)
        conn.autocommit = False
        cur = conn.cursor()

        try:
            print(f"Syncing transactions (last {args.days_back} days)...")
            count = sync_transactions(token, cur, args.days_back, args.dry_run)

            if not args.dry_run:
                conn.commit()
                print(f"Total: {count} transactions synced")
            else:
                print(f"[dry-run] Would sync {count} transactions")
        finally:
            conn.close()

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        rc = 1

    if not args.dry_run:
        ping_hc(f"/{rc}" if rc else "")

    sys.exit(rc)


if __name__ == "__main__":
    main()
