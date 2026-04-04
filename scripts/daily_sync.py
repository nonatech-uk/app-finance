#!/usr/bin/env python3
"""Daily transaction sync orchestrator.

Fetches Monzo + Wise transactions, runs cleaning and dedup pipelines,
pings healthcheck URLs on success.

Called by systemd timer:
    podman exec finance-sync python scripts/daily_sync.py

Env vars:
    HEALTHCHECK_MONZO_URL  — pinged after successful Monzo sync
    HEALTHCHECK_WISE_URL   — pinged after successful Wise sync
"""

import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2

from config.settings import settings


def ping_healthcheck(url: str | None, label: str):
    """Ping a healthcheck URL. Silently skips if not configured."""
    if not url:
        return
    try:
        subprocess.run(
            ["curl", "-fsS", "-m", "10", "--retry", "3", url],
            capture_output=True, timeout=60,
        )
        print(f"  [{label}] Healthcheck pinged.")
    except Exception as e:
        print(f"  [{label}] Healthcheck ping failed: {e}")


def sync_wise() -> dict:
    """Sync Wise transactions for the last 30 days."""
    from src.ingestion.wise import get_profiles, get_balances, fetch_activities, enrich_activities
    from scripts.wise_bulk_load import parse_activity, write_transactions, build_api_fx_events

    since = datetime.now(timezone.utc) - timedelta(days=30)

    profiles = get_profiles()
    profile = next(p for p in profiles if p["type"] == "PERSONAL")
    profile_id = profile["id"]
    print(f"  Wise profile: {profile_id}")

    activities = fetch_activities(profile_id, since=since)
    print(f"  Fetched {len(activities)} activities")

    activities = enrich_activities(profile_id, activities)

    txns = []
    for activity in activities:
        parsed = parse_activity(activity)
        if parsed:
            txns.append(parsed)

    print(f"  Parsed {len(txns)} transactions")

    if not txns:
        return {"inserted": 0, "skipped": 0, "fx_events": 0}

    conn = psycopg2.connect(settings.dsn)
    try:
        result = write_transactions(txns, conn)
        fx_stats = build_api_fx_events(txns, conn)
        result["fx_events"] = fx_stats["fx_events"]
        return result
    finally:
        conn.close()


def sync_monzo() -> dict:
    """Sync Monzo transactions for the last 30 days."""
    from src.ingestion.monzo import authenticate, list_accounts, fetch_transactions, AuthRequiredError
    from src.ingestion.writer import write_monzo_transactions

    access_token = authenticate(headless=True)
    auth_time = time.time()

    accounts = list_accounts(access_token)
    since = datetime.now(timezone.utc) - timedelta(days=30)

    total_inserted = 0
    total_skipped = 0

    for acc in accounts:
        if acc.get("closed"):
            continue

        acc_id = acc["id"]
        print(f"  Account: {acc_id}")
        txns = fetch_transactions(access_token, acc_id, since=since, auth_time=auth_time)
        print(f"  Fetched {len(txns)} transactions")

        if txns:
            result = write_monzo_transactions(txns, acc_id)
            total_inserted += result["inserted"]
            total_skipped += result["skipped"]

    return {"inserted": total_inserted, "skipped": total_skipped}


def run_cleaning():
    """Run the cleaning pipeline for new transactions."""
    from src.cleaning.processor import process_all
    from src.cleaning.matcher import match_all

    print("  Cleaning raw merchants...")
    process_all()
    print("  Matching to canonical merchants...")
    match_all()


def run_dedup():
    """Run the dedup pipeline."""
    from src.dedup.matcher import find_duplicates

    conn = psycopg2.connect(settings.dsn)
    try:
        result = find_duplicates(conn)
        print(f"  Superseded: {result['source_superseded']}, "
              f"Cross-source: {result['cross_source_groups']}, "
              f"Extended: {result['cross_source_extended']}, "
              f"iBank internal: {result['ibank_internal_groups']}, "
              f"Skipped: {result['skipped']}")
    finally:
        conn.close()


def run_categorisation():
    """Run full categorisation pipeline: naming, source hints, LLM, fuzzy merge, Amazon, override learning."""
    from src.categorisation.engine import run_all

    conn = psycopg2.connect(settings.dsn)
    try:
        results = run_all(conn)
        # Summarise key stats
        naming = results.get('naming', {})
        hints = results.get('source_hints', {})
        llm = results.get('llm', {})
        fuzzy = results.get('fuzzy', {})
        amazon = results.get('amazon', {})
        overrides = results.get('overrides', {})
        print(f"  Names: {naming.get('display_names_set', 0)} set, "
              f"Hints: {hints.get('auto_accepted', 0)} accepted / {hints.get('queued', 0)} queued, "
              f"LLM: {llm.get('llm_auto_accepted', 0)} accepted / {llm.get('llm_queued', 0)} queued, "
              f"Fuzzy: {fuzzy.get('fuzzy_auto_merged', 0)} merged / {fuzzy.get('fuzzy_suggested', 0)} suggested, "
              f"Amazon: {amazon.get('amazon_categorised', 0)} categorised, "
              f"Learned: {overrides.get('merchant_corrections', 0)} corrections")
    finally:
        conn.close()


def main():
    hc_monzo = os.environ.get("HEALTHCHECK_MONZO_URL")
    hc_wise = os.environ.get("HEALTHCHECK_WISE_URL")

    print(f"=== Daily Sync — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n")

    # Step 1: Wise
    wise_ok = False
    print("Step 1: Sync Wise...")
    try:
        result = sync_wise()
        print(f"  Result: {result['inserted']} new, {result['skipped']} dupes, "
              f"{result.get('fx_events', 0)} FX events")
        wise_ok = True
    except Exception as e:
        print(f"  ERROR syncing Wise: {e}")
        traceback.print_exc()

    # Step 2: Monzo
    monzo_ok = False
    print("\nStep 2: Sync Monzo...")
    try:
        result = sync_monzo()
        print(f"  Result: {result['inserted']} new, {result['skipped']} dupes")
        monzo_ok = True
    except Exception as e:
        print(f"  ERROR syncing Monzo: {e}")
        traceback.print_exc()

    # Step 3: Cleaning
    print("\nStep 3: Cleaning pipeline...")
    try:
        run_cleaning()
        print("  Done.")
    except Exception as e:
        print(f"  ERROR in cleaning: {e}")
        traceback.print_exc()

    # Step 4: Dedup
    print("\nStep 4: Dedup pipeline...")
    try:
        run_dedup()
        print("  Done.")
    except Exception as e:
        print(f"  ERROR in dedup: {e}")
        traceback.print_exc()

    # Step 5: Auto-categorisation (source hints only, no LLM)
    print("\nStep 5: Auto-categorisation...")
    try:
        run_categorisation()
        print("  Done.")
    except Exception as e:
        print(f"  ERROR in categorisation: {e}")
        traceback.print_exc()

    # Step 6: Apply merchant split rules
    print("\nStep 6: Apply merchant split rules...")
    try:
        from scripts.apply_split_rules import apply_split_rules
        result = apply_split_rules()
        print(f"  Rules: {result['total_rules']}, Overrides: {result['overrides_created']}")
    except Exception as e:
        print(f"  ERROR applying split rules: {e}")
        traceback.print_exc()

    # Step 7: Apply tag rules
    print("\nStep 7: Apply tag rules...")
    try:
        from src.api.routers.tag_rules import reconcile_tag_rules
        conn = psycopg2.connect(settings.dsn)
        try:
            result = reconcile_tag_rules(conn)
            print(f"  Rules: {result['rules_applied']}, "
                  f"Created: {result['tags_created']}, Removed: {result['tags_removed']}")
        finally:
            conn.close()
    except Exception as e:
        print(f"  ERROR applying tag rules: {e}")
        traceback.print_exc()

    # Step 7.5: Splitwise sync — removed, now manual via UI at /splitwise

    # Step 8: Link ATM cash withdrawals
    print("\nStep 8: Link ATM cash withdrawals...")
    try:
        from scripts.link_cash_withdrawals import (
            link_cash_withdrawals, suppress_ibank_cash_duplicates,
            recalculate_balance_resets,
        )
        result = link_cash_withdrawals()
        print(f"  Inserted: {result['inserted']}, Linked: {result['linked']}, "
              f"Accounts: {result['accounts_created']}")

        # Suppress iBank cash withdrawal entries that duplicate ATM mirrors
        suppressed = suppress_ibank_cash_duplicates()
        if suppressed:
            print(f"  Suppressed {suppressed} duplicate iBank cash entries")

        # Recalculate balance resets to maintain target balances
        reset_result = recalculate_balance_resets()
        if reset_result["updated"]:
            print(f"  Recalculated {reset_result['updated']}/{reset_result['checked']} balance resets")
    except Exception as e:
        print(f"  ERROR linking cash withdrawals: {e}")
        traceback.print_exc()

    # Step 9: Link inter-account transfers and FX conversions
    print("\nStep 9: Link transfers & FX events...")
    try:
        from scripts.link_fx_events import (
            find_fx_pairs, find_same_ccy_pairs, find_visa_payment_pairs,
            find_already_linked, link_fx_pairs, link_same_ccy_pairs,
            link_visa_payment_pairs,
        )
        conn = psycopg2.connect(settings.dsn)
        try:
            cur = conn.cursor()
            already_linked = find_already_linked(cur)

            fx_pairs = find_fx_pairs(cur)
            fx_created, fx_skipped = link_fx_pairs(cur, fx_pairs, already_linked)
            conn.commit()

            xfr_pairs = find_same_ccy_pairs(cur)
            xfr_created, xfr_skipped = link_same_ccy_pairs(cur, xfr_pairs, already_linked)
            conn.commit()

            visa_pairs = find_visa_payment_pairs(cur)
            visa_created, visa_skipped = link_visa_payment_pairs(cur, visa_pairs, already_linked)
            conn.commit()

            print(f"  FX: {fx_created} created, {fx_skipped} skipped")
            print(f"  Transfers: {xfr_created} created, {xfr_skipped} skipped")
            print(f"  Visa payments: {visa_created} created, {visa_skipped} skipped")
        finally:
            conn.close()
    except Exception as e:
        print(f"  ERROR linking transfers: {e}")
        traceback.print_exc()

    # Step 10: Refresh stock prices
    print("\nStep 10: Refresh stock prices...")
    try:
        from src.stocks.prices import fetch_current_prices
        conn = psycopg2.connect(settings.dsn)
        try:
            result = fetch_current_prices(conn)
            print(f"  Updated: {result['updated']} prices, "
                  f"Errors: {len(result['errors'])}")
            for err in result["errors"]:
                print(f"    {err['symbol']}: {err['error']}")
        finally:
            conn.close()
    except Exception as e:
        print(f"  ERROR refreshing prices: {e}")
        traceback.print_exc()

    # Step 11: Sync business transactions to Xero
    print("\nStep 11: Xero sync...")
    try:
        from scripts.xero_sync import sync_to_xero
        from src.ingestion.xero import AuthRequiredError as XeroAuthRequired
        result = sync_to_xero(headless=True)
        print(f"  Result: {result['pushed']} pushed, {result['failed']} failed")
    except XeroAuthRequired:
        print("  Xero token expired — manual re-auth needed (python scripts/xero_setup.py)")
    except Exception as e:
        print(f"  ERROR syncing to Xero: {e}")
        traceback.print_exc()

    # Step 13: Receipt sweep — re-attempt matching + CalDAV alerts for overdue
    print("\nStep 13: Receipt sweep...")
    try:
        from src.receipts.matcher import auto_match_receipt

        conn = psycopg2.connect(settings.dsn)
        try:
            cur = conn.cursor()

            # Re-attempt matching for pending receipts
            cur.execute("""
                SELECT id FROM receipt
                WHERE match_status = 'pending_match'
            """)
            pending = cur.fetchall()
            matched_count = 0
            for (rid,) in pending:
                result = auto_match_receipt(conn, rid)
                if result and result.get("matched"):
                    matched_count += 1

            print(f"  Pending: {len(pending)}, Newly matched: {matched_count}")

            # CalDAV alerts for overdue unmatched receipts
            cur.execute("SELECT value FROM app_setting WHERE key = 'receipt.alert_days'")
            alert_days_row = cur.fetchone()
            alert_days = int(alert_days_row[0]) if alert_days_row else 7

            cur.execute("SELECT value FROM app_setting WHERE key = 'caldav.enabled'")
            caldav_row = cur.fetchone()
            caldav_enabled = caldav_row and caldav_row[0].lower() == "true"

            if caldav_enabled:
                cur.execute("""
                    SELECT id, extracted_merchant, extracted_amount, extracted_date
                    FROM receipt
                    WHERE match_status IN ('pending_match', 'pending_ocr')
                      AND uploaded_at < now() - interval '%s days'
                """, (alert_days,))
                overdue = cur.fetchall()
                if overdue:
                    print(f"  Overdue receipts needing alerts: {len(overdue)}")
                    # CalDAV todo creation would go here
                    # For now just log the count
            else:
                print("  CalDAV disabled — skipping overdue alerts")

        finally:
            conn.close()
    except Exception as e:
        print(f"  ERROR in receipt sweep: {e}")
        traceback.print_exc()

    # Step 12: Healthcheck pings (only on source-level success)
    print("\nStep 12: Healthcheck pings...")
    if wise_ok:
        ping_healthcheck(hc_wise, "Wise")
    else:
        print("  [Wise] Skipped (sync failed)")

    if monzo_ok:
        ping_healthcheck(hc_monzo, "Monzo")
    else:
        print("  [Monzo] Skipped (sync failed)")

    print(f"\n=== Done ===")

    # Exit with error if either source failed
    if not (wise_ok and monzo_ok):
        sys.exit(1)


if __name__ == "__main__":
    main()
