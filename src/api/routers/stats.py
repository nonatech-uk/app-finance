"""Statistics endpoints."""

from decimal import Decimal

from fastapi import APIRouter, Depends, Query

from src.api.deps import CurrentUser, get_conn, get_current_user, scope_condition, validate_scope
from src.api.models import AccountOption, MonthlyReport, MonthlyTotal, OverviewStats

router = APIRouter()


@router.get("/stats/monthly", response_model=MonthlyReport)
def monthly_totals(
    months: int = Query(12, ge=1, le=120, description="Number of months"),
    institution: str | None = None,
    account_ref: str | None = None,
    currency: str = Query("GBP", description="Currency"),
    scope: str | None = Query("personal", description="Scope filter (personal/business/all)"),
    conn=Depends(get_conn),
    user: CurrentUser = Depends(get_current_user),
):
    """Monthly income/expense totals."""
    cur = conn.cursor()

    effective_scope = validate_scope(scope, user)
    scope_cond, scope_params = scope_condition(effective_scope, user, alias="a")

    conditions = [
        "rt.currency = %(currency)s",
        "rt.posted_at >= (CURRENT_DATE - %(months)s * INTERVAL '1 month')",
        "(a.is_archived IS NOT TRUE)",
        scope_cond,
        # Exclude transactions categorised as +Ignore or +Transfer
        """NOT EXISTS (
            SELECT 1 FROM transaction_category_override tco
            WHERE tco.raw_transaction_id = rt.id
              AND tco.category_path LIKE '+%%'
        )""",
    ]
    params: dict = {"currency": currency, "months": months, **scope_params}

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
    rows = cur.fetchall()
    items = [MonthlyTotal(**dict(zip(columns, row))) for row in rows]

    return MonthlyReport(items=items, currency=currency)


@router.get("/stats/overview", response_model=OverviewStats)
def overview(
    scope: str | None = Query("personal", description="Scope filter (personal/business/all)"),
    conn=Depends(get_conn),
    user: CurrentUser = Depends(get_current_user),
):
    """Dashboard overview statistics."""
    cur = conn.cursor()

    # Accounts
    cur.execute("SELECT count(*) FROM account")
    total_accounts = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM account WHERE is_active")
    active_accounts = cur.fetchone()[0]

    # Transactions
    cur.execute("SELECT count(*) FROM raw_transaction")
    total_raw = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM active_transaction")
    total_active = cur.fetchone()[0]

    # Dedup
    cur.execute("SELECT count(*) FROM dedup_group")
    dedup_groups = cur.fetchone()[0]

    # Category coverage: active transactions that have a category via the JOIN chain
    cur.execute("""
        SELECT count(*) FROM active_transaction rt
        JOIN cleaned_transaction ct ON ct.raw_transaction_id = rt.id
        JOIN merchant_raw_mapping mrm ON mrm.cleaned_merchant = ct.cleaned_merchant
        JOIN canonical_merchant cm ON cm.id = mrm.canonical_merchant_id
        WHERE cm.category_hint IS NOT NULL
    """)
    categorised = cur.fetchone()[0]
    coverage = Decimal(categorised * 100) / Decimal(total_active) if total_active > 0 else Decimal(0)

    # Active accounts for filter dropdown (scope-filtered)
    effective_scope = validate_scope(scope, user)
    ov_scope_cond, ov_scope_params = scope_condition(effective_scope, user, alias="a")
    cur.execute(f"""
        SELECT a.institution, a.account_ref,
               COALESCE(a.display_name, a.account_ref) AS label
        FROM account a
        WHERE NOT a.is_archived AND {ov_scope_cond}
        ORDER BY a.display_order ASC NULLS LAST,
               COALESCE(a.display_name, a.account_ref)
    """, ov_scope_params)
    accounts = [
        AccountOption(institution=r[0], account_ref=r[1], label=r[2])
        for r in cur.fetchall()
    ]

    # Date range
    cur.execute("SELECT min(posted_at), max(posted_at) FROM active_transaction")
    date_row = cur.fetchone()

    return OverviewStats(
        total_accounts=total_accounts,
        active_accounts=active_accounts,
        total_raw_transactions=total_raw,
        active_transactions=total_active,
        dedup_groups=dedup_groups,
        removed_by_dedup=total_raw - total_active,
        category_coverage_pct=round(coverage, 1),
        accounts=accounts,
        date_range_from=date_row[0] if date_row else None,
        date_range_to=date_row[1] if date_row else None,
    )
