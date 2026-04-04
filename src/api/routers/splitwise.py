"""Splitwise sync UI — incoming matching and outgoing push."""

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.api.deps import CurrentUser, get_conn, get_current_user
from src.ingestion.splitwise import (
    create_expense,
    fetch_expenses,
    get_current_user as sw_get_current_user,
    get_expense,
    get_group,
    get_groups,
    get_original_currency,
    get_user_share,
    map_finance_category,
)

log = logging.getLogger(__name__)
router = APIRouter()


# ── Models ───────────────────────────────────────────────────────────────────


class LinkRequest(BaseModel):
    transaction_id: UUID


class PushRequest(BaseModel):
    group_id: int
    member_ids: list[int]


# ── Incoming ─────────────────────────────────────────────────────────────────


@router.get("/splitwise/incoming")
def list_incoming(
    since: date | None = Query(None),
    show_all: bool = Query(False),
    show_ignored: bool = Query(False),
    conn=Depends(get_conn),
    _user: CurrentUser = Depends(get_current_user),
):
    """Fetch unsynced Splitwise expenses the user paid."""
    sw_user = sw_get_current_user()
    user_id = sw_user["id"]

    if show_all:
        dated_after = None
    else:
        dated_after = datetime.combine(
            since or (date.today() - timedelta(days=90)),
            datetime.min.time(),
            tzinfo=timezone.utc,
        )
    expenses = fetch_expenses(dated_after=dated_after)

    # Filter to expenses user paid
    user_expenses = []
    for e in expenses:
        share = get_user_share(e, user_id)
        if share is not None:
            user_expenses.append(e)

    if not user_expenses:
        return []

    # Batch-check which are already synced
    expense_ids = [e["id"] for e in user_expenses]
    cur = conn.cursor()
    cur.execute(
        "SELECT splitwise_expense_id, dismissed, permanent FROM splitwise_sync_log "
        "WHERE splitwise_expense_id = ANY(%s)",
        (expense_ids,),
    )
    synced = {}
    for row in cur.fetchall():
        synced[row[0]] = {"dismissed": row[1], "permanent": row[2]}

    results = []
    for e in user_expenses:
        eid = e["id"]
        if eid in synced:
            entry = synced[eid]
            if entry["dismissed"]:
                if not show_ignored:
                    continue
                status = "ignored"
            else:
                continue  # already linked, skip
        else:
            status = "pending"

        group_id = e.get("group_id", 0)
        results.append({
            "id": eid,
            "date": e.get("date", "")[:10],
            "cost": e.get("cost"),
            "currency_code": e.get("currency_code"),
            "description": e.get("description", ""),
            "group_id": group_id,
            "group_name": None,  # populated below if needed
            "status": status,
        })

    return results


@router.get("/splitwise/incoming/{expense_id}/candidates")
def get_candidates(
    expense_id: int,
    conn=Depends(get_conn),
    _user: CurrentUser = Depends(get_current_user),
):
    """Find candidate finance transactions to link to a Splitwise expense."""
    full_expense = get_expense(expense_id)
    if not full_expense:
        raise HTTPException(404, "Splitwise expense not found")

    cost = Decimal(full_expense.get("cost", "0"))
    currency = full_expense.get("currency_code", "GBP")
    expense_date = full_expense.get("date", "")[:10]

    # Check for original currency from conversion comments
    original = get_original_currency(full_expense)

    # Build candidate queries with ±10% tolerance and ±3 days
    tolerance = cost * Decimal("0.10")
    low = cost - tolerance
    high = cost + tolerance

    cur = conn.cursor()

    # Query 1: direct currency match with fuzzy amount
    cur.execute("""
        SELECT rt.id, rt.posted_at, rt.amount, rt.currency, rt.raw_merchant,
               rt.institution, ssl.id IS NOT NULL AS already_linked
        FROM active_transaction rt
        LEFT JOIN splitwise_sync_log ssl ON ssl.raw_transaction_id = rt.id
        WHERE rt.posted_at BETWEEN (%s::date - interval '3 days') AND (%s::date + interval '3 days')
          AND rt.institution != 'splitwise'
          AND rt.currency = %s
          AND abs(rt.amount) BETWEEN %s AND %s
        ORDER BY abs(abs(rt.amount) - %s) ASC, abs(rt.posted_at - %s::date) ASC
        LIMIT 10
    """, (expense_date, expense_date, currency, low, high, cost, expense_date))

    candidates = []
    for row in cur.fetchall():
        candidates.append({
            "id": str(row[0]),
            "date": str(row[1]),
            "amount": str(row[2]),
            "currency": row[3].strip(),
            "merchant": (row[4] or "").strip(),
            "institution": row[5],
            "already_linked": row[6],
        })

    # Query 2: local currency match (Monzo raw_data)
    cur.execute("""
        SELECT rt.id, rt.posted_at, rt.amount, rt.currency, rt.raw_merchant,
               rt.institution, ssl.id IS NOT NULL AS already_linked
        FROM active_transaction rt
        LEFT JOIN splitwise_sync_log ssl ON ssl.raw_transaction_id = rt.id
        WHERE rt.posted_at BETWEEN (%s::date - interval '3 days') AND (%s::date + interval '3 days')
          AND rt.institution != 'splitwise'
          AND rt.raw_data->>'local_currency' = %s
          AND abs((rt.raw_data->>'local_amount')::numeric) BETWEEN %s * 100 AND %s * 100
        ORDER BY abs(abs((rt.raw_data->>'local_amount')::numeric / 100) - %s) ASC
        LIMIT 10
    """, (expense_date, expense_date, currency, low, high, cost))

    seen_ids = {c["id"] for c in candidates}
    for row in cur.fetchall():
        tid = str(row[0])
        if tid not in seen_ids:
            candidates.append({
                "id": tid,
                "date": str(row[1]),
                "amount": str(row[2]),
                "currency": row[3].strip(),
                "merchant": (row[4] or "").strip(),
                "institution": row[5],
                "already_linked": row[6],
                "matched_via": "local_currency",
            })
            seen_ids.add(tid)

    # Query 3: if original currency known, try matching on that too
    if original:
        orig_ccy, orig_amt_str = original
        orig_cost = Decimal(orig_amt_str)
        orig_tol = orig_cost * Decimal("0.10")
        orig_low = orig_cost - orig_tol
        orig_high = orig_cost + orig_tol

        for q_currency, q_low, q_high, q_cost, match_via in [
            (orig_ccy, orig_low, orig_high, orig_cost, "original_currency"),
        ]:
            cur.execute("""
                SELECT rt.id, rt.posted_at, rt.amount, rt.currency, rt.raw_merchant,
                       rt.institution, ssl.id IS NOT NULL AS already_linked
                FROM active_transaction rt
                LEFT JOIN splitwise_sync_log ssl ON ssl.raw_transaction_id = rt.id
                WHERE rt.posted_at BETWEEN (%s::date - interval '3 days') AND (%s::date + interval '3 days')
                  AND rt.institution != 'splitwise'
                  AND (
                      (rt.currency = %s AND abs(rt.amount) BETWEEN %s AND %s)
                      OR (
                          rt.raw_data->>'local_currency' = %s
                          AND abs((rt.raw_data->>'local_amount')::numeric) BETWEEN %s * 100 AND %s * 100
                      )
                  )
                ORDER BY abs(abs(rt.amount) - %s) ASC
                LIMIT 10
            """, (expense_date, expense_date,
                  q_currency, q_low, q_high,
                  q_currency, q_low, q_high,
                  q_cost))

            for row in cur.fetchall():
                tid = str(row[0])
                if tid not in seen_ids:
                    candidates.append({
                        "id": tid,
                        "date": str(row[1]),
                        "amount": str(row[2]),
                        "currency": row[3].strip(),
                        "merchant": (row[4] or "").strip(),
                        "institution": row[5],
                        "already_linked": row[6],
                        "matched_via": match_via,
                    })
                    seen_ids.add(tid)

    return {
        "expense": {
            "id": expense_id,
            "date": expense_date,
            "cost": str(cost),
            "currency_code": currency,
            "description": full_expense.get("description", ""),
            "details": full_expense.get("details"),
            "original_currency": f"{original[0]} {original[1]}" if original else None,
        },
        "candidates": candidates,
    }


@router.post("/splitwise/incoming/{expense_id}/link")
def link_expense(
    expense_id: int,
    body: LinkRequest,
    conn=Depends(get_conn),
    _user: CurrentUser = Depends(get_current_user),
):
    """Link a Splitwise expense to a finance transaction."""
    cur = conn.cursor()

    # Check expense not already synced
    cur.execute(
        "SELECT id FROM splitwise_sync_log WHERE splitwise_expense_id = %s AND NOT dismissed",
        (expense_id,),
    )
    if cur.fetchone():
        raise HTTPException(409, "Expense already linked")

    # Remove any dismissed entry for this expense
    cur.execute(
        "DELETE FROM splitwise_sync_log WHERE splitwise_expense_id = %s AND dismissed",
        (expense_id,),
    )

    # Add splitwise tag
    cur.execute("""
        INSERT INTO transaction_tag (raw_transaction_id, tag)
        VALUES (%s, 'splitwise')
        ON CONFLICT DO NOTHING
    """, (str(body.transaction_id),))

    # Create sync log entry
    cur.execute("""
        INSERT INTO splitwise_sync_log (raw_transaction_id, splitwise_expense_id, direction)
        VALUES (%s, %s, 'pull')
    """, (str(body.transaction_id), expense_id))

    conn.commit()
    return {"ok": True}


@router.post("/splitwise/incoming/{expense_id}/ignore")
def ignore_expense(
    expense_id: int,
    conn=Depends(get_conn),
    _user: CurrentUser = Depends(get_current_user),
):
    """Permanently ignore a Splitwise expense."""
    cur = conn.cursor()

    cur.execute(
        "SELECT id FROM splitwise_sync_log WHERE splitwise_expense_id = %s AND NOT dismissed",
        (expense_id,),
    )
    if cur.fetchone():
        raise HTTPException(409, "Expense already synced")

    # Upsert — replace any existing dismissed entry
    cur.execute("""
        INSERT INTO splitwise_sync_log (splitwise_expense_id, direction, dismissed, permanent)
        VALUES (%s, 'pull', true, true)
        ON CONFLICT (splitwise_expense_id) DO UPDATE SET dismissed = true, permanent = true
    """, (expense_id,))
    conn.commit()
    return {"ok": True}


@router.delete("/splitwise/incoming/{expense_id}/ignore")
def unignore_expense(
    expense_id: int,
    conn=Depends(get_conn),
    _user: CurrentUser = Depends(get_current_user),
):
    """Restore a previously ignored Splitwise expense."""
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM splitwise_sync_log WHERE splitwise_expense_id = %s AND dismissed",
        (expense_id,),
    )
    if cur.rowcount == 0:
        raise HTTPException(404, "No dismissed entry found")
    conn.commit()
    return {"ok": True}


# ── Outgoing ─────────────────────────────────────────────────────────────────


@router.get("/splitwise/outgoing")
def list_outgoing(
    conn=Depends(get_conn),
    _user: CurrentUser = Depends(get_current_user),
):
    """Finance transactions tagged 'splitwise' without a sync log entry."""
    cur = conn.cursor()
    cur.execute("""
        SELECT
            rt.id, rt.posted_at, rt.amount, rt.currency,
            rt.raw_merchant, rt.institution,
            COALESCE(cm_override.display_name, cm_override.name,
                     cm.display_name, cm.name, rt.raw_merchant) AS merchant_name,
            COALESCE(tcat.full_path, cat_override.full_path, cat.full_path) AS category_path,
            tn.note
        FROM active_transaction rt
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
        JOIN transaction_tag tt
            ON tt.raw_transaction_id = rt.id AND lower(tt.tag) = 'splitwise'
        LEFT JOIN splitwise_sync_log ssl ON ssl.raw_transaction_id = rt.id
        WHERE ssl.id IS NULL
        ORDER BY rt.posted_at DESC, rt.id
    """)

    columns = [desc[0] for desc in cur.description]
    results = []
    for row in cur.fetchall():
        r = dict(zip(columns, row))
        results.append({
            "id": str(r["id"]),
            "date": str(r["posted_at"]),
            "amount": str(r["amount"]),
            "currency": r["currency"].strip(),
            "raw_merchant": (r["raw_merchant"] or "").strip(),
            "merchant_name": (r["merchant_name"] or "").strip(),
            "category_path": r["category_path"],
            "institution": r["institution"],
            "note": r["note"],
        })
    return results


@router.get("/splitwise/groups")
def list_groups(
    _user: CurrentUser = Depends(get_current_user),
):
    """List Splitwise groups with members."""
    sw_user = sw_get_current_user()
    groups = get_groups()

    results = []
    for g in groups:
        # Skip settled groups (no outstanding debts)
        debts = g.get("simplified_debts") or g.get("original_debts") or []
        if not debts and g.get("id") != 0:
            continue

        members = []
        for m in g.get("members", []):
            members.append({
                "id": m.get("id"),
                "name": f"{m.get('first_name', '')} {m.get('last_name', '') or ''}".strip(),
            })
        results.append({
            "id": g["id"],
            "name": g.get("name", ""),
            "members": members,
            "created_at": g.get("created_at", ""),
        })

    # Sort by created_at descending (most recent first)
    results.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    return {"user_id": sw_user["id"], "groups": results}


@router.post("/splitwise/outgoing/{transaction_id}/push")
def push_expense(
    transaction_id: UUID,
    body: PushRequest,
    conn=Depends(get_conn),
    _user: CurrentUser = Depends(get_current_user),
):
    """Push a finance transaction to Splitwise."""
    cur = conn.cursor()

    # Check not already pushed
    cur.execute(
        "SELECT id FROM splitwise_sync_log WHERE raw_transaction_id = %s",
        (str(transaction_id),),
    )
    if cur.fetchone():
        raise HTTPException(409, "Transaction already synced")

    # Fetch transaction details
    cur.execute("""
        SELECT
            rt.posted_at, rt.amount, rt.currency, rt.raw_merchant, rt.raw_memo,
            COALESCE(cm_override.display_name, cm_override.name,
                     cm.display_name, cm.name, rt.raw_merchant) AS merchant_name,
            COALESCE(tcat.full_path, cat_override.full_path, cat.full_path) AS category_path,
            tn.note
        FROM active_transaction rt
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

    posted_at, amount, currency, raw_merchant, raw_memo, merchant_name, category_path, note = row

    cost_str = f"{abs(float(amount)):.2f}"
    merchant = (merchant_name or raw_merchant or "Unknown").strip()
    category_id = map_finance_category(category_path)
    txn_date = posted_at.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Build details from raw_memo and note
    detail_parts = []
    if raw_memo:
        detail_parts.append(str(raw_memo).strip())
    if note:
        detail_parts.append(str(note))
    details = " | ".join(detail_parts) if detail_parts else None

    # Get payer user ID
    sw_user = sw_get_current_user()
    payer_user_id = sw_user["id"]

    # Build splits for selected members
    member_count = len(body.member_ids)
    per_person = abs(float(amount)) / member_count
    per_person_str = f"{per_person:.2f}"

    splits = []
    for mid in body.member_ids:
        splits.append({"user_id": mid, "owed_share": per_person_str})

    created = create_expense(
        cost=cost_str,
        description=merchant,
        date=txn_date,
        currency_code=currency.strip(),
        category_id=category_id,
        group_id=body.group_id,
        payer_user_id=payer_user_id,
        splits=splits,
        details=details,
    )

    sw_expense_id = created["id"]

    cur.execute("""
        INSERT INTO splitwise_sync_log (raw_transaction_id, splitwise_expense_id, direction)
        VALUES (%s, %s, 'push')
    """, (str(transaction_id), sw_expense_id))
    conn.commit()

    return {"ok": True, "splitwise_expense_id": sw_expense_id}
