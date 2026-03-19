"""Reusable query builders for the finance database.

This module extracts the canonical query patterns that are shared across
API routers. A separate project (e.g. an MCP server) can import these
to query the same data without duplicating the join logic.

All functions accept a psycopg2 cursor and return result rows.
None of them commit or modify data.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID


# ---------------------------------------------------------------------------
# The canonical merchant/category resolution JOIN chain.
#
# This is the single most important query pattern in the system. It resolves
# a transaction's effective merchant and category through the override chain:
#
#   Category:  transaction_category_override > override_merchant.category_hint
#              > default_merchant.category_hint
#   Merchant:  transaction_merchant_override > cleaning chain default
#
# Used by: transaction list, account detail, spending reports, search.
# ---------------------------------------------------------------------------

TRANSACTION_SELECT = """\
    rt.id, rt.source, rt.institution, rt.account_ref,
    rt.posted_at, rt.amount, rt.currency,
    rt.raw_merchant, rt.raw_memo,
    ct.cleaned_merchant,
    COALESCE(cm_override.id, cm.id) AS canonical_merchant_id,
    COALESCE(cm_override.display_name, cm_override.name,
             cm.display_name, cm.name) AS canonical_merchant_name,
    mrm.match_type AS merchant_match_type,
    COALESCE(tcat.full_path, cat_override.full_path,
             cat.full_path) AS category_path,
    COALESCE(tcat.name, cat_override.name, cat.name) AS category_name,
    COALESCE(tcat.category_type, cat_override.category_type,
             cat.category_type) AS category_type,
    (tco.raw_transaction_id IS NOT NULL) AS category_is_override,
    (tmo.raw_transaction_id IS NOT NULL) AS merchant_is_override"""

TRANSACTION_JOINS = """\
    LEFT JOIN cleaned_transaction ct ON ct.raw_transaction_id = rt.id
    LEFT JOIN merchant_raw_mapping mrm ON mrm.cleaned_merchant = ct.cleaned_merchant
    LEFT JOIN canonical_merchant cm ON cm.id = mrm.canonical_merchant_id
    LEFT JOIN transaction_merchant_override tmo ON tmo.raw_transaction_id = rt.id
    LEFT JOIN canonical_merchant cm_override ON cm_override.id = tmo.canonical_merchant_id
    LEFT JOIN category cat ON cat.full_path = cm.category_hint
    LEFT JOIN category cat_override ON cat_override.full_path = cm_override.category_hint
    LEFT JOIN transaction_category_override tco ON tco.raw_transaction_id = rt.id
    LEFT JOIN category tcat ON tcat.full_path = tco.category_path"""


def transaction_columns() -> list[str]:
    """Return the column names produced by TRANSACTION_SELECT.

    Useful for zipping with fetchall() results.
    """
    return [
        "id", "source", "institution", "account_ref",
        "posted_at", "amount", "currency",
        "raw_merchant", "raw_memo",
        "cleaned_merchant",
        "canonical_merchant_id", "canonical_merchant_name",
        "merchant_match_type",
        "category_path", "category_name", "category_type",
        "category_is_override", "merchant_is_override",
    ]


# ---------------------------------------------------------------------------
# Ready-to-use query functions
# ---------------------------------------------------------------------------


def get_transaction_detail(cur, transaction_id: UUID) -> dict | None:
    """Fetch a single transaction with full merchant/category resolution.

    Returns a dict keyed by column name, or None if not found.
    Queries raw_transaction (not the view) so dedup-suppressed rows are
    also accessible — useful for detail pages.
    """
    cur.execute(f"""
        SELECT
            {TRANSACTION_SELECT},
            rt.raw_data,
            tn.note,
            tn.source AS note_source,
            EXISTS (SELECT 1 FROM transaction_split_line sl
                    WHERE sl.raw_transaction_id = rt.id) AS is_split
        FROM raw_transaction rt
        {TRANSACTION_JOINS}
        LEFT JOIN transaction_note tn ON tn.raw_transaction_id = rt.id
        WHERE rt.id = %s
    """, (str(transaction_id),))
    row = cur.fetchone()
    if not row:
        return None
    columns = [desc[0] for desc in cur.description]
    return dict(zip(columns, row))


def get_dedup_group(cur, transaction_id: UUID) -> dict | None:
    """Fetch the dedup group for a transaction, if any.

    Returns {"group_id", "match_rule", "confidence", "members": [...]},
    or None if the transaction is not in a dedup group.
    """
    cur.execute("""
        SELECT dg.id, dg.match_rule, dg.confidence,
               dgm2.raw_transaction_id, rt2.source, dgm2.is_preferred
        FROM dedup_group_member dgm
        JOIN dedup_group dg ON dg.id = dgm.dedup_group_id
        JOIN dedup_group_member dgm2 ON dgm2.dedup_group_id = dg.id
        JOIN raw_transaction rt2 ON rt2.id = dgm2.raw_transaction_id
        WHERE dgm.raw_transaction_id = %s
    """, (str(transaction_id),))
    rows = cur.fetchall()
    if not rows:
        return None
    return {
        "group_id": rows[0][0],
        "match_rule": rows[0][1],
        "confidence": rows[0][2],
        "members": [
            {
                "raw_transaction_id": r[3],
                "source": r[4],
                "is_preferred": r[5],
            }
            for r in rows
        ],
    }


def get_economic_event(cur, transaction_id: UUID) -> dict | None:
    """Fetch the economic event (transfer/FX) linked to a transaction.

    Returns {"event_id", "event_type", "initiated_at", "description",
    "legs": [...]}, or None.
    """
    cur.execute("""
        SELECT ee.id, ee.event_type, ee.initiated_at, ee.description,
               eel2.raw_transaction_id, eel2.leg_type, eel2.amount, eel2.currency
        FROM economic_event_leg eel
        JOIN economic_event ee ON ee.id = eel.economic_event_id
        JOIN economic_event_leg eel2 ON eel2.economic_event_id = ee.id
        WHERE eel.raw_transaction_id = %s
    """, (str(transaction_id),))
    rows = cur.fetchall()
    if not rows:
        return None
    return {
        "event_id": rows[0][0],
        "event_type": rows[0][1],
        "initiated_at": rows[0][2],
        "description": rows[0][3],
        "legs": [
            {
                "raw_transaction_id": r[4],
                "leg_type": r[5],
                "amount": r[6],
                "currency": r[7],
            }
            for r in rows
        ],
    }


def get_split_lines(cur, transaction_id: UUID) -> list[dict]:
    """Fetch split lines for a transaction.

    Returns a list of dicts with id, line_number, amount, currency,
    category_path, category_name, description. Empty list if not split.
    """
    cur.execute("""
        SELECT sl.id, sl.line_number, sl.amount, sl.currency,
               sl.category_path, cat.name AS category_name, sl.description
        FROM transaction_split_line sl
        LEFT JOIN category cat ON cat.full_path = sl.category_path
        WHERE sl.raw_transaction_id = %s
        ORDER BY sl.line_number
    """, (str(transaction_id),))
    columns = [desc[0] for desc in cur.description]
    return [dict(zip(columns, r)) for r in cur.fetchall()]


def get_tags_for_transactions(cur, transaction_ids: list[str]) -> dict[str, list[str]]:
    """Batch-load tags for a set of transactions.

    Returns {transaction_id_str: [tag1, tag2, ...]}. Missing keys = no tags.
    """
    if not transaction_ids:
        return {}
    cur.execute("""
        SELECT raw_transaction_id, array_agg(tag ORDER BY tag)
        FROM transaction_tag
        WHERE raw_transaction_id = ANY(%s::uuid[])
        GROUP BY raw_transaction_id
    """, (transaction_ids,))
    return {str(r[0]): r[1] for r in cur.fetchall()}


def get_account_balances(
    cur,
    *,
    scope: str | None = None,
    allowed_scopes: list[str] | None = None,
    include_archived: bool = False,
) -> list[dict]:
    """Compute account balances from active transactions.

    Each row has: institution, account_ref, currency, transaction_count,
    earliest_date, latest_date, balance, plus account metadata columns.

    Args:
        scope: Filter to a specific scope, or None for all allowed.
        allowed_scopes: User's allowed scopes (used when scope is None).
        include_archived: Include archived accounts.
    """
    conditions: list[str] = []
    params: dict = {}

    if not include_archived:
        conditions.append("(a.is_archived IS NOT TRUE)")

    if scope:
        conditions.append("(a.scope = %(scope)s)")
        params["scope"] = scope
    elif allowed_scopes:
        conditions.append("(a.scope = ANY(%(allowed_scopes)s))")
        params["allowed_scopes"] = allowed_scopes

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    cur.execute(f"""
        SELECT
            a.id AS account_id,
            rt.institution,
            rt.account_ref,
            rt.currency,
            COUNT(*) AS transaction_count,
            MIN(rt.posted_at) AS earliest_date,
            MAX(rt.posted_at) AS latest_date,
            SUM(rt.amount) AS balance,
            a.name AS account_name,
            a.display_name,
            a.account_type,
            a.is_active,
            a.is_archived,
            a.exclude_from_reports,
            a.scope,
            a.display_order,
            a.is_taxable
        FROM active_transaction rt
        LEFT JOIN account a
            ON a.institution = rt.institution
            AND a.account_ref = rt.account_ref
        {where}
        GROUP BY a.id, rt.institution, rt.account_ref, rt.currency,
                 a.name, a.display_name, a.account_type, a.is_active,
                 a.is_archived, a.exclude_from_reports, a.scope,
                 a.display_order, a.is_taxable
        ORDER BY a.display_order ASC NULLS LAST, rt.institution, rt.account_ref
    """, params)
    columns = [desc[0] for desc in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def get_monthly_totals(
    cur,
    *,
    months: int = 12,
    currency: str = "GBP",
    scope: str | None = None,
    allowed_scopes: list[str] | None = None,
    institution: str | None = None,
    account_ref: str | None = None,
) -> list[dict]:
    """Monthly income/expense totals.

    Returns rows with: month (YYYY-MM), income, expense, net, transaction_count.
    Excludes transactions with system category overrides (+Transfer, +Ignore).
    """
    conditions = [
        "rt.currency = %(currency)s",
        "rt.posted_at >= (CURRENT_DATE - %(months)s * INTERVAL '1 month')",
        "(a.is_archived IS NOT TRUE)",
        # Exclude system categories
        """NOT EXISTS (
            SELECT 1 FROM transaction_category_override tco
            WHERE tco.raw_transaction_id = rt.id
              AND tco.category_path LIKE '+%%'
        )""",
    ]
    params: dict = {"currency": currency, "months": months}

    if scope:
        conditions.append("(a.scope = %(scope)s)")
        params["scope"] = scope
    elif allowed_scopes:
        conditions.append("(a.scope = ANY(%(allowed_scopes)s))")
        params["allowed_scopes"] = allowed_scopes

    if institution:
        conditions.append("rt.institution = %(institution)s")
        params["institution"] = institution
    if account_ref:
        conditions.append("rt.account_ref = %(account_ref)s")
        params["account_ref"] = account_ref

    where = " AND ".join(conditions)

    cur.execute(f"""
        SELECT
            TO_CHAR(rt.posted_at, 'YYYY-MM') AS month,
            SUM(CASE WHEN rt.amount > 0 THEN rt.amount ELSE 0 END) AS income,
            SUM(CASE WHEN rt.amount < 0 THEN rt.amount ELSE 0 END) AS expense,
            SUM(rt.amount) AS net,
            COUNT(*) AS transaction_count
        FROM active_transaction rt
        LEFT JOIN account a
            ON a.institution = rt.institution
            AND a.account_ref = rt.account_ref
        WHERE {where}
        GROUP BY TO_CHAR(rt.posted_at, 'YYYY-MM')
        ORDER BY month DESC
    """, params)
    columns = [desc[0] for desc in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def get_spending_by_category(
    cur,
    *,
    date_from: date,
    date_to: date,
    currency: str = "GBP",
    scope: str | None = None,
    allowed_scopes: list[str] | None = None,
    institution: str | None = None,
    account_ref: str | None = None,
) -> list[dict]:
    """Spending aggregated by category for a date range.

    Handles split transactions: unsplit transactions use the standard category
    resolution chain, split transactions use per-line categories.

    Returns rows with: category_path, category_name, category_type, total,
    transaction_count. Excludes system categories (paths starting with '+').
    """
    conditions = [
        "rt.posted_at >= %(date_from)s",
        "rt.posted_at <= %(date_to)s",
        "rt.currency = %(currency)s",
        "(acct.exclude_from_reports IS NOT TRUE)",
    ]
    params: dict = {
        "date_from": date_from,
        "date_to": date_to,
        "currency": currency,
    }

    if scope:
        conditions.append("(acct.scope = %(scope)s)")
        params["scope"] = scope
    elif allowed_scopes:
        conditions.append("(acct.scope = ANY(%(allowed_scopes)s))")
        params["allowed_scopes"] = allowed_scopes

    if institution:
        conditions.append("rt.institution = %(institution)s")
        params["institution"] = institution
    if account_ref:
        conditions.append("rt.account_ref = %(account_ref)s")
        params["account_ref"] = account_ref

    where = " AND ".join(conditions)

    cur.execute(f"""
        WITH effective_lines AS (
            -- Unsplit transactions: standard category resolution
            SELECT rt.amount, rt.currency, rt.posted_at,
                   rt.institution, rt.account_ref,
                   COALESCE(tcat.full_path, cat_override.full_path,
                            cat.full_path) AS category_path,
                   COALESCE(tcat.name, cat_override.name,
                            cat.name) AS category_name,
                   COALESCE(tcat.category_type, cat_override.category_type,
                            cat.category_type) AS category_type
            FROM active_transaction rt
            LEFT JOIN account acct
                ON acct.institution = rt.institution
                AND acct.account_ref = rt.account_ref
            {TRANSACTION_JOINS}
            WHERE NOT EXISTS (
                SELECT 1 FROM transaction_split_line sl
                WHERE sl.raw_transaction_id = rt.id
            )
            AND {where}

            UNION ALL

            -- Split transactions: per-line amount and category
            SELECT sl.amount, sl.currency, rt.posted_at,
                   rt.institution, rt.account_ref,
                   sl.category_path,
                   scat.name AS category_name,
                   scat.category_type
            FROM active_transaction rt
            LEFT JOIN account acct
                ON acct.institution = rt.institution
                AND acct.account_ref = rt.account_ref
            JOIN transaction_split_line sl ON sl.raw_transaction_id = rt.id
            LEFT JOIN category scat ON scat.full_path = sl.category_path
            WHERE {where}
        )
        SELECT
            COALESCE(el.category_path, 'Uncategorised') AS category_path,
            COALESCE(el.category_name, 'Uncategorised') AS category_name,
            el.category_type,
            SUM(el.amount) AS total,
            COUNT(*) AS transaction_count
        FROM effective_lines el
        WHERE COALESCE(el.category_path, 'Uncategorised') NOT LIKE '+%%'
        GROUP BY COALESCE(el.category_path, 'Uncategorised'),
                 COALESCE(el.category_name, 'Uncategorised'),
                 el.category_type
        ORDER BY total ASC
    """, params)
    columns = [desc[0] for desc in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def get_overview_stats(cur) -> dict:
    """Dashboard overview statistics.

    Returns dict with: total_accounts, active_accounts, total_raw_transactions,
    active_transactions, dedup_groups, removed_by_dedup, category_coverage_pct,
    date_range_from, date_range_to.
    """
    cur.execute("SELECT COUNT(*) FROM account")
    total_accounts = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM account WHERE is_active")
    active_accounts = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM raw_transaction")
    total_raw = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM active_transaction")
    total_active = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM dedup_group")
    dedup_groups = cur.fetchone()[0]

    # Category coverage: active transactions that have a category
    cur.execute("""
        SELECT COUNT(*) FROM active_transaction rt
        JOIN cleaned_transaction ct ON ct.raw_transaction_id = rt.id
        JOIN merchant_raw_mapping mrm ON mrm.cleaned_merchant = ct.cleaned_merchant
        JOIN canonical_merchant cm ON cm.id = mrm.canonical_merchant_id
        WHERE cm.category_hint IS NOT NULL
    """)
    categorised = cur.fetchone()[0]
    coverage = (
        Decimal(categorised * 100) / Decimal(total_active)
        if total_active > 0
        else Decimal(0)
    )

    cur.execute("SELECT MIN(posted_at), MAX(posted_at) FROM active_transaction")
    date_row = cur.fetchone()

    return {
        "total_accounts": total_accounts,
        "active_accounts": active_accounts,
        "total_raw_transactions": total_raw,
        "active_transactions": total_active,
        "dedup_groups": dedup_groups,
        "removed_by_dedup": total_raw - total_active,
        "category_coverage_pct": round(coverage, 1),
        "date_range_from": date_row[0] if date_row else None,
        "date_range_to": date_row[1] if date_row else None,
    }


def get_category_tree(cur) -> list[dict]:
    """Fetch the full category tree as a flat list.

    Returns rows with: id, name, full_path, category_type, is_active,
    parent_id. Sorted by full_path for deterministic tree construction.
    """
    cur.execute("""
        SELECT id, name, full_path, category_type, is_active, parent_id
        FROM category
        ORDER BY full_path
    """)
    columns = [desc[0] for desc in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def get_merchant_for_transaction(cur, transaction_id: UUID) -> dict | None:
    """Resolve the effective canonical merchant for a single transaction.

    Returns {"id", "name", "display_name", "category_hint"} or None.
    Respects merchant overrides.
    """
    cur.execute("""
        SELECT
            COALESCE(cm_override.id, cm.id) AS id,
            COALESCE(cm_override.name, cm.name) AS name,
            COALESCE(cm_override.display_name, cm_override.name,
                     cm.display_name, cm.name) AS display_name,
            COALESCE(cm_override.category_hint, cm.category_hint) AS category_hint
        FROM raw_transaction rt
        LEFT JOIN cleaned_transaction ct ON ct.raw_transaction_id = rt.id
        LEFT JOIN merchant_raw_mapping mrm ON mrm.cleaned_merchant = ct.cleaned_merchant
        LEFT JOIN canonical_merchant cm ON cm.id = mrm.canonical_merchant_id
        LEFT JOIN transaction_merchant_override tmo ON tmo.raw_transaction_id = rt.id
        LEFT JOIN canonical_merchant cm_override ON cm_override.id = tmo.canonical_merchant_id
        WHERE rt.id = %s
    """, (str(transaction_id),))
    row = cur.fetchone()
    if not row or row[0] is None:
        return None
    columns = [desc[0] for desc in cur.description]
    return dict(zip(columns, row))
