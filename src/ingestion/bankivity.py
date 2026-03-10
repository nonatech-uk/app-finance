"""Bankivity .bank8 import logic for API use."""

from pathlib import Path

# fd_bankivity_load.py lives in scripts/ which is on sys.path via the script itself,
# but we need to import it as a module from the API. Add scripts/ to path if needed.
import sys

_scripts_dir = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from fd_bankivity_load import extract_transactions, write_transactions


def _resolve_db_path(bank8_path: str) -> Path:
    """Resolve .bank8 path to inner core.sql, raising ValueError if not found."""
    p = Path(bank8_path)
    if not p.exists():
        raise ValueError(f"Path does not exist: {bank8_path}")
    db_path = p / "StoreContent" / "core.sql"
    if not db_path.exists():
        raise ValueError(
            f"Database not found at {db_path}. "
            "Check the .bank8 path is correct."
        )
    return db_path


def _preview(db_path: str, conn) -> dict:
    """Extract transactions from SQLite and compare against DB."""
    txns = extract_transactions(db_path)

    refs = [t["transaction_ref"] for t in txns]
    if refs:
        cur = conn.cursor()
        cur.execute("""
            SELECT transaction_ref
            FROM raw_transaction
            WHERE institution = 'first_direct'
              AND source = 'first_direct_bankivity'
              AND transaction_ref = ANY(%s)
        """, (refs,))
        existing_refs = {row[0] for row in cur.fetchall()}
    else:
        existing_refs = set()

    new_txns = [t for t in txns if t["transaction_ref"] not in existing_refs]

    by_account: dict[str, int] = {}
    for t in new_txns:
        by_account[t["account_ref"]] = by_account.get(t["account_ref"], 0) + 1

    return {
        "total": len(txns),
        "new_count": len(new_txns),
        "existing_count": len(existing_refs),
        "by_account": by_account,
        "path": db_path,
    }


def preview_bankivity(bank8_path: str, conn) -> dict:
    """Validate .bank8 directory path, extract and preview transactions."""
    db_path = _resolve_db_path(bank8_path)
    return _preview(str(db_path), conn)


def preview_bankivity_file(sql_path: str, conn) -> dict:
    """Preview transactions from an uploaded core.sql file."""
    if not Path(sql_path).exists():
        raise ValueError(f"File does not exist: {sql_path}")
    return _preview(sql_path, conn)


def execute_bankivity(bank8_path: str, conn) -> dict:
    """Extract and write transactions from .bank8 directory."""
    db_path = _resolve_db_path(bank8_path)
    txns = extract_transactions(str(db_path))
    return write_transactions(txns, conn)


def execute_bankivity_file(sql_path: str, conn) -> dict:
    """Extract and write transactions from an uploaded core.sql file."""
    if not Path(sql_path).exists():
        raise ValueError(f"File does not exist: {sql_path}")
    txns = extract_transactions(sql_path)
    return write_transactions(txns, conn)
