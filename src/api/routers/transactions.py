"""Transaction endpoints."""

from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.deps import CurrentUser, get_conn, get_current_user, require_admin, scope_condition, validate_scope
from src.api.models import (
    BulkCategoryUpdate,
    BulkMerchantNameUpdate,
    BulkNoteUpdate,
    BulkTagAdd,
    BulkTagRemove,
    BulkTagReplace,
    CategoryUpdate,
    DedupGroupInfo,
    DedupMember,
    EconomicEventInfo,
    EconomicEventLeg,
    LinkTransferRequest,
    NoteUpdate,
    SplitLineItem,
    SplitRequest,
    TagItem,
    TagRename,
    TagUpdate,
    TransactionDetail,
    TransactionItem,
    TransactionList,
)

router = APIRouter()


@router.get("/transactions", response_model=TransactionList)
def list_transactions(
    cursor: str | None = Query(None, description="Cursor for pagination (posted_at,id)"),
    offset: int | None = Query(None, ge=0, description="Offset pagination (non-date sorts)"),
    limit: int = Query(50, ge=1, le=200),
    sort_by: str = Query("posted_at", description="Sort column"),
    sort_dir: str = Query("desc", description="Sort direction"),
    institution: str | None = None,
    account_ref: str | None = None,
    source: str | None = None,
    category: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    amount_min: Decimal | None = None,
    amount_max: Decimal | None = None,
    currency: str | None = None,
    search: str | None = Query(None, description="Search merchant name"),
    tag: str | None = Query(None, description="Filter by exact tag"),
    tag_pattern: str | None = Query(None, description="Filter by tag regex (case-insensitive)"),
    uncategorised: bool = Query(False, description="Show only uncategorised transactions"),
    scope: str | None = Query("personal", description="Scope filter (personal/business/all)"),
    conn=Depends(get_conn),
    user: CurrentUser = Depends(get_current_user),
):
    """List deduplicated transactions with full merchant/category chain."""
    cur = conn.cursor()

    # Sort column mapping
    sort_columns = {
        "posted_at": "rt.posted_at",
        "merchant": "COALESCE(cm_override.display_name, cm_override.name, cm.display_name, cm.name)",
        "category": "COALESCE(tcat.full_path, cat_override.full_path, cat.full_path)",
        "amount": "rt.amount",
        "source": "rt.source",
    }
    sort_col = sort_columns.get(sort_by, "rt.posted_at")
    if sort_dir not in ("asc", "desc"):
        sort_dir = "desc"
    use_keyset = sort_by == "posted_at"

    # Build WHERE clauses
    conditions = []
    params: dict = {"limit": limit + 1}  # fetch one extra to detect has_more

    # Keyset pagination: cursor is "posted_at,id" (only for date sort)
    if cursor and use_keyset:
        try:
            cursor_date_str, cursor_id = cursor.split(",", 1)
            params["cursor_date"] = cursor_date_str
            params["cursor_id"] = cursor_id
            op = "<" if sort_dir == "desc" else ">"
            conditions.append(
                f"(rt.posted_at, rt.id) {op} (%(cursor_date)s::date, %(cursor_id)s::uuid)"
            )
        except ValueError:
            raise HTTPException(400, "Invalid cursor format, expected 'date,uuid'")

    if institution:
        conditions.append("rt.institution = %(institution)s")
        params["institution"] = institution
    if account_ref:
        conditions.append("rt.account_ref = %(account_ref)s")
        params["account_ref"] = account_ref
    if source:
        conditions.append("rt.source = %(source)s")
        params["source"] = source
    if date_from:
        conditions.append("rt.posted_at >= %(date_from)s")
        params["date_from"] = date_from
    if date_to:
        conditions.append("rt.posted_at <= %(date_to)s")
        params["date_to"] = date_to
    if amount_min is not None:
        conditions.append("rt.amount >= %(amount_min)s")
        params["amount_min"] = amount_min
    if amount_max is not None:
        conditions.append("rt.amount <= %(amount_max)s")
        params["amount_max"] = amount_max
    if currency:
        conditions.append("rt.currency = %(currency)s")
        params["currency"] = currency
    if search:
        # Split into tokens — each must match at least one searchable field.
        # e.g. "bash 34" finds merchants matching "bash" whose amount contains "34"
        tokens = search.strip().split()
        for i, token in enumerate(tokens):
            tok_key = f"search_{i}"
            amt_key = f"amount_search_{i}"
            conditions.append(
                f"(cm.name ILIKE %({tok_key})s OR ct.cleaned_merchant ILIKE %({tok_key})s "
                f"OR rt.raw_merchant ILIKE %({tok_key})s OR tn.note ILIKE %({tok_key})s "
                f"OR cm.display_name ILIKE %({tok_key})s "
                f"OR cm_override.name ILIKE %({tok_key})s OR cm_override.display_name ILIKE %({tok_key})s "
                f"OR trim(trailing '.' from trim(trailing '0' from rt.amount::text)) LIKE %({amt_key})s)"
            )
            params[tok_key] = f"%{token}%"
            params[amt_key] = f"%{token}%"
    if category:
        conditions.append("COALESCE(tcat.full_path, cat.full_path) LIKE %(category)s")
        params["category"] = f"{category}%"
    if tag:
        conditions.append(
            "EXISTS (SELECT 1 FROM transaction_tag tt WHERE tt.raw_transaction_id = rt.id AND tt.tag = %(tag)s)"
        )
        params["tag"] = tag
    if tag_pattern:
        conditions.append(
            "EXISTS (SELECT 1 FROM transaction_tag tt WHERE tt.raw_transaction_id = rt.id AND tt.tag ~* %(tag_pattern)s)"
        )
        params["tag_pattern"] = tag_pattern
    if uncategorised:
        conditions.append(
            "COALESCE(tcat.full_path, cat.full_path) IS NULL"
        )

    # Always exclude archived accounts; scope filtered by user permissions
    conditions.append("(a.is_archived IS NOT TRUE)")
    effective_scope = validate_scope(scope, user)
    scope_cond, scope_params = scope_condition(effective_scope, user, alias="a")
    conditions.append(scope_cond)
    params.update(scope_params)

    where = "WHERE " + " AND ".join(conditions)

    sql = f"""
        SELECT
            rt.id, rt.source, rt.institution, rt.account_ref,
            rt.posted_at, rt.amount, rt.currency,
            rt.raw_merchant, rt.raw_memo,
            ct.cleaned_merchant,
            COALESCE(cm_override.id, cm.id) AS canonical_merchant_id,
            COALESCE(cm_override.display_name, cm_override.name, cm.display_name, cm.name) AS canonical_merchant_name,
            mrm.match_type AS merchant_match_type,
            COALESCE(tcat.full_path, cat_override.full_path, cat.full_path) AS category_path,
            COALESCE(tcat.name, cat_override.name, cat.name) AS category_name,
            COALESCE(tcat.category_type, cat_override.category_type, cat.category_type) AS category_type,
            (tco.raw_transaction_id IS NOT NULL) AS category_is_override,
            EXISTS (SELECT 1 FROM transaction_split_line sl WHERE sl.raw_transaction_id = rt.id) AS is_split,
            tn.note,
            (tmo.raw_transaction_id IS NOT NULL) AS merchant_is_override
        FROM active_transaction rt
        LEFT JOIN account a
            ON a.institution = rt.institution AND a.account_ref = rt.account_ref
        LEFT JOIN cleaned_transaction ct ON ct.raw_transaction_id = rt.id
        LEFT JOIN merchant_raw_mapping mrm ON mrm.cleaned_merchant = ct.cleaned_merchant
        LEFT JOIN canonical_merchant cm ON cm.id = mrm.canonical_merchant_id
        LEFT JOIN transaction_merchant_override tmo ON tmo.raw_transaction_id = rt.id
        LEFT JOIN canonical_merchant cm_override ON cm_override.id = tmo.canonical_merchant_id
        LEFT JOIN category cat ON cat.full_path = cm.category_hint
        LEFT JOIN category cat_override ON cat_override.full_path = cm_override.category_hint
        LEFT JOIN transaction_category_override tco ON tco.raw_transaction_id = rt.id
        LEFT JOIN category tcat ON tcat.full_path = tco.category_path
        LEFT JOIN transaction_note tn ON tn.raw_transaction_id = rt.id
        {where}
        ORDER BY {sort_col} {sort_dir} NULLS LAST, rt.id {sort_dir}
        LIMIT %(limit)s
        {"OFFSET %(offset)s" if not use_keyset and offset is not None else ""}
    """
    if not use_keyset and offset is not None:
        params["offset"] = offset

    cur.execute(sql, params)
    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()

    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    items = [TransactionItem(**dict(zip(columns, row))) for row in rows]

    # Batch-load tags for returned items
    if items:
        txn_ids = [str(item.id) for item in items]
        cur.execute("""
            SELECT raw_transaction_id, array_agg(tag ORDER BY tag)
            FROM transaction_tag
            WHERE raw_transaction_id = ANY(%s::uuid[])
            GROUP BY raw_transaction_id
        """, (txn_ids,))
        tags_by_txn = {str(r[0]): r[1] for r in cur.fetchall()}
        for item in items:
            item.tags = tags_by_txn.get(str(item.id), [])

    next_cursor = None
    if has_more and items and use_keyset:
        last = items[-1]
        next_cursor = f"{last.posted_at},{last.id}"

    return TransactionList(items=items, next_cursor=next_cursor, has_more=has_more)


@router.get("/transactions/{transaction_id}", response_model=TransactionDetail)
def get_transaction(
    transaction_id: UUID,
    conn=Depends(get_conn),
    user: CurrentUser = Depends(get_current_user),
):
    """Get full transaction detail including dedup group and economic event."""
    cur = conn.cursor()

    # Base transaction with merchant/category chain + note
    cur.execute("""
        SELECT
            rt.id, rt.source, rt.institution, rt.account_ref,
            rt.posted_at, rt.amount, rt.currency,
            rt.raw_merchant, rt.raw_memo, rt.raw_data,
            ct.cleaned_merchant,
            COALESCE(cm_override.id, cm.id) AS canonical_merchant_id,
            COALESCE(cm_override.display_name, cm_override.name, cm.display_name, cm.name) AS canonical_merchant_name,
            mrm.match_type AS merchant_match_type,
            COALESCE(tcat.full_path, cat_override.full_path, cat.full_path) AS category_path,
            COALESCE(tcat.name, cat_override.name, cat.name) AS category_name,
            COALESCE(tcat.category_type, cat_override.category_type, cat.category_type) AS category_type,
            (tco.raw_transaction_id IS NOT NULL) AS category_is_override,
            EXISTS (SELECT 1 FROM transaction_split_line sl WHERE sl.raw_transaction_id = rt.id) AS is_split,
            tn.note,
            tn.source AS note_source,
            (tmo.raw_transaction_id IS NOT NULL) AS merchant_is_override
        FROM raw_transaction rt
        LEFT JOIN cleaned_transaction ct ON ct.raw_transaction_id = rt.id
        LEFT JOIN merchant_raw_mapping mrm ON mrm.cleaned_merchant = ct.cleaned_merchant
        LEFT JOIN canonical_merchant cm ON cm.id = mrm.canonical_merchant_id
        LEFT JOIN transaction_merchant_override tmo ON tmo.raw_transaction_id = rt.id
        LEFT JOIN canonical_merchant cm_override ON cm_override.id = tmo.canonical_merchant_id
        LEFT JOIN category cat ON cat.full_path = cm.category_hint
        LEFT JOIN category cat_override ON cat_override.full_path = cm_override.category_hint
        LEFT JOIN transaction_category_override tco ON tco.raw_transaction_id = rt.id
        LEFT JOIN category tcat ON tcat.full_path = tco.category_path
        LEFT JOIN transaction_note tn ON tn.raw_transaction_id = rt.id
        WHERE rt.id = %s
    """, (str(transaction_id),))

    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Transaction not found")

    columns = [desc[0] for desc in cur.description]
    txn_data = dict(zip(columns, row))

    # Dedup group info
    dedup_group = None
    cur.execute("""
        SELECT dg.id, dg.match_rule, dg.confidence,
               dgm2.raw_transaction_id, rt2.source, dgm2.is_preferred
        FROM dedup_group_member dgm
        JOIN dedup_group dg ON dg.id = dgm.dedup_group_id
        JOIN dedup_group_member dgm2 ON dgm2.dedup_group_id = dg.id
        JOIN raw_transaction rt2 ON rt2.id = dgm2.raw_transaction_id
        WHERE dgm.raw_transaction_id = %s
    """, (str(transaction_id),))

    dedup_rows = cur.fetchall()
    if dedup_rows:
        members = [
            DedupMember(
                raw_transaction_id=r[3],
                source=r[4],
                is_preferred=r[5],
            )
            for r in dedup_rows
        ]
        dedup_group = DedupGroupInfo(
            group_id=dedup_rows[0][0],
            match_rule=dedup_rows[0][1],
            confidence=dedup_rows[0][2],
            members=members,
        )

    # Economic event info
    economic_event = None
    cur.execute("""
        SELECT ee.id, ee.event_type, ee.initiated_at, ee.description,
               eel2.raw_transaction_id, eel2.leg_type, eel2.amount, eel2.currency
        FROM economic_event_leg eel
        JOIN economic_event ee ON ee.id = eel.economic_event_id
        JOIN economic_event_leg eel2 ON eel2.economic_event_id = ee.id
        WHERE eel.raw_transaction_id = %s
    """, (str(transaction_id),))

    event_rows = cur.fetchall()
    if event_rows:
        legs = [
            EconomicEventLeg(
                raw_transaction_id=r[4],
                leg_type=r[5],
                amount=r[6],
                currency=r[7],
            )
            for r in event_rows
        ]
        economic_event = EconomicEventInfo(
            event_id=event_rows[0][0],
            event_type=event_rows[0][1],
            initiated_at=event_rows[0][2],
            description=event_rows[0][3],
            legs=legs,
        )

    # Tags
    cur.execute("""
        SELECT tag, source FROM transaction_tag
        WHERE raw_transaction_id = %s
        ORDER BY tag
    """, (str(transaction_id),))
    tags = [TagItem(tag=r[0], source=r[1]) for r in cur.fetchall()]

    # Split lines
    cur.execute("""
        SELECT sl.id, sl.line_number, sl.amount, sl.currency,
               sl.category_path, cat.name AS category_name, sl.description
        FROM transaction_split_line sl
        LEFT JOIN category cat ON cat.full_path = sl.category_path
        WHERE sl.raw_transaction_id = %s
        ORDER BY sl.line_number
    """, (str(transaction_id),))
    split_cols = [desc[0] for desc in cur.description]
    split_lines = [SplitLineItem(**dict(zip(split_cols, r))) for r in cur.fetchall()]

    return TransactionDetail(
        **txn_data,
        tags=tags,
        dedup_group=dedup_group,
        economic_event=economic_event,
        split_lines=split_lines,
    )


@router.put("/transactions/{transaction_id}/note")
def update_note(
    transaction_id: UUID,
    body: NoteUpdate,
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    """Create, update, or delete a transaction note."""
    cur = conn.cursor()

    # Verify transaction exists
    cur.execute("SELECT 1 FROM raw_transaction WHERE id = %s", (str(transaction_id),))
    if not cur.fetchone():
        raise HTTPException(404, "Transaction not found")

    note_text = body.note.strip()

    if not note_text:
        # Delete note
        cur.execute(
            "DELETE FROM transaction_note WHERE raw_transaction_id = %s",
            (str(transaction_id),),
        )
    else:
        # Upsert note
        cur.execute("""
            INSERT INTO transaction_note (raw_transaction_id, note, source)
            VALUES (%s, %s, 'user')
            ON CONFLICT (raw_transaction_id)
            DO UPDATE SET note = EXCLUDED.note, source = 'user', updated_at = now()
        """, (str(transaction_id), note_text))

    conn.commit()
    return {"ok": True}


@router.put("/transactions/{transaction_id}/category")
def update_category(
    transaction_id: UUID,
    body: CategoryUpdate,
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    """Create, update, or delete a transaction category override."""
    cur = conn.cursor()

    # Verify transaction exists
    cur.execute("SELECT 1 FROM raw_transaction WHERE id = %s", (str(transaction_id),))
    if not cur.fetchone():
        raise HTTPException(404, "Transaction not found")

    category_path = body.category_path.strip()

    if not category_path:
        # Delete override — falls back to merchant category
        cur.execute(
            "DELETE FROM transaction_category_override WHERE raw_transaction_id = %s",
            (str(transaction_id),),
        )
    else:
        # Validate category exists
        cur.execute("SELECT 1 FROM category WHERE full_path = %s", (category_path,))
        if not cur.fetchone():
            raise HTTPException(400, f"Category not found: {category_path}")

        # Upsert override
        cur.execute("""
            INSERT INTO transaction_category_override (raw_transaction_id, category_path, source)
            VALUES (%s, %s, 'user')
            ON CONFLICT (raw_transaction_id)
            DO UPDATE SET category_path = EXCLUDED.category_path, source = 'user', updated_at = now()
        """, (str(transaction_id), category_path))

    # Re-apply tag rules for this transaction (category may affect rule matching)
    from src.api.routers.tag_rules import reconcile_tag_rules_for_transactions
    reconcile_tag_rules_for_transactions(conn, [str(transaction_id)])

    conn.commit()
    return {"ok": True}


@router.post("/transactions/{transaction_id}/link-transfer")
def link_transfer(
    transaction_id: UUID,
    body: LinkTransferRequest,
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    """Link two transactions as an inter-account transfer / FX conversion."""
    cur = conn.cursor()

    txn_id_a = str(transaction_id)
    txn_id_b = str(body.counterpart_id)

    if txn_id_a == txn_id_b:
        raise HTTPException(400, "Cannot link a transaction to itself")

    # Fetch both transactions
    cur.execute("""
        SELECT id, amount, currency, posted_at
        FROM raw_transaction WHERE id IN (%s, %s)
    """, (txn_id_a, txn_id_b))
    rows = cur.fetchall()
    if len(rows) != 2:
        raise HTTPException(404, "One or both transactions not found")

    txns = {str(r[0]): {"amount": r[1], "currency": r[2], "posted_at": r[3]} for r in rows}

    # Check neither is already in an economic event
    cur.execute("""
        SELECT raw_transaction_id FROM economic_event_leg
        WHERE raw_transaction_id IN (%s, %s)
    """, (txn_id_a, txn_id_b))
    existing = cur.fetchall()
    if existing:
        raise HTTPException(409, "One or both transactions are already linked to an economic event")

    a = txns[txn_id_a]
    b = txns[txn_id_b]

    # Determine event type
    is_fx = a["currency"] != b["currency"]
    event_type = "fx_conversion" if is_fx else "inter_account_transfer"

    # Determine source (debit) and target (credit)
    if a["amount"] < 0:
        source_id, target_id = txn_id_a, txn_id_b
        source, target = a, b
    else:
        source_id, target_id = txn_id_b, txn_id_a
        source, target = b, a

    description = f"{abs(source['amount'])} {source['currency']} -> {abs(target['amount'])} {target['currency']}"

    # Create economic event
    cur.execute("""
        INSERT INTO economic_event (event_type, initiated_at, description, match_status)
        VALUES (%s, %s, %s, 'manual')
        RETURNING id
    """, (event_type, source["posted_at"], description))
    event_id = cur.fetchone()[0]

    # Create legs
    cur.execute("""
        INSERT INTO economic_event_leg
            (economic_event_id, raw_transaction_id, leg_type, amount, currency)
        VALUES (%s, %s, 'source', %s, %s)
    """, (event_id, source_id, source["amount"], source["currency"]))

    cur.execute("""
        INSERT INTO economic_event_leg
            (economic_event_id, raw_transaction_id, leg_type, amount, currency)
        VALUES (%s, %s, 'target', %s, %s)
    """, (event_id, target_id, target["amount"], target["currency"]))

    # Create fx_event if cross-currency
    if is_fx and source["amount"] != 0:
        rate = abs(target["amount"] / source["amount"])
        cur.execute("""
            INSERT INTO fx_event
                (economic_event_id, source_amount, source_currency,
                 target_amount, target_currency, achieved_rate, provider)
            VALUES (%s, %s, %s, %s, %s, %s, 'manual')
        """, (
            event_id,
            abs(source["amount"]),
            source["currency"],
            abs(target["amount"]),
            target["currency"],
            rate,
        ))

    # Set +Transfer category override on both legs
    for tid in [source_id, target_id]:
        cur.execute("""
            INSERT INTO transaction_category_override (raw_transaction_id, category_path, source)
            VALUES (%s, '+Transfer', 'system')
            ON CONFLICT (raw_transaction_id)
            DO UPDATE SET category_path = '+Transfer', source = 'system', updated_at = now()
        """, (tid,))

    conn.commit()
    return {"ok": True, "event_id": str(event_id)}


@router.delete("/economic-events/{event_id}")
def unlink_event(
    event_id: UUID,
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    """Delete an economic event and its legs (unlink transactions)."""
    cur = conn.cursor()

    cur.execute("SELECT 1 FROM economic_event WHERE id = %s", (str(event_id),))
    if not cur.fetchone():
        raise HTTPException(404, "Economic event not found")

    # Get leg transaction IDs before deleting
    cur.execute(
        "SELECT raw_transaction_id FROM economic_event_leg WHERE economic_event_id = %s",
        (str(event_id),),
    )
    leg_txn_ids = [row[0] for row in cur.fetchall()]

    cur.execute("DELETE FROM fx_event WHERE economic_event_id = %s", (str(event_id),))
    cur.execute("DELETE FROM economic_event_leg WHERE economic_event_id = %s", (str(event_id),))
    cur.execute("DELETE FROM economic_event WHERE id = %s", (str(event_id),))

    # Remove system-set +Transfer overrides (preserve user overrides)
    for tid in leg_txn_ids:
        cur.execute("""
            DELETE FROM transaction_category_override
            WHERE raw_transaction_id = %s AND category_path = '+Transfer' AND source = 'system'
        """, (str(tid),))

    conn.commit()
    return {"ok": True}


# ── Tags ─────────────────────────────────────────────────────────────────────


@router.get("/tags")
def list_tags(conn=Depends(get_conn), user: CurrentUser = Depends(get_current_user)):
    """List all tags with usage counts and source breakdown."""
    cur = conn.cursor()
    cur.execute("""
        SELECT tag, source, tag_rule_id, COUNT(*) AS cnt
        FROM transaction_tag
        GROUP BY tag, source, tag_rule_id
        ORDER BY tag
    """)
    rows = cur.fetchall()

    tags: dict[str, dict] = {}
    for tag, source, rule_id, cnt in rows:
        if tag not in tags:
            tags[tag] = {"tag": tag, "count": 0, "sources": {}, "rule_ids": []}
        entry = tags[tag]
        entry["count"] += cnt
        entry["sources"][source] = entry["sources"].get(source, 0) + cnt
        if rule_id is not None and rule_id not in entry["rule_ids"]:
            entry["rule_ids"].append(rule_id)

    return {"items": list(tags.values())}


@router.put("/tags/{tag_name}")
def rename_tag(
    tag_name: str,
    body: TagRename,
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    """Rename a tag across all transactions. Updates tag rules that generate it."""
    cur = conn.cursor()
    new_name = body.new_name.strip()
    if not new_name:
        raise HTTPException(400, "New tag name cannot be empty")
    if new_name == tag_name:
        raise HTTPException(400, "New name is the same as the old name")

    # Check the tag exists
    cur.execute("SELECT COUNT(*) FROM transaction_tag WHERE tag = %s", (tag_name,))
    if cur.fetchone()[0] == 0:
        raise HTTPException(404, "Tag not found")

    # Update transaction_tag rows (handle conflict if new name already exists on same txn)
    cur.execute("""
        DELETE FROM transaction_tag tt1
        USING transaction_tag tt2
        WHERE tt1.tag = %s
        AND tt2.tag = %s
        AND tt1.raw_transaction_id = tt2.raw_transaction_id
    """, (tag_name, new_name))

    cur.execute("""
        UPDATE transaction_tag SET tag = %s WHERE tag = %s
    """, (new_name, tag_name))
    affected = cur.rowcount

    # Update any tag rules that reference this tag
    cur.execute("""
        UPDATE tag_rule
        SET tags = array_replace(tags, %s, %s),
            updated_at = now()
        WHERE %s = ANY(tags)
    """, (tag_name, new_name, tag_name))
    rules_updated = cur.rowcount

    conn.commit()
    return {"ok": True, "affected": affected, "rules_updated": rules_updated}


@router.delete("/tags/{tag_name}")
def delete_tag(
    tag_name: str,
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    """Delete a tag from all transactions."""
    cur = conn.cursor()
    cur.execute("DELETE FROM transaction_tag WHERE tag = %s", (tag_name,))
    affected = cur.rowcount

    if affected == 0:
        raise HTTPException(404, "Tag not found")

    conn.commit()
    return {"ok": True, "affected": affected}


@router.post("/transactions/{transaction_id}/tags")
def add_tag(
    transaction_id: UUID,
    body: TagUpdate,
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    """Add a tag to a transaction."""
    cur = conn.cursor()

    cur.execute("SELECT 1 FROM raw_transaction WHERE id = %s", (str(transaction_id),))
    if not cur.fetchone():
        raise HTTPException(404, "Transaction not found")

    tag_name = body.tag.strip()
    if not tag_name:
        raise HTTPException(400, "Tag cannot be empty")

    cur.execute("""
        INSERT INTO transaction_tag (raw_transaction_id, tag, source)
        VALUES (%s, %s, 'user')
        ON CONFLICT (raw_transaction_id, tag) DO NOTHING
    """, (str(transaction_id), tag_name))

    conn.commit()
    return {"ok": True}


@router.delete("/transactions/{transaction_id}/tags/{tag_name}")
def remove_tag(
    transaction_id: UUID,
    tag_name: str,
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    """Remove a tag from a transaction."""
    cur = conn.cursor()
    cur.execute("""
        DELETE FROM transaction_tag
        WHERE raw_transaction_id = %s AND tag = %s
    """, (str(transaction_id), tag_name))

    if cur.rowcount == 0:
        raise HTTPException(404, "Tag not found on this transaction")

    conn.commit()
    return {"ok": True}


# ── Split Transactions ───────────────────────────────────────────────────────


@router.put("/transactions/{transaction_id}/split")
def save_split(
    transaction_id: UUID,
    body: SplitRequest,
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    """Create or replace a split on a transaction."""
    cur = conn.cursor()

    # Fetch parent transaction
    cur.execute(
        "SELECT amount, currency FROM raw_transaction WHERE id = %s",
        (str(transaction_id),),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Transaction not found")
    parent_amount, parent_currency = row

    # Validate minimum 2 lines
    if len(body.lines) < 2:
        raise HTTPException(400, "A split requires at least 2 lines")

    # Validate amounts sum to parent
    line_sum = sum(line.amount for line in body.lines)
    if line_sum != parent_amount:
        raise HTTPException(
            400,
            f"Split lines sum to {line_sum} but transaction amount is {parent_amount}",
        )

    # Validate category paths exist
    for line in body.lines:
        if line.category_path:
            cur.execute(
                "SELECT 1 FROM category WHERE full_path = %s",
                (line.category_path,),
            )
            if not cur.fetchone():
                raise HTTPException(400, f"Category not found: {line.category_path}")

    # Replace: delete existing lines, insert new
    cur.execute(
        "DELETE FROM transaction_split_line WHERE raw_transaction_id = %s",
        (str(transaction_id),),
    )

    created = []
    for i, line in enumerate(body.lines, 1):
        cur.execute("""
            INSERT INTO transaction_split_line
                (raw_transaction_id, line_number, amount, currency,
                 category_path, description)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            str(transaction_id), i, line.amount, parent_currency.strip(),
            line.category_path, line.description,
        ))
        line_id = cur.fetchone()[0]
        created.append({
            "id": str(line_id),
            "line_number": i,
            "amount": str(line.amount),
            "currency": parent_currency.strip(),
            "category_path": line.category_path,
            "description": line.description,
        })

    conn.commit()
    return {"ok": True, "lines": created}


@router.delete("/transactions/{transaction_id}/split")
def delete_split(
    transaction_id: UUID,
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    """Remove a split, reverting to a single unsplit transaction."""
    cur = conn.cursor()

    cur.execute(
        "DELETE FROM transaction_split_line WHERE raw_transaction_id = %s",
        (str(transaction_id),),
    )
    deleted = cur.rowcount

    if deleted == 0:
        raise HTTPException(404, "No split found for this transaction")

    conn.commit()
    return {"ok": True, "lines_removed": deleted}


@router.get("/transactions/{transaction_id}/split/suggest-amazon")
def suggest_amazon_split(
    transaction_id: UUID,
    conn=Depends(get_conn),
    user: CurrentUser = Depends(get_current_user),
):
    """Suggest split lines from matched Amazon order items."""
    cur = conn.cursor()

    # Fetch parent transaction
    cur.execute(
        "SELECT amount, currency FROM raw_transaction WHERE id = %s",
        (str(transaction_id),),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Transaction not found")
    parent_amount, parent_currency = row

    # Find matched Amazon orders
    cur.execute("""
        SELECT DISTINCT am.order_id
        FROM amazon_order_match am
        WHERE am.raw_transaction_id = %s
    """, (str(transaction_id),))
    order_ids = [r[0] for r in cur.fetchall()]

    if not order_ids:
        raise HTTPException(404, "No Amazon order match found for this transaction")

    # Fetch order items
    cur.execute("""
        SELECT description, unit_price, quantity, category
        FROM amazon_order_item
        WHERE order_id = ANY(%s) AND unit_price IS NOT NULL
        ORDER BY order_id, description
    """, (order_ids,))

    lines = []
    items_total = Decimal("0")
    for desc, unit_price, quantity, category in cur.fetchall():
        line_amount = -(unit_price * quantity)  # negative = expense
        items_total += line_amount
        lines.append({
            "amount": str(line_amount),
            "description": desc,
            "category_path": None,
        })

    # Add adjustment line if totals don't match (shipping, discounts, etc.)
    remainder = parent_amount - items_total
    if remainder != 0:
        lines.append({
            "amount": str(remainder),
            "description": "Shipping / Adjustment",
            "category_path": None,
        })

    return {"lines": lines, "order_ids": order_ids}


# ── Bulk Operations ──────────────────────────────────────────────────────────


@router.post("/transactions/bulk/category")
def bulk_update_category(body: BulkCategoryUpdate, conn=Depends(get_conn), user: CurrentUser = Depends(require_admin)):
    """Bulk set or remove category override on multiple transactions."""
    cur = conn.cursor()
    ids = [str(tid) for tid in body.transaction_ids]
    if not ids:
        return {"ok": True, "affected": 0}

    category_path = body.category_path.strip()

    if not category_path:
        # Remove overrides
        cur.execute("""
            DELETE FROM transaction_category_override
            WHERE raw_transaction_id = ANY(%s::uuid[])
        """, (ids,))
        affected = cur.rowcount
    else:
        # Validate category exists
        cur.execute("SELECT 1 FROM category WHERE full_path = %s", (category_path,))
        if not cur.fetchone():
            raise HTTPException(400, f"Category not found: {category_path}")

        # Upsert overrides
        cur.execute("""
            INSERT INTO transaction_category_override (raw_transaction_id, category_path, source)
            SELECT unnest(%s::uuid[]), %s, 'user'
            ON CONFLICT (raw_transaction_id)
            DO UPDATE SET category_path = EXCLUDED.category_path,
                          source = 'user', updated_at = now()
        """, (ids, category_path))
        affected = cur.rowcount

    # Re-apply tag rules for affected transactions
    from src.api.routers.tag_rules import reconcile_tag_rules_for_transactions
    reconcile_tag_rules_for_transactions(conn, ids)

    conn.commit()
    return {"ok": True, "affected": affected}


@router.post("/transactions/bulk/merchant-name")
def bulk_update_merchant_name(body: BulkMerchantNameUpdate, conn=Depends(get_conn), user: CurrentUser = Depends(require_admin)):
    """Bulk update display_name for the effective canonical merchants of given transactions.

    Uses the override merchant (from transaction_merchant_override) when present,
    falling back to the base merchant (from merchant_raw_mapping).  This prevents
    renaming a split-rule sub-merchant from accidentally renaming the shared parent.
    """
    cur = conn.cursor()
    ids = [str(tid) for tid in body.transaction_ids]
    if not ids:
        return {"ok": True, "affected": 0, "merchant_ids": []}

    # Find the EFFECTIVE canonical merchant for each selected transaction:
    #   override merchant (split-rule or manual) takes priority over base mapping.
    cur.execute("""
        SELECT DISTINCT COALESCE(tmo.canonical_merchant_id, mrm.canonical_merchant_id) AS effective_id
        FROM raw_transaction rt
        JOIN cleaned_transaction ct ON ct.raw_transaction_id = rt.id
        JOIN merchant_raw_mapping mrm ON mrm.cleaned_merchant = ct.cleaned_merchant
        LEFT JOIN transaction_merchant_override tmo ON tmo.raw_transaction_id = rt.id
        JOIN canonical_merchant cm
          ON cm.id = COALESCE(tmo.canonical_merchant_id, mrm.canonical_merchant_id)
        WHERE rt.id = ANY(%s::uuid[])
          AND cm.merged_into_id IS NULL
    """, (ids,))
    merchant_ids = [str(r[0]) for r in cur.fetchall()]

    if not merchant_ids:
        return {"ok": True, "affected": 0, "merchant_ids": []}

    cur.execute("""
        UPDATE canonical_merchant
        SET display_name = %s
        WHERE id = ANY(%s::uuid[])
    """, (body.display_name, merchant_ids))
    affected = cur.rowcount

    # Auto-merge if multiple distinct merchants got the same display name
    merged_count = 0
    if len(merchant_ids) > 1:
        from src.categorisation.merger import merge
        surviving_id = merchant_ids[0]
        for secondary_id in merchant_ids[1:]:
            try:
                merge(conn, secondary_id=secondary_id, surviving_id=surviving_id)
                merged_count += 1
            except ValueError:
                pass

    conn.commit()
    return {"ok": True, "affected": affected, "merchant_ids": merchant_ids, "merged": merged_count}


@router.post("/transactions/bulk/tags/add")
def bulk_add_tags(body: BulkTagAdd, conn=Depends(get_conn), user: CurrentUser = Depends(require_admin)):
    """Add tag(s) to multiple transactions."""
    cur = conn.cursor()
    ids = [str(tid) for tid in body.transaction_ids]
    tags = [t.strip() for t in body.tags if t.strip()]
    if not ids or not tags:
        return {"ok": True, "affected": 0}

    cur.execute("""
        INSERT INTO transaction_tag (raw_transaction_id, tag, source)
        SELECT t_id, t_tag, 'user'
        FROM unnest(%s::uuid[]) AS t_id
        CROSS JOIN unnest(%s::text[]) AS t_tag
        ON CONFLICT (raw_transaction_id, tag) DO NOTHING
    """, (ids, tags))
    affected = cur.rowcount

    conn.commit()
    return {"ok": True, "affected": affected}


@router.post("/transactions/bulk/tags/remove")
def bulk_remove_tag(body: BulkTagRemove, conn=Depends(get_conn), user: CurrentUser = Depends(require_admin)):
    """Remove a single tag from multiple transactions."""
    cur = conn.cursor()
    ids = [str(tid) for tid in body.transaction_ids]
    tag = body.tag.strip()
    if not ids or not tag:
        return {"ok": True, "affected": 0}

    cur.execute("""
        DELETE FROM transaction_tag
        WHERE raw_transaction_id = ANY(%s::uuid[]) AND tag = %s
    """, (ids, tag))
    affected = cur.rowcount

    conn.commit()
    return {"ok": True, "affected": affected}


@router.post("/transactions/bulk/tags/replace")
def bulk_replace_tags(body: BulkTagReplace, conn=Depends(get_conn), user: CurrentUser = Depends(require_admin)):
    """Replace all tags on selected transactions with a new set."""
    cur = conn.cursor()
    ids = [str(tid) for tid in body.transaction_ids]
    tags = [t.strip() for t in body.tags if t.strip()]
    if not ids:
        return {"ok": True, "affected": 0, "removed": 0}

    # Delete all existing tags
    cur.execute("""
        DELETE FROM transaction_tag
        WHERE raw_transaction_id = ANY(%s::uuid[])
    """, (ids,))
    removed = cur.rowcount

    added = 0
    if tags:
        cur.execute("""
            INSERT INTO transaction_tag (raw_transaction_id, tag, source)
            SELECT t_id, t_tag, 'user'
            FROM unnest(%s::uuid[]) AS t_id
            CROSS JOIN unnest(%s::text[]) AS t_tag
            ON CONFLICT (raw_transaction_id, tag) DO NOTHING
        """, (ids, tags))
        added = cur.rowcount

    conn.commit()
    return {"ok": True, "affected": added, "removed": removed}


@router.post("/transactions/bulk/note")
def bulk_update_note(body: BulkNoteUpdate, conn=Depends(get_conn), user: CurrentUser = Depends(require_admin)):
    """Bulk set or append notes on multiple transactions."""
    cur = conn.cursor()
    ids = [str(tid) for tid in body.transaction_ids]
    note_text = body.note.strip()
    mode = body.mode  # "replace" or "append"

    if not ids:
        return {"ok": True, "affected": 0}

    if not note_text and mode == "replace":
        # Delete notes
        cur.execute("""
            DELETE FROM transaction_note
            WHERE raw_transaction_id = ANY(%s::uuid[])
        """, (ids,))
        affected = cur.rowcount
    elif mode == "append":
        # Append: for existing notes, concat with newline; for missing, insert
        cur.execute("""
            INSERT INTO transaction_note (raw_transaction_id, note, source)
            SELECT unnest(%s::uuid[]), %s, 'user'
            ON CONFLICT (raw_transaction_id)
            DO UPDATE SET note = transaction_note.note || E'\n' || EXCLUDED.note,
                          source = 'user', updated_at = now()
        """, (ids, note_text))
        affected = cur.rowcount
    else:
        # Replace
        cur.execute("""
            INSERT INTO transaction_note (raw_transaction_id, note, source)
            SELECT unnest(%s::uuid[]), %s, 'user'
            ON CONFLICT (raw_transaction_id)
            DO UPDATE SET note = EXCLUDED.note,
                          source = 'user', updated_at = now()
        """, (ids, note_text))
        affected = cur.rowcount

    conn.commit()
    return {"ok": True, "affected": affected}
