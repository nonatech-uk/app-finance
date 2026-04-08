"""PayPal transaction cache — search, match, and unmatch endpoints."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.deps import CurrentUser, get_conn, get_current_user
from src.api.models import PayPalMatchCreate, PayPalMatchItem, PayPalTransaction

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/paypal/search", response_model=list[PayPalTransaction])
def search_paypal_transactions(
    q: str | None = Query(None),
    transaction_type: str | None = Query(None, alias="type"),
    limit: int = Query(20, le=100),
    conn=Depends(get_conn),
    _user: CurrentUser = Depends(get_current_user),
):
    """Search cached PayPal transactions."""
    where_clauses = []
    params: list = []

    if q:
        where_clauses.append("to_tsvector('english', description) @@ plainto_tsquery('english', %s)")
        params.append(q)
    if transaction_type:
        where_clauses.append("transaction_type = %s")
        params.append(transaction_type)

    if not where_clauses:
        raise HTTPException(400, "Provide q or type parameter")

    where = " AND ".join(where_clauses)
    cur = conn.cursor()
    cur.execute(f"""
        SELECT id, paypal_transaction_id, paypal_order_id, transaction_type,
               description, amount, fee, net_amount, currency, counterparty,
               counterparty_email, transaction_date, status
        FROM paypal_transaction
        WHERE {where}
        ORDER BY transaction_date DESC
        LIMIT %s
    """, tuple(params + [limit]))

    return [
        PayPalTransaction(
            id=r[0], paypal_transaction_id=r[1], paypal_order_id=r[2],
            transaction_type=r[3], description=r[4],
            amount=float(r[5]) if r[5] else None,
            fee=float(r[6]) if r[6] else None,
            net_amount=float(r[7]) if r[7] else None,
            currency=r[8], counterparty=r[9], counterparty_email=r[10],
            transaction_date=str(r[11]) if r[11] else None, status=r[12],
        )
        for r in cur.fetchall()
    ]


@router.post("/paypal/match", response_model=PayPalMatchItem, status_code=201)
def match_paypal_transaction(
    body: PayPalMatchCreate,
    conn=Depends(get_conn),
    _user: CurrentUser = Depends(get_current_user),
):
    """Link a PayPal transaction to a raw_transaction."""
    cur = conn.cursor()

    # Verify both exist
    cur.execute("SELECT id FROM paypal_transaction WHERE id = %s", (str(body.paypal_transaction_id),))
    if not cur.fetchone():
        raise HTTPException(404, "PayPal transaction not found")

    cur.execute("SELECT id FROM raw_transaction WHERE id = %s", (str(body.raw_transaction_id),))
    if not cur.fetchone():
        raise HTTPException(404, "Raw transaction not found")

    cur.execute("""
        INSERT INTO paypal_transaction_match (paypal_transaction_id, raw_transaction_id)
        VALUES (%s, %s)
        ON CONFLICT DO NOTHING
        RETURNING id, paypal_transaction_id, raw_transaction_id, match_confidence, matched_at
    """, (str(body.paypal_transaction_id), str(body.raw_transaction_id)))
    conn.commit()
    r = cur.fetchone()
    if not r:
        raise HTTPException(409, "Match already exists")

    return PayPalMatchItem(
        id=r[0], paypal_transaction_id=r[1], raw_transaction_id=r[2],
        match_confidence=float(r[3]) if r[3] else None, matched_at=str(r[4]),
    )


@router.delete("/paypal/match/{match_id}", status_code=204)
def unmatch_paypal_transaction(
    match_id: str,
    conn=Depends(get_conn),
    _user: CurrentUser = Depends(get_current_user),
):
    cur = conn.cursor()
    cur.execute("DELETE FROM paypal_transaction_match WHERE id = %s", (match_id,))
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "PayPal match not found")
