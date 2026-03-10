"""CSV import orchestration layer.

Auto-detects CSV format, parses, previews against existing data,
and executes imports. Designed to be called from the API endpoint
now and an email handler later.
"""

import csv
import io
import json
import tempfile
from decimal import Decimal
from pathlib import Path

import psycopg2

from config.settings import settings
from src.ingestion.monzo_csv import parse_monzo_csv


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

FORMAT_SIGNATURES = {
    "first_direct_a": {"date", "description", "amount", "balance"},
    "first_direct_b": {"date", "description", "amount", "reference"},
    "marcus": {"transactiondate", "description", "value", "accountbalance"},
    "wise": {"id", "status", "direction", "source currency", "target currency"},
    "monzo": {"transaction id", "date", "time", "type", "name", "amount"},
}


def detect_format(file_bytes: bytes) -> str | None:
    """Detect CSV format from column headers.

    Returns format key or None if unrecognised.
    """
    text = file_bytes.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    try:
        headers = next(reader)
    except StopIteration:
        return None

    normalised = {h.strip().strip('"').lower() for h in headers}

    # Check each format — match if all signature columns present
    for fmt, required in FORMAT_SIGNATURES.items():
        if required.issubset(normalised):
            return fmt
    return None


# ---------------------------------------------------------------------------
# Parsing — delegates to existing loaders
# ---------------------------------------------------------------------------

def _parse_with_tempfile(file_bytes: bytes, parse_fn, **kwargs) -> list[dict]:
    """Write bytes to a temp file and call a file-path-based parser."""
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="wb") as f:
        f.write(file_bytes)
        tmp_path = f.name
    try:
        return parse_fn(tmp_path, **kwargs)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def parse_csv(file_bytes: bytes, fmt: str, institution: str, account_ref: str) -> list[dict]:
    """Parse CSV bytes into normalised transaction dicts.

    All returned dicts have at minimum:
        source, institution, account_ref, transaction_ref,
        posted_at, amount, currency, raw_merchant, raw_data
    """
    if fmt == "monzo":
        return parse_monzo_csv(file_bytes, account_ref)

    if fmt in ("first_direct_a", "first_direct_b"):
        from scripts.fd_csv_load import parse_fd_csv
        txns, _fmt, _acct = _parse_with_tempfile(file_bytes, parse_fd_csv)
        # Normalise to common shape
        for t in txns:
            t["source"] = "first_direct_csv"
            t["institution"] = "first_direct"
            t["account_ref"] = account_ref
            t["currency"] = "GBP"
        return txns

    if fmt == "marcus":
        from scripts.marcus_csv_load import parse_marcus_csv
        txns = _parse_with_tempfile(file_bytes, parse_marcus_csv)
        for t in txns:
            t["source"] = "marcus_csv"
            t["institution"] = "goldman_sachs"
            t["account_ref"] = account_ref
        return txns

    if fmt == "wise":
        from scripts.wise_csv_load import load_csv_files
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="wb") as f:
            f.write(file_bytes)
            tmp_path = f.name
        try:
            txns = load_csv_files([tmp_path])
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        # Normalise
        for t in txns:
            t["source"] = "wise_csv"
            t["institution"] = "wise"
            if "raw_data" not in t:
                t["raw_data"] = {}
        return txns

    raise ValueError(f"Unsupported format: {fmt}")


# ---------------------------------------------------------------------------
# Preview — compare parsed CSV against existing DB data
# ---------------------------------------------------------------------------

def preview_import(
    txns: list[dict], institution: str, account_ref: str, conn
) -> dict:
    """Compare parsed transactions against active_transaction in DB.

    Returns dict with new/existing/mismatches lists and summary counts.
    """
    if not txns:
        return {
            "new": [],
            "existing": [],
            "mismatches": [],
            "total_rows": 0,
            "new_count": 0,
            "existing_count": 0,
            "mismatch_count": 0,
        }

    cur = conn.cursor()

    # Gather all transaction_refs from the CSV
    refs = [t["transaction_ref"] for t in txns if t.get("transaction_ref")]

    # Fetch existing transactions for this account that match any of these refs
    cur.execute("""
        SELECT transaction_ref, amount, posted_at
        FROM active_transaction
        WHERE institution = %s
          AND account_ref = %s
          AND transaction_ref = ANY(%s)
    """, (institution, account_ref, refs))

    existing_map = {}
    for ref, amount, posted_at in cur.fetchall():
        existing_map[ref] = {"amount": amount, "posted_at": str(posted_at)}

    new = []
    existing = []
    mismatches = []

    for t in txns:
        ref = t.get("transaction_ref")
        if not ref or ref not in existing_map:
            new.append(_serialise_txn(t))
        else:
            db_row = existing_map[ref]
            csv_amount = Decimal(str(t["amount"]))
            db_amount = Decimal(str(db_row["amount"]))
            if csv_amount != db_amount:
                mismatches.append({
                    "transaction_ref": ref,
                    "posted_at": t.get("posted_at"),
                    "raw_merchant": t.get("raw_merchant"),
                    "csv_amount": str(csv_amount),
                    "db_amount": str(db_amount),
                })
            else:
                existing.append({"transaction_ref": ref})

    return {
        "new": new,
        "existing": existing,
        "mismatches": mismatches,
        "total_rows": len(txns),
        "new_count": len(new),
        "existing_count": len(existing),
        "mismatch_count": len(mismatches),
    }


def _serialise_txn(t: dict) -> dict:
    """Serialise a transaction dict for JSON response."""
    return {
        "transaction_ref": t.get("transaction_ref"),
        "posted_at": t.get("posted_at"),
        "amount": str(t.get("amount", 0)),
        "currency": t.get("currency", "GBP"),
        "raw_merchant": t.get("raw_merchant"),
    }


# ---------------------------------------------------------------------------
# Execute import
# ---------------------------------------------------------------------------

def execute_import(txns: list[dict], fmt: str, conn) -> dict:
    """Insert new transactions into raw_transaction.

    Uses ON CONFLICT DO NOTHING for idempotency.
    Returns {inserted, skipped}.
    """
    cur = conn.cursor()
    inserted = 0

    for txn in txns:
        cur.execute("""
            INSERT INTO raw_transaction (
                source, institution, account_ref, transaction_ref,
                posted_at, amount, currency,
                raw_merchant, raw_memo, is_dirty, raw_data
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, false, %s
            )
            ON CONFLICT (institution, account_ref, transaction_ref)
                WHERE transaction_ref IS NOT NULL
            DO NOTHING
            RETURNING id
        """, (
            txn.get("source", fmt),
            txn["institution"],
            txn["account_ref"],
            txn["transaction_ref"],
            txn["posted_at"],
            txn["amount"],
            txn.get("currency", "GBP"),
            txn.get("raw_merchant"),
            txn.get("raw_memo"),
            json.dumps(txn.get("raw_data", {})),
        ))
        if cur.fetchone():
            inserted += 1

    conn.commit()
    return {"inserted": inserted, "skipped": len(txns) - inserted}


def run_post_import() -> dict:
    """Run cleaning + dedup + split-rule pipeline after import.

    Each module manages its own DB connection.
    """
    from src.cleaning.processor import process_all
    from src.cleaning.matcher import match_all
    from src.dedup.matcher import find_duplicates
    from src.api.routers.merchants import run_apply_split_rules

    cleaning_stats = process_all()
    match_all()

    conn = psycopg2.connect(settings.dsn)
    try:
        dedup_stats = find_duplicates(conn)
        split_stats = run_apply_split_rules(conn)
    finally:
        conn.close()

    return {
        "cleaning": cleaning_stats or {},
        "dedup": dedup_stats or {},
        "split_rules": split_stats or {},
    }
