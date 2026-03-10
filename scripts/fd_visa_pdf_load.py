#!/usr/bin/env python3
"""First Direct Visa PDF statement parser.

Extracts transactions from First Direct Gold Card (Visa) PDF statements
and outputs a CSV suitable for loading via fd_csv_load.py or direct DB import.

Handles:
  - Multi-page statements
  - Multi-line transaction details (FX rates, flight info)
  - Credits (CR suffix)
  - Non-sterling transaction fees
  - Second cardholder section (ignored if no transactions)

Usage:
    # Parse all statements to CSV
    python scripts/fd_visa_pdf_load.py "/path/to/statements/*.pdf" -o visa_transactions.csv

    # Dry run - show what would be parsed
    python scripts/fd_visa_pdf_load.py "/path/to/statements/*.pdf" --dry-run

    # Load directly into database
    python scripts/fd_visa_pdf_load.py "/path/to/statements/*.pdf" --load
"""

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from glob import glob
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Card prefix -> account_ref mapping
CARD_PREFIX_MAP = {
    '4543 6120': 'fd_8178',   # Joint Visa
    '4543 6121': 'fd_8897',   # Sole Visa
}

# --- Date parsing ---

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Matches: "16 Jan 26", "16 Jan 2026", "16Jan26", "16.Jun 10" (OCR variants)
DATE_RE = re.compile(
    r"(\d{1,2})[\s.,]*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[\s.,]*(\d{2,4})",
    re.IGNORECASE,
)

# Statement date: "Statement Date 15 February 2026" or "Statement Date    13 March 2020"
# Allow 4-5 digit year to catch OCR garbles like "22013" (for 2013)
STATEMENT_DATE_RE = re.compile(
    r"Statement\s+Date\s+(\d{1,2})\s+(\w+)\s+(\d{4,5})",
    re.IGNORECASE,
)

# Amount at end of line: digits with optional comma, decimal point, two digits, optional CR
# Note: space between amount and CR is common in larger amounts
# Allow comma as decimal separator (OCR artifact), case-insensitive CR
AMOUNT_RE = re.compile(r"([\d,]+[.,]\d{2})\s*(CR)?\s*$", re.IGNORECASE)

# Transaction line: starts with a date pattern (spaces/punctuation optional for OCR variants)
_DATE_PAT = r"(\d{1,2}[\s.,]*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[\s.,]*\d{2,4})"
TXN_LINE_RE = re.compile(
    rf"^[\s\-=]*{_DATE_PAT}[\s\-—=.]+{_DATE_PAT}[\s.)]+(.+)$",
    re.IGNORECASE,
)

# Section headers to skip
SKIP_PATTERNS = [
    re.compile(r"Your Transaction Details", re.IGNORECASE),
    re.compile(r"Received By Us\s+Transaction Date", re.IGNORECASE),
    re.compile(r"Summary Of Interest", re.IGNORECASE),
    re.compile(r"NO TRANSACTIONS FOR THIS ACCOUNT", re.IGNORECASE),
    re.compile(r"NO INTEREST CHARGED", re.IGNORECASE),
    re.compile(r"Estimated interest", re.IGNORECASE),
    re.compile(r"Summary Box", re.IGNORECASE),
    re.compile(r"Interest free", re.IGNORECASE),
    re.compile(r"your Gold Card statement", re.IGNORECASE),
    re.compile(r"first direct\s*$", re.IGNORECASE),
    re.compile(r"firstdirect\.com", re.IGNORECASE),
    re.compile(r"^\s*03 456", re.IGNORECASE),
    re.compile(r"^\s*40 Wakefield", re.IGNORECASE),
    re.compile(r"Leeds LS98", re.IGNORECASE),
    re.compile(r"MR STUART BEVAN", re.IGNORECASE),
    re.compile(r"Miss Frances", re.IGNORECASE),
    re.compile(r"^\s*Card number", re.IGNORECASE),
    re.compile(r"^\s*Sheet number", re.IGNORECASE),
    re.compile(r"^\s*4543\s+612[01]", re.IGNORECASE),
    re.compile(r"Account Summary", re.IGNORECASE),
    re.compile(r"Credit Lim", re.IGNORECASE),
    re.compile(r"^\s*APR\s", re.IGNORECASE),
    re.compile(r"Previous Balance", re.IGNORECASE),
    re.compile(r"New Balance", re.IGNORECASE),
    re.compile(r"M\s*inim\s*um\s+paym", re.IGNORECASE),
    re.compile(r"Paym\s*ent to be credited", re.IGNORECASE),
    re.compile(r"direct debit on", re.IGNORECASE),
    re.compile(r"allocate your paym", re.IGNORECASE),
    re.compile(r"specific order", re.IGNORECASE),
    re.compile(r"significant difference", re.IGNORECASE),
    re.compile(r"balance is cleared", re.IGNORECASE),
    re.compile(r"take you longer", re.IGNORECASE),
    re.compile(r"^\s*Debits\b", re.IGNORECASE),
    re.compile(r"^\s*Credits\b", re.IGNORECASE),
    re.compile(r"^\s*Amount\s*$", re.IGNORECASE),
    re.compile(r"^\s*Details\s*$", re.IGNORECASE),
    re.compile(r"^\s+$"),
    re.compile(r"^\s*£\s*[\d,]+\.\d{2}\s*$"),  # Standalone amount (min payment, credit limit)
    re.compile(r"^\s*\d+\.\d+%\s*$"),  # Standalone percentage
    re.compile(r"If you do not pay", re.IGNORECASE),
    re.compile(r"Your payment of", re.IGNORECASE),
    re.compile(r"by direct debit", re.IGNORECASE),
    re.compile(r"^\s*$"),  # blank
]

# Continuation line patterns (extra detail for previous transaction)
CONTINUATION_PATTERNS = [
    re.compile(r"^\s+Visa Exchange Rate", re.IGNORECASE),
    re.compile(r"^\s+[\d.]+\s+\w{3}@[\d.]+", re.IGNORECASE),  # FX: "10.35 EUR@1.1923"
]


@dataclass
class Transaction:
    received_date: str      # YYYY-MM-DD
    transaction_date: str   # YYYY-MM-DD
    description: str
    amount: Decimal
    is_credit: bool = False
    extra_details: list = field(default_factory=list)
    statement_date: str = ""  # YYYY-MM-DD of the statement


def parse_short_date(date_str: str, statement_year_hint: Optional[int] = None) -> str:
    """Parse '16 Jan 26' -> '2026-01-16'."""
    m = DATE_RE.match(date_str.strip())
    if not m:
        raise ValueError(f"Cannot parse date: {date_str!r}")
    day = int(m.group(1))
    month = MONTH_MAP[m.group(2).lower()]
    year_raw = int(m.group(3))
    if year_raw < 100:
        # Two-digit year: 20 -> 2020, 26 -> 2026
        year = 2000 + year_raw
    else:
        year = year_raw
    # Fix OCR-garbled years using statement date hint
    if statement_year_hint:
        if not (1990 <= year <= 2030) or abs(year - statement_year_hint) > 1:
            year = statement_year_hint
    return f"{year:04d}-{month:02d}-{day:02d}"


def extract_statement_date(text: str, filename_year: Optional[int] = None) -> Optional[str]:
    """Extract statement date from PDF text.

    If filename_year is provided, it's used to validate/correct OCR-garbled years.
    """
    m = STATEMENT_DATE_RE.search(text)
    if m:
        day = int(m.group(1))
        month_name = m.group(2).lower()[:3]
        month = MONTH_MAP.get(month_name)
        year_str = m.group(3)
        year = int(year_str)
        # Handle OCR producing extra digits (e.g. "22013" for "2013")
        if len(year_str) == 5:
            # Try last 4 digits first (e.g. "22013" -> "2013")
            year = int(year_str[1:])
        if month:
            # Validate against filename year if available
            if filename_year and abs(year - filename_year) > 1:
                year = filename_year
            # Final sanity check
            if not (1990 <= year <= 2040):
                return None
            return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def should_skip_line(line: str) -> bool:
    """Check if line is a header/footer/boilerplate to skip."""
    for pat in SKIP_PATTERNS:
        if pat.search(line):
            return True
    return False


def is_continuation_line(line: str) -> bool:
    """Check if line is a continuation of previous transaction detail."""
    stripped = line.strip()
    if not stripped:
        return False
    # Lines that start with spaces and contain no date pattern at the start
    if line.startswith("   ") and not TXN_LINE_RE.match(line):
        # Could be extra flight details, FX info, etc.
        for pat in CONTINUATION_PATTERNS:
            if pat.search(line):
                return True
        # Generic continuation: indented text that's not a new transaction
        # and doesn't match skip patterns
        if not should_skip_line(line) and len(stripped) > 2:
            return True
    return False


def detect_account_ref(text: str) -> str:
    """Detect account_ref from card number prefix in statement text."""
    for prefix, ref in CARD_PREFIX_MAP.items():
        if prefix in text:
            return ref
    return 'fd_8897'  # fallback


def parse_pdf(filepath: str, account_ref_override: str = "") -> tuple[list[Transaction], str]:
    """Extract transactions from a single PDF statement.

    Returns (transactions, account_ref).
    """
    result = subprocess.run(
        ["pdftotext", "-layout", filepath, "-"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        print(f"  ERROR: pdftotext failed for {filepath}: {result.stderr}")
        return [], ""

    text = result.stdout

    # Extract year from filename (e.g. "20130412 First Direct..." -> 2013)
    fname = Path(filepath).name
    fname_year_match = re.match(r"(\d{4})", fname)
    filename_year = int(fname_year_match.group(1)) if fname_year_match else None

    statement_date = extract_statement_date(text, filename_year) or ""
    stmt_year_hint = int(statement_date[:4]) if len(statement_date) >= 4 else filename_year
    account_ref = account_ref_override or detect_account_ref(text)
    is_joint = (account_ref == 'fd_8178')

    transactions: list[Transaction] = []
    in_transaction_section = False
    in_second_cardholder = False
    past_summary = False  # Past the Account Summary header area

    lines = text.split("\n")

    for i, line in enumerate(lines):
        # Detect second cardholder section
        # For Joint Visa: include second cardholder transactions
        # For Sole Visa: skip second cardholder (if present)
        if re.search(r"Ms\s+Heather|Rutherford", line, re.IGNORECASE):
            in_second_cardholder = True
            continue

        # Detect start of transaction section
        if re.search(r"Your\s*Transaction\s+Details", line, re.IGNORECASE):
            in_transaction_section = True
            continue

        # Also detect Received By Us header as start of transaction section
        if re.search(r"Received\s*By\s*Us", line, re.IGNORECASE):
            in_transaction_section = True
            continue

        # Past the statement date header = past the summary section
        if re.search(r"Statement\s+Date", line, re.IGNORECASE):
            past_summary = True

        # Detect end of transaction section
        if re.search(r"Summary\s*Of\s*Interest", line, re.IGNORECASE):
            in_transaction_section = False
            in_second_cardholder = False
            continue

        # If not in a detected section, try to auto-detect by matching transaction lines
        if not in_transaction_section and past_summary:
            # Check if this line looks like a transaction (has two dates + amount)
            if TXN_LINE_RE.match(line) and AMOUNT_RE.search(line):
                in_transaction_section = True
                # Fall through to parse this line

        if not in_transaction_section:
            continue

        # Skip second cardholder section only for Sole Visa
        if in_second_cardholder and not is_joint:
            continue

        if should_skip_line(line):
            continue

        # Try to match a transaction line
        txn_match = TXN_LINE_RE.match(line)
        if txn_match:
            received_str = txn_match.group(1).strip()
            trans_str = txn_match.group(2).strip()
            rest = txn_match.group(3).strip()

            # Extract amount from end of rest
            amount_match = AMOUNT_RE.search(rest)
            if amount_match:
                raw_amount = amount_match.group(1)
                # Handle comma as decimal separator (OCR artifact: "806,21")
                # If last separator before final 2 digits is comma, treat as decimal
                if raw_amount[-3] == ',':
                    amount_str = raw_amount[:-3].replace(",", "").replace(".", "") + "." + raw_amount[-2:]
                else:
                    amount_str = raw_amount.replace(",", "")
                is_credit = amount_match.group(2) is not None
                description = rest[:amount_match.start()].strip()

                try:
                    received_date = parse_short_date(received_str, stmt_year_hint)
                    transaction_date = parse_short_date(trans_str, stmt_year_hint)
                except ValueError as e:
                    print(f"  WARNING: {e} in {filepath}")
                    continue

                amount = Decimal(amount_str)
                if not is_credit:
                    amount = -amount  # Debits are negative (purchases)

                txn = Transaction(
                    received_date=received_date,
                    transaction_date=transaction_date,
                    description=description,
                    amount=amount,
                    is_credit=is_credit,
                    statement_date=statement_date,
                )
                transactions.append(txn)
            else:
                # Line with dates but no amount - might be a weird format
                # Check if it's a continuation
                pass
        elif is_continuation_line(line) and transactions:
            # Append extra detail to last transaction
            transactions[-1].extra_details.append(line.strip())

    return transactions, account_ref


def make_transaction_ref(txn: Transaction, position: int = 0) -> str:
    """Generate a stable, unique reference for a transaction.

    Includes received_date and positional index to disambiguate
    legitimate same-day same-amount same-merchant transactions
    (e.g. two pints at the pub, multiple TFL journeys).
    """
    key = (
        f"{txn.transaction_date}|{txn.received_date}|"
        f"{txn.amount}|{txn.description}|{position}"
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def write_csv(transactions: list[Transaction], output_path: str):
    """Write transactions to CSV in First Direct format B style."""
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Description", "Amount", "Reference"])

        for txn in transactions:
            # Format date as DD/MM/YYYY for consistency with FD CSV format
            dt = datetime.strptime(txn.transaction_date, "%Y-%m-%d")
            date_str = dt.strftime("%d/%m/%Y")

            writer.writerow([
                date_str,
                txn.description,
                str(txn.amount),
                make_transaction_ref(txn, getattr(txn, '_position', 0)),
            ])


def load_to_db(transactions: list[Transaction], account_ref: str = 'fd_8897'):
    """Load transactions directly into raw_transaction."""
    import psycopg2
    from config.settings import settings

    conn = psycopg2.connect(settings.dsn)
    cur = conn.cursor()
    inserted = 0
    skipped = 0

    for txn in transactions:
        ref = make_transaction_ref(txn, getattr(txn, '_position', 0))
        raw_data = {
            "received_date": txn.received_date,
            "transaction_date": txn.transaction_date,
            "description": txn.description,
            "amount": str(txn.amount),
            "is_credit": txn.is_credit,
            "statement_date": txn.statement_date,
        }
        if txn.extra_details:
            raw_data["extra_details"] = txn.extra_details

        cur.execute("""
            INSERT INTO raw_transaction (
                source, institution, account_ref, transaction_ref,
                posted_at, amount, currency,
                raw_merchant, raw_memo, is_dirty, raw_data
            ) VALUES (
                'first_direct_pdf', 'first_direct', %s, %s,
                %s, %s, 'GBP',
                %s, %s, false, %s
            )
            ON CONFLICT (institution, account_ref, transaction_ref)
                WHERE transaction_ref IS NOT NULL
            DO NOTHING
            RETURNING id
        """, (
            account_ref,
            ref,
            txn.transaction_date,
            txn.amount,
            txn.description,
            "; ".join(txn.extra_details) if txn.extra_details else None,
            json.dumps(raw_data),
        ))

        result = cur.fetchone()
        if result:
            inserted += 1
        else:
            skipped += 1

    conn.commit()
    conn.close()
    return inserted, skipped


def main():
    parser = argparse.ArgumentParser(
        description="Parse First Direct Visa PDF statements to CSV"
    )
    parser.add_argument(
        "files", nargs="+",
        help="Path(s) to PDF files (supports glob patterns)"
    )
    parser.add_argument(
        "-o", "--output",
        default="visa_statements.csv",
        help="Output CSV path (default: visa_statements.csv)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse and report only, don't write files"
    )
    parser.add_argument(
        "--load", action="store_true",
        help="Load directly into database instead of CSV"
    )
    parser.add_argument(
        "--account-ref",
        help="Override account_ref (default: auto-detect from card number)"
    )
    args = parser.parse_args()

    # Expand globs
    pdf_files = []
    for pattern in args.files:
        expanded = sorted(glob(pattern))
        if not expanded:
            print(f"WARNING: No files match {pattern}")
        pdf_files.extend(expanded)

    if not pdf_files:
        print("ERROR: No PDF files found")
        sys.exit(1)

    print(f"=== First Direct Visa PDF Parser ===\n")
    print(f"  Found {len(pdf_files)} PDF files\n")

    all_transactions: list[Transaction] = []
    account_refs_seen: set[str] = set()

    for filepath in pdf_files:
        txns, acct_ref = parse_pdf(filepath, args.account_ref or "")
        account_refs_seen.add(acct_ref)
        fname = Path(filepath).name
        if txns:
            dates = [t.transaction_date for t in txns]
            amounts = sum(t.amount for t in txns)
            credits = [t for t in txns if t.is_credit]
            debits = [t for t in txns if not t.is_credit]
            print(f"  {fname}: {len(txns)} txns "
                  f"({len(debits)} debits, {len(credits)} credits) "
                  f"[{min(dates)} to {max(dates)}] acct={acct_ref}")
        else:
            print(f"  {fname}: 0 txns")
        all_transactions.extend(txns)

    # Sort by received date (statement ordering) then transaction date
    all_transactions.sort(key=lambda t: (t.received_date, t.transaction_date))

    # Assign positional index within each (txn_date, received_date, amount, description)
    # group to disambiguate legitimate same-day same-amount same-merchant transactions
    from collections import Counter
    position_counter: Counter = Counter()
    for txn in all_transactions:
        key = (txn.transaction_date, txn.received_date, str(txn.amount), txn.description)
        txn._position = position_counter[key]
        position_counter[key] += 1

    print(f"\n  Total: {len(all_transactions)} transactions")

    if all_transactions:
        dates = [t.transaction_date for t in all_transactions]
        total_debits = sum(t.amount for t in all_transactions if not t.is_credit)
        total_credits = sum(t.amount for t in all_transactions if t.is_credit)
        print(f"  Date range: {min(dates)} to {max(dates)}")
        print(f"  Total debits: £{total_debits:,.2f}")
        print(f"  Total credits: £{total_credits:,.2f} (payments)")

    if args.dry_run:
        print(f"\n  [DRY RUN] No output written.")
        return

    if args.load:
        # Group by account_ref for loading
        acct_ref = args.account_ref or (account_refs_seen.pop() if len(account_refs_seen) == 1 else 'fd_8897')
        inserted, skipped = load_to_db(all_transactions, acct_ref)
        print(f"\n  Database ({acct_ref}): {inserted} inserted, {skipped} skipped (duplicates)")
    else:
        output = args.output
        write_csv(all_transactions, output)
        print(f"\n  Written to {output}")
        print(f"  {len(all_transactions)} rows")


if __name__ == "__main__":
    main()
