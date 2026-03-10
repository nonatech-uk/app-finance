"""Merchant endpoints."""

import csv
import io
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from src.api.deps import CurrentUser, get_conn, get_current_user, require_admin, scope_condition, validate_scope
from src.api.models import (
    AliasSplitRequest,
    BulkMerchantMerge,
    CategorySuggestionItem,
    CategorySuggestionList,
    DisplayRuleCreate,
    DisplayRuleItem,
    DisplayRuleList,
    MerchantDetail,
    MerchantItem,
    MerchantList,
    MerchantMappingUpdate,
    MerchantMergeRequest,
    MerchantNameUpdate,
    MerchantTransaction,
    SplitRuleCreate,
    SplitRuleItem,
    SplitRuleList,
    SuggestionReview,
)

router = APIRouter()


@router.get("/merchants", response_model=MerchantList)
def list_merchants(
    search: str | None = Query(None, description="Search merchant name"),
    search_aliases: bool = Query(False, description="Also search in raw merchant aliases"),
    unmapped: bool = Query(False, description="Only show merchants without category"),
    has_suggestions: bool = Query(False, description="Only show merchants with pending suggestions"),
    last_used_after: date | None = Query(None, description="Only merchants with transactions on or after this date"),
    last_used_before: date | None = Query(None, description="Only merchants with transactions on or before this date"),
    cursor: str | None = Query(None, description="Cursor for pagination (merchant name)"),
    offset: int = Query(0, ge=0, description="Offset for non-name sorts"),
    sort_by: str = Query("name", description="Sort column: name, category, confidence, mappings"),
    sort_dir: str = Query("asc", description="Sort direction: asc or desc"),
    limit: int = Query(50, ge=1, le=200),
    scope: str | None = Query("personal", description="Scope filter (personal/business/all)"),
    conn=Depends(get_conn),
    user: CurrentUser = Depends(get_current_user),
):
    """List canonical merchants with mapping counts."""
    VALID_SORT_COLUMNS = {
        "name": "COALESCE(cm.display_name, cm.name)",
        "category": "cm.category_hint",
        "confidence": "cm.category_confidence",
        "mappings": "COUNT(mrm.cleaned_merchant)",
        "last_used": "MAX(at.posted_at)",
    }
    if sort_by not in VALID_SORT_COLUMNS:
        raise HTTPException(400, f"Invalid sort_by: {sort_by}")
    if sort_dir not in ("asc", "desc"):
        raise HTTPException(400, "sort_dir must be 'asc' or 'desc'")

    cur = conn.cursor()

    # Build the active-transaction existence check, optionally with date bounds
    date_filters = []
    if last_used_after:
        date_filters.append("AND at2.posted_at >= %(last_used_after)s")
    if last_used_before:
        date_filters.append("AND at2.posted_at <= %(last_used_before)s")
    date_clause = " ".join(date_filters)

    effective_scope = validate_scope(scope, user)
    scope_cond, scope_params = scope_condition(effective_scope, user, alias="acct")

    conditions = [
        "cm.merged_into_id IS NULL",
        f"""EXISTS (
            SELECT 1 FROM merchant_raw_mapping mrm2
            JOIN cleaned_transaction ct2 ON ct2.cleaned_merchant = mrm2.cleaned_merchant
            JOIN active_transaction at2 ON at2.id = ct2.raw_transaction_id
            JOIN account acct ON acct.institution = at2.institution AND acct.account_ref = at2.account_ref
            WHERE mrm2.canonical_merchant_id = cm.id
              AND {scope_cond}
              {date_clause}
        )""",
    ]
    params: dict = {"limit": limit + 1, **scope_params}
    if last_used_after:
        params["last_used_after"] = last_used_after
    if last_used_before:
        params["last_used_before"] = last_used_before

    if search:
        if search_aliases:
            conditions.append("""(cm.name ILIKE %(search)s OR cm.display_name ILIKE %(search)s
                OR EXISTS (SELECT 1 FROM merchant_raw_mapping mrm3
                           WHERE mrm3.canonical_merchant_id = cm.id
                             AND mrm3.cleaned_merchant ILIKE %(search)s))""")
        else:
            conditions.append("(cm.name ILIKE %(search)s OR cm.display_name ILIKE %(search)s)")
        params["search"] = f"%{search}%"
    if unmapped:
        conditions.append("cm.category_hint IS NULL")
    if has_suggestions:
        conditions.append("""
            EXISTS (SELECT 1 FROM category_suggestion cs
                    WHERE cs.canonical_merchant_id = cm.id AND cs.status = 'pending')
        """)

    # Sorting & pagination
    direction = sort_dir.upper()
    sort_col_sql = VALID_SORT_COLUMNS[sort_by]

    if sort_by == "name":
        # Keyset pagination on display name (falling back to raw name)
        if cursor:
            op = ">" if sort_dir == "asc" else "<"
            conditions.append(f"COALESCE(cm.display_name, cm.name) {op} %(cursor)s")
            params["cursor"] = cursor
        order_clause = f"ORDER BY COALESCE(cm.display_name, cm.name) {direction}"
        pagination_clause = "LIMIT %(limit)s"
    else:
        # Offset pagination for other columns
        nulls = "NULLS LAST" if sort_by in ("category", "confidence", "last_used") else ""
        order_clause = f"ORDER BY {sort_col_sql} {direction} {nulls}, cm.name ASC"
        pagination_clause = "LIMIT %(limit)s OFFSET %(offset)s"
        params["offset"] = offset

    where = "WHERE " + " AND ".join(conditions)

    cur.execute(f"""
        SELECT
            cm.id,
            cm.name,
            cm.display_name,
            cm.category_hint,
            cm.category_method,
            cm.category_confidence,
            COUNT(DISTINCT mrm.cleaned_merchant) AS mapping_count,
            MAX(at.posted_at) AS last_transaction_date
        FROM canonical_merchant cm
        LEFT JOIN merchant_raw_mapping mrm ON mrm.canonical_merchant_id = cm.id
        LEFT JOIN cleaned_transaction ct ON ct.cleaned_merchant = mrm.cleaned_merchant
        LEFT JOIN active_transaction at ON at.id = ct.raw_transaction_id
        {where}
        GROUP BY cm.id, cm.name, cm.display_name, cm.category_hint,
                 cm.category_method, cm.category_confidence
        {order_clause}
        {pagination_clause}
    """, params)

    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()

    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    items = [MerchantItem(**dict(zip(columns, row))) for row in rows]

    next_cursor = None
    if has_more and items and sort_by == "name":
        last = items[-1]
        next_cursor = last.display_name or last.name

    return MerchantList(items=items, next_cursor=next_cursor, has_more=has_more)


@router.get("/merchants/export")
def export_merchants(scope: str | None = Query("personal", description="Scope filter"), conn=Depends(get_conn), user: CurrentUser = Depends(get_current_user)):
    """Export all active merchants as CSV with display name, canonical name, aliases, category, last used date."""
    cur = conn.cursor()

    effective_scope = validate_scope(scope, user)
    exp_scope_cond, exp_scope_params = scope_condition(effective_scope, user, alias="acct")

    cur.execute(f"""
        WITH merchant_last_used AS (
            SELECT
                mrm.canonical_merchant_id,
                MAX(at2.posted_at) AS last_used
            FROM merchant_raw_mapping mrm
            JOIN cleaned_transaction ct ON ct.cleaned_merchant = mrm.cleaned_merchant
            JOIN active_transaction at2 ON at2.id = ct.raw_transaction_id
            JOIN account acct ON acct.institution = at2.institution AND acct.account_ref = at2.account_ref
            WHERE {exp_scope_cond}
            GROUP BY mrm.canonical_merchant_id
        )
        SELECT
            COALESCE(cm.display_name, cm.name) AS display_name,
            cm.name AS canonical_name,
            mrm.cleaned_merchant AS alias,
            cm.category_hint,
            mlu.last_used
        FROM canonical_merchant cm
        JOIN merchant_raw_mapping mrm ON mrm.canonical_merchant_id = cm.id
        LEFT JOIN merchant_last_used mlu ON mlu.canonical_merchant_id = cm.id
        WHERE cm.merged_into_id IS NULL
          AND mlu.last_used IS NOT NULL
        ORDER BY COALESCE(cm.display_name, cm.name), mrm.cleaned_merchant
    """, exp_scope_params)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Display Name", "Canonical Name", "Alias", "Category", "Last Used"])

    for row in cur.fetchall():
        display_name, canonical_name, alias, category, last_used = row
        writer.writerow([
            display_name or "",
            canonical_name or "",
            alias or "",
            category or "",
            str(last_used) if last_used else "",
        ])

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=merchants.csv"},
    )


@router.get("/merchants/suggestions", response_model=CategorySuggestionList)
def list_suggestions(
    status: str = Query("pending", description="Filter by status"),
    limit: int = Query(50, ge=1, le=200),
    scope: str | None = Query("personal", description="Scope filter"),
    conn=Depends(get_conn),
    user: CurrentUser = Depends(get_current_user),
):
    """List category suggestions for review."""
    cur = conn.cursor()

    effective_scope = validate_scope(scope, user)
    sug_scope_cond, sug_scope_params = scope_condition(effective_scope, user, alias="acct")

    active_merchant_filter = f"""
        EXISTS (
            SELECT 1 FROM merchant_raw_mapping mrm
            JOIN cleaned_transaction ct ON ct.cleaned_merchant = mrm.cleaned_merchant
            JOIN active_transaction at2 ON at2.id = ct.raw_transaction_id
            JOIN account acct ON acct.institution = at2.institution AND acct.account_ref = at2.account_ref
            WHERE mrm.canonical_merchant_id = cm.id
              AND {sug_scope_cond}
        )
    """

    cur.execute(f"""
        SELECT cs.id, cs.canonical_merchant_id, cm.name as merchant_name,
               cs.suggested_category_id, cat.full_path as suggested_category_path,
               cs.method, cs.confidence, cs.reasoning, cs.status, cs.created_at
        FROM category_suggestion cs
        JOIN canonical_merchant cm ON cm.id = cs.canonical_merchant_id
        JOIN category cat ON cat.id = cs.suggested_category_id
        WHERE cs.status = %(status)s
          AND {active_merchant_filter}
        ORDER BY cs.confidence DESC, cm.name
        LIMIT %(limit)s
    """, {"status": status, "limit": limit, **sug_scope_params})

    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()

    items = [CategorySuggestionItem(**dict(zip(columns, row))) for row in rows]

    # Get total count (matching the same filter)
    cur.execute(f"""
        SELECT count(*)
        FROM category_suggestion cs
        JOIN canonical_merchant cm ON cm.id = cs.canonical_merchant_id
        WHERE cs.status = %(status)s
          AND {active_merchant_filter}
    """, {"status": status, **sug_scope_params})
    total = cur.fetchone()[0]

    return CategorySuggestionList(items=items, total=total)


@router.put("/merchants/suggestions/{suggestion_id}")
def review_suggestion(
    suggestion_id: int,
    body: SuggestionReview,
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    """Accept or reject a category suggestion."""
    if body.status not in ('accepted', 'rejected'):
        raise HTTPException(400, "Status must be 'accepted' or 'rejected'")

    cur = conn.cursor()

    cur.execute("""
        SELECT cs.id, cs.canonical_merchant_id, cs.suggested_category_id, cs.confidence, cs.method
        FROM category_suggestion cs
        WHERE cs.id = %s AND cs.status = 'pending'
    """, (suggestion_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Suggestion not found or already reviewed")

    _, cm_id, cat_id, confidence, method = row

    # Update suggestion status
    cur.execute(
        "UPDATE category_suggestion SET status = %s, reviewed_at = now() WHERE id = %s",
        (body.status, suggestion_id),
    )

    # If accepted, apply the category to the merchant
    if body.status == 'accepted':
        if method == 'fuzzy_merge':
            # Extract merge target ID from reasoning field
            import re as _re
            cur.execute("SELECT reasoning FROM category_suggestion WHERE id = %s", (suggestion_id,))
            reasoning = cur.fetchone()[0] or ''
            match = _re.search(r'merge_target:([0-9a-f-]+)', reasoning)
            if match:
                from src.categorisation.merger import merge
                target_id = match.group(1)
                try:
                    merge(conn, secondary_id=str(cm_id), surviving_id=target_id)
                except ValueError as e:
                    raise HTTPException(400, f"Merge failed: {e}")
            else:
                # Fallback: just set category like normal
                cur.execute("""
                    UPDATE canonical_merchant
                    SET category_hint = (SELECT full_path FROM category WHERE id = %s),
                        category_method = %s,
                        category_confidence = %s,
                        category_set_at = now()
                    WHERE id = %s
                """, (str(cat_id), method, float(confidence), str(cm_id)))
        else:
            cur.execute("""
                UPDATE canonical_merchant
                SET category_hint = (SELECT full_path FROM category WHERE id = %s),
                    category_method = %s,
                    category_confidence = %s,
                    category_set_at = now()
                WHERE id = %s
            """, (str(cat_id), method, float(confidence), str(cm_id)))

    conn.commit()

    return {"id": suggestion_id, "status": body.status, "applied": body.status == 'accepted'}


# ── Display Rules ────────────────────────────────────────────────────────────
# These must be defined BEFORE /merchants/{merchant_id} to avoid
# FastAPI matching "rules" as a UUID parameter.


@router.get("/merchants/rules", response_model=DisplayRuleList)
def list_rules(conn=Depends(get_conn), user: CurrentUser = Depends(get_current_user)):
    """List all merchant display rules."""
    cur = conn.cursor()
    cur.execute("SELECT id, pattern, display_name, merge_group, category_hint, priority FROM merchant_display_rule ORDER BY priority, id")
    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()
    items = [DisplayRuleItem(**dict(zip(columns, row))) for row in rows]
    return DisplayRuleList(items=items)


@router.post("/merchants/rules", response_model=DisplayRuleItem)
def create_rule(body: DisplayRuleCreate, conn=Depends(get_conn), user: CurrentUser = Depends(require_admin)):
    """Create a new merchant display rule."""
    import re
    try:
        re.compile(body.pattern)
    except re.error as e:
        raise HTTPException(400, f"Invalid regex: {e}")

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO merchant_display_rule (pattern, display_name, merge_group, category_hint, priority)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
    """, (body.pattern, body.display_name, body.merge_group, body.category_hint, body.priority))
    rule_id = cur.fetchone()[0]
    conn.commit()

    return DisplayRuleItem(id=rule_id, **body.model_dump())


@router.put("/merchants/rules/{rule_id}", response_model=DisplayRuleItem)
def update_rule(rule_id: int, body: DisplayRuleCreate, conn=Depends(get_conn), user: CurrentUser = Depends(require_admin)):
    """Update an existing merchant display rule."""
    import re
    try:
        re.compile(body.pattern)
    except re.error as e:
        raise HTTPException(400, f"Invalid regex: {e}")

    cur = conn.cursor()
    cur.execute("""
        UPDATE merchant_display_rule
        SET pattern = %s, display_name = %s, merge_group = %s, category_hint = %s, priority = %s
        WHERE id = %s
    """, (body.pattern, body.display_name, body.merge_group, body.category_hint, body.priority, rule_id))
    if cur.rowcount == 0:
        raise HTTPException(404, "Rule not found")
    conn.commit()

    return DisplayRuleItem(id=rule_id, **body.model_dump())


@router.delete("/merchants/rules/{rule_id}")
def delete_rule(rule_id: int, conn=Depends(get_conn), user: CurrentUser = Depends(require_admin)):
    """Delete a merchant display rule."""
    cur = conn.cursor()
    cur.execute("DELETE FROM merchant_display_rule WHERE id = %s", (rule_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "Rule not found")
    conn.commit()
    return {"id": rule_id, "deleted": True}


# ── Split Rules ──────────────────────────────────────────────────────────────
# Amount-based merchant routing: when cleaned_merchant matches a pattern AND
# amount matches, override the canonical merchant for that transaction.


@router.get("/merchants/split-rules", response_model=SplitRuleList)
def list_split_rules(conn=Depends(get_conn), user: CurrentUser = Depends(get_current_user)):
    """List all merchant split rules."""
    cur = conn.cursor()
    cur.execute("""
        SELECT sr.id, sr.merchant_pattern, sr.amount_exact, sr.amount_min, sr.amount_max,
               sr.target_merchant_id, COALESCE(cm.display_name, cm.name) AS target_merchant_name,
               sr.priority, sr.description
        FROM merchant_split_rule sr
        JOIN canonical_merchant cm ON cm.id = sr.target_merchant_id
        ORDER BY sr.priority, sr.id
    """)
    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()
    items = [SplitRuleItem(**dict(zip(columns, row))) for row in rows]
    return SplitRuleList(items=items)


@router.post("/merchants/split-rules", response_model=SplitRuleItem)
def create_split_rule(body: SplitRuleCreate, conn=Depends(get_conn), user: CurrentUser = Depends(require_admin)):
    """Create a new merchant split rule."""
    cur = conn.cursor()

    # Validate target merchant exists
    cur.execute("SELECT id, COALESCE(display_name, name) FROM canonical_merchant WHERE id = %s",
                (str(body.target_merchant_id),))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Target merchant not found")
    target_name = row[1]

    cur.execute("""
        INSERT INTO merchant_split_rule
            (merchant_pattern, amount_exact, amount_min, amount_max,
             target_merchant_id, priority, description)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (body.merchant_pattern,
          body.amount_exact, body.amount_min, body.amount_max,
          str(body.target_merchant_id), body.priority, body.description))
    rule_id = cur.fetchone()[0]
    conn.commit()

    return SplitRuleItem(
        id=rule_id,
        target_merchant_name=target_name,
        **body.model_dump(),
    )


@router.put("/merchants/split-rules/{rule_id}", response_model=SplitRuleItem)
def update_split_rule(rule_id: int, body: SplitRuleCreate, conn=Depends(get_conn), user: CurrentUser = Depends(require_admin)):
    """Update an existing merchant split rule."""
    cur = conn.cursor()

    # Validate target merchant exists
    cur.execute("SELECT id, COALESCE(display_name, name) FROM canonical_merchant WHERE id = %s",
                (str(body.target_merchant_id),))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Target merchant not found")
    target_name = row[1]

    cur.execute("""
        UPDATE merchant_split_rule
        SET merchant_pattern = %s, amount_exact = %s, amount_min = %s, amount_max = %s,
            target_merchant_id = %s, priority = %s, description = %s
        WHERE id = %s
    """, (body.merchant_pattern,
          body.amount_exact, body.amount_min, body.amount_max,
          str(body.target_merchant_id), body.priority, body.description,
          rule_id))
    if cur.rowcount == 0:
        raise HTTPException(404, "Rule not found")
    conn.commit()

    return SplitRuleItem(
        id=rule_id,
        target_merchant_name=target_name,
        **body.model_dump(),
    )


@router.delete("/merchants/split-rules/{rule_id}")
def delete_split_rule(rule_id: int, conn=Depends(get_conn), user: CurrentUser = Depends(require_admin)):
    """Delete a merchant split rule and its generated overrides."""
    cur = conn.cursor()

    # Remove overrides created by this rule
    cur.execute("DELETE FROM transaction_merchant_override WHERE split_rule_id = %s", (rule_id,))
    overrides_removed = cur.rowcount

    cur.execute("DELETE FROM merchant_split_rule WHERE id = %s", (rule_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "Rule not found")
    conn.commit()

    return {"id": rule_id, "deleted": True, "overrides_removed": overrides_removed}


def run_apply_split_rules(conn) -> dict:
    """Apply all split rules, creating overrides for matching transactions.

    Reusable core logic — called from the API endpoint and from
    the post-import pipeline.
    """
    cur = conn.cursor()

    # Load rules ordered by priority
    cur.execute("""
        SELECT id, merchant_pattern, amount_exact, amount_min, amount_max, target_merchant_id
        FROM merchant_split_rule
        ORDER BY priority, id
    """)
    rules = cur.fetchall()

    total_created = 0
    for rule_id, pattern, amt_exact, amt_min, amt_max, target_id in rules:
        amount_conditions = []
        params: dict = {"pattern": pattern, "target_id": str(target_id), "rule_id": rule_id}

        if amt_exact is not None:
            amount_conditions.append("rt.amount = %(amt_exact)s")
            params["amt_exact"] = amt_exact
        if amt_min is not None:
            amount_conditions.append("rt.amount >= %(amt_min)s")
            params["amt_min"] = amt_min
        if amt_max is not None:
            amount_conditions.append("rt.amount <= %(amt_max)s")
            params["amt_max"] = amt_max

        amount_where = (" AND " + " AND ".join(amount_conditions)) if amount_conditions else ""

        cur.execute(f"""
            INSERT INTO transaction_merchant_override
                (raw_transaction_id, canonical_merchant_id, split_rule_id)
            SELECT rt.id, %(target_id)s::uuid, %(rule_id)s
            FROM cleaned_transaction ct
            JOIN active_transaction rt ON rt.id = ct.raw_transaction_id
            WHERE ct.cleaned_merchant LIKE %(pattern)s
              {amount_where}
              AND NOT EXISTS (
                  SELECT 1 FROM transaction_merchant_override tmo
                  WHERE tmo.raw_transaction_id = rt.id
              )
            ON CONFLICT (raw_transaction_id) DO NOTHING
        """, params)
        total_created += cur.rowcount

    conn.commit()
    return {"rules_applied": len(rules), "overrides_created": total_created}


@router.post("/merchants/split-rules/apply")
def apply_split_rules(conn=Depends(get_conn), user: CurrentUser = Depends(require_admin)):
    """Apply all split rules, creating overrides for matching transactions."""
    return run_apply_split_rules(conn)


# ── Merchant Detail ──────────────────────────────────────────────────────────


@router.get("/merchants/{merchant_id}", response_model=MerchantDetail)
def get_merchant(
    merchant_id: UUID,
    scope: str | None = Query("personal", description="Scope filter"),
    conn=Depends(get_conn),
    user: CurrentUser = Depends(get_current_user),
):
    """Get full merchant detail including aliases."""
    cur = conn.cursor()

    cur.execute("""
        SELECT cm.id, cm.name, cm.display_name, cm.category_hint,
               cm.category_method, cm.category_confidence
        FROM canonical_merchant cm
        WHERE cm.id = %s
    """, (str(merchant_id),))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Merchant not found")

    cm_id, name, display_name, cat_hint, cat_method, cat_conf = row

    # Get aliases (all cleaned merchant strings mapped to this canonical)
    cur.execute("""
        SELECT mrm.cleaned_merchant
        FROM merchant_raw_mapping mrm
        WHERE mrm.canonical_merchant_id = %s
        ORDER BY mrm.cleaned_merchant
    """, (str(merchant_id),))
    aliases = [r[0] for r in cur.fetchall()]

    # Get recent transactions for this merchant (scope-filtered)
    effective_scope = validate_scope(scope, user)
    detail_scope_cond, detail_scope_params = scope_condition(effective_scope, user, alias="acct")
    cur.execute(f"""
        SELECT rt.id, rt.posted_at, rt.amount, rt.currency, rt.raw_merchant,
               rt.source, rt.institution, rt.account_ref
        FROM merchant_raw_mapping mrm
        JOIN cleaned_transaction ct ON ct.cleaned_merchant = mrm.cleaned_merchant
        JOIN active_transaction rt ON rt.id = ct.raw_transaction_id
        JOIN account acct ON acct.institution = rt.institution AND acct.account_ref = rt.account_ref
        WHERE mrm.canonical_merchant_id = %(merchant_id)s
          AND {detail_scope_cond}
        ORDER BY rt.posted_at DESC
        LIMIT 20
    """, {"merchant_id": str(merchant_id), **detail_scope_params})
    txn_columns = [desc[0] for desc in cur.description]
    txn_rows = cur.fetchall()
    transactions = [MerchantTransaction(**dict(zip(txn_columns, row))) for row in txn_rows]

    return MerchantDetail(
        id=cm_id,
        name=name,
        display_name=display_name,
        category_hint=cat_hint,
        category_method=cat_method,
        category_confidence=cat_conf,
        mapping_count=len(aliases),
        aliases=aliases,
        recent_transactions=transactions,
    )


@router.put("/merchants/{merchant_id}/name")
def update_merchant_name(
    merchant_id: UUID,
    body: MerchantNameUpdate,
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    """Update a canonical merchant's display name."""
    cur = conn.cursor()

    cur.execute("SELECT id FROM canonical_merchant WHERE id = %s", (str(merchant_id),))
    if not cur.fetchone():
        raise HTTPException(404, "Merchant not found")

    cur.execute(
        "UPDATE canonical_merchant SET display_name = %s WHERE id = %s",
        (body.display_name, str(merchant_id)),
    )
    conn.commit()

    return {"id": str(merchant_id), "display_name": body.display_name, "updated": True}


@router.put("/merchants/{merchant_id}/mapping")
def update_merchant_mapping(
    merchant_id: UUID,
    body: MerchantMappingUpdate,
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    """Update a canonical merchant's category hint."""
    cur = conn.cursor()

    cur.execute("SELECT id, name FROM canonical_merchant WHERE id = %s", (str(merchant_id),))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Merchant not found")

    cur.execute(
        """UPDATE canonical_merchant
           SET category_hint = %s, category_method = 'human', category_confidence = 1.00,
               category_set_at = now()
           WHERE id = %s""",
        (body.category_hint, str(merchant_id)),
    )
    conn.commit()

    return {
        "id": str(merchant_id),
        "name": row[1],
        "category_hint": body.category_hint,
        "updated": True,
    }


@router.post("/merchants/bulk-merge")
def bulk_merge_merchants(body: BulkMerchantMerge, conn=Depends(get_conn), user: CurrentUser = Depends(require_admin)):
    """Merge multiple merchants into one, optionally setting a display name.

    The first merchant in the list (or the one with the most mappings) survives.
    """
    from src.categorisation.merger import merge

    ids = [str(mid) for mid in body.merchant_ids]
    if len(ids) < 2:
        raise HTTPException(400, "Need at least 2 merchants to merge")

    cur = conn.cursor()

    # Pick surviving merchant: prefer one with category_hint, then most mappings
    cur.execute("""
        SELECT cm.id, cm.name, cm.category_hint,
               (SELECT count(*) FROM merchant_raw_mapping WHERE canonical_merchant_id = cm.id) AS cnt
        FROM canonical_merchant cm
        WHERE cm.id = ANY(%s::uuid[]) AND cm.merged_into_id IS NULL
        ORDER BY (cm.category_hint IS NOT NULL) DESC, cnt DESC, cm.name
    """, (ids,))
    rows = cur.fetchall()
    if len(rows) < 2:
        raise HTTPException(400, "Need at least 2 active (non-merged) merchants")

    surviving_id = str(rows[0][0])
    merged_count = 0

    for row in rows[1:]:
        try:
            merge(conn, secondary_id=str(row[0]), surviving_id=surviving_id)
            merged_count += 1
        except ValueError:
            pass

    # Set display name if provided
    if body.display_name:
        cur.execute(
            "UPDATE canonical_merchant SET display_name = %s WHERE id = %s",
            (body.display_name, surviving_id),
        )

    conn.commit()
    return {
        "surviving_id": surviving_id,
        "merged": merged_count,
        "display_name": body.display_name,
    }


@router.post("/merchants/{merchant_id}/merge")
def merge_merchant(
    merchant_id: UUID,
    body: MerchantMergeRequest,
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    """Merge another merchant into this one (this one survives)."""
    from src.categorisation.merger import merge

    try:
        result = merge(conn, secondary_id=str(body.merge_from_id), surviving_id=str(merchant_id))
        conn.commit()
    except ValueError as e:
        raise HTTPException(400, str(e))

    return {
        "surviving_id": str(merchant_id),
        "merged_from_id": str(body.merge_from_id),
        **result,
    }


@router.post("/merchants/{merchant_id}/split-alias")
def split_alias(
    merchant_id: UUID,
    body: AliasSplitRequest,
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    """Split a single alias off from this merchant into its own canonical_merchant."""
    cur = conn.cursor()

    # 1. Verify merchant exists and is not merged
    cur.execute(
        "SELECT id FROM canonical_merchant WHERE id = %s AND merged_into_id IS NULL",
        (str(merchant_id),),
    )
    if not cur.fetchone():
        raise HTTPException(404, "Merchant not found or already merged")

    # 2. Verify alias belongs to this merchant
    cur.execute(
        "SELECT cleaned_merchant FROM merchant_raw_mapping WHERE canonical_merchant_id = %s AND cleaned_merchant = %s",
        (str(merchant_id), body.alias),
    )
    if not cur.fetchone():
        raise HTTPException(404, "Alias not found on this merchant")

    # 3. Must keep at least one alias
    cur.execute(
        "SELECT count(*) FROM merchant_raw_mapping WHERE canonical_merchant_id = %s",
        (str(merchant_id),),
    )
    if cur.fetchone()[0] <= 1:
        raise HTTPException(400, "Cannot split the only alias from a merchant")

    # 4. Create new canonical_merchant (or reuse existing with same name)
    #    Use a unique name to avoid colliding with the source merchant
    new_name = body.alias
    cur.execute("SELECT id FROM canonical_merchant WHERE name = %s", (new_name,))
    existing = cur.fetchone()
    if existing and str(existing[0]) == str(merchant_id):
        # The alias name matches the source merchant's name — use a suffixed name
        new_name = f"{body.alias} (split)"

    cur.execute(
        "INSERT INTO canonical_merchant (name) VALUES (%s) ON CONFLICT (name) DO NOTHING RETURNING id",
        (new_name,),
    )
    result = cur.fetchone()
    if result:
        new_id = str(result[0])
    else:
        cur.execute("SELECT id FROM canonical_merchant WHERE name = %s", (new_name,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(500, "Failed to create or find canonical merchant")
        new_id = str(row[0])
        # Reactivate if it was previously merged
        cur.execute(
            "UPDATE canonical_merchant SET merged_into_id = NULL WHERE id = %s",
            (new_id,),
        )

    # 5. Reassign the mapping
    cur.execute(
        "UPDATE merchant_raw_mapping SET canonical_merchant_id = %s WHERE cleaned_merchant = %s",
        (new_id, body.alias),
    )

    # 6. If the split alias was the merchant's name, update name to display_name or a remaining alias
    cur.execute("SELECT name, display_name FROM canonical_merchant WHERE id = %s", (str(merchant_id),))
    cm_name, cm_display = cur.fetchone()
    if cm_name == body.alias:
        # Pick the best replacement: display_name first, then first remaining alias
        if cm_display:
            new_cm_name = cm_display
        else:
            cur.execute(
                "SELECT cleaned_merchant FROM merchant_raw_mapping WHERE canonical_merchant_id = %s ORDER BY cleaned_merchant LIMIT 1",
                (str(merchant_id),),
            )
            new_cm_name = cur.fetchone()[0]
        cur.execute(
            "UPDATE canonical_merchant SET name = %s WHERE id = %s",
            (new_cm_name, str(merchant_id)),
        )

    conn.commit()

    return {
        "original_merchant_id": str(merchant_id),
        "new_merchant_id": new_id,
        "alias": body.alias,
    }


@router.post("/categorisation/run")
def run_categorisation(
    include_llm: bool = Query(False, description="Also run LLM categorisation"),
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    """Trigger the categorisation engine."""
    from src.categorisation.engine import run_naming, run_source_hints, run_llm

    naming_result = run_naming(conn)
    hints_result = run_source_hints(conn)

    llm_result = {}
    if include_llm:
        llm_result = run_llm(conn)

    return {
        "display_names_set": naming_result["display_names_set"],
        "rules_merchants_merged": naming_result.get("rules_merchants_merged", 0),
        "rules_merchants_renamed": naming_result.get("rules_merchants_renamed", 0),
        "source_hint_suggestions": hints_result["total_suggestions"],
        "auto_accepted": hints_result["auto_accepted"],
        "queued_for_review": hints_result["queued"],
        "llm_queued": llm_result.get("llm_queued", 0),
    }
