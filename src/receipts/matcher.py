"""Auto-matching receipts to transactions by date, amount, currency, and merchant."""

import logging
import re
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

log = logging.getLogger(__name__)

# Trivial tokens excluded from merchant name comparison
_TRIVIAL_TOKENS = frozenset({
    "ltd", "limited", "the", "inc", "plc", "llc", "co", "company",
    "uk", "us", "gb", "group", "services", "international", "holdings",
    "and", "of", "for", "in", "at",
})


def get_match_tolerance(conn) -> int:
    """Get date tolerance from app_setting (default: 7 days)."""
    cur = conn.cursor()
    cur.execute("SELECT value FROM app_setting WHERE key = 'receipt.match_date_tolerance'")
    row = cur.fetchone()
    return int(row[0]) if row else 7


def get_amount_tolerance_pct(conn) -> int:
    """Get amount tolerance percentage from app_setting (default: 20%).

    This allows matching when amounts differ, e.g. due to tips.
    A receipt for £25.00 with 20% tolerance matches transactions £20.00-£30.00.
    """
    cur = conn.cursor()
    cur.execute("SELECT value FROM app_setting WHERE key = 'receipt.amount_tolerance_pct'")
    row = cur.fetchone()
    return int(row[0]) if row else 20


# ── Merchant name matching helpers ───────────────────────────────────────────


def _tokenize_merchant(name: str) -> set[str]:
    """Tokenize a merchant name for comparison, excluding trivial words."""
    if not name:
        return set()
    tokens = set(re.split(r'[\s/\-_.,*()]+', name.lower()))
    return tokens - _TRIVIAL_TOKENS - {''}


def _merchant_similarity(receipt_merchant: str, txn_merchant: str) -> float:
    """Token overlap between receipt merchant and transaction merchant.

    Returns score 0.0-1.0: (overlapping tokens) / (receipt tokens).
    """
    receipt_tokens = _tokenize_merchant(receipt_merchant)
    txn_tokens = _tokenize_merchant(txn_merchant)

    if not receipt_tokens:
        return 0.0

    overlap = receipt_tokens & txn_tokens
    return len(overlap) / len(receipt_tokens)


def _fetch_candidate_merchant_names(conn, candidate_ids: list[str]) -> dict[str, list[str]]:
    """Fetch effective merchant names for candidate transactions.

    Returns dict: transaction_id -> [raw_merchant, effective_canonical_name].
    """
    if not candidate_ids:
        return {}

    cur = conn.cursor()
    cur.execute("""
        SELECT
            rt.id,
            rt.raw_merchant,
            COALESCE(cm_override.display_name, cm_override.name,
                     cm.display_name, cm.name) AS effective_merchant
        FROM active_transaction rt
        LEFT JOIN cleaned_transaction ct ON ct.raw_transaction_id = rt.id
        LEFT JOIN merchant_raw_mapping mrm ON mrm.cleaned_merchant = ct.cleaned_merchant
        LEFT JOIN canonical_merchant cm ON cm.id = mrm.canonical_merchant_id
        LEFT JOIN transaction_merchant_override tmo ON tmo.raw_transaction_id = rt.id
        LEFT JOIN canonical_merchant cm_override ON cm_override.id = tmo.canonical_merchant_id
        WHERE rt.id = ANY(%s::uuid[])
    """, (candidate_ids,))

    result: dict[str, list[str]] = {}
    for row in cur.fetchall():
        names = []
        if row[1]:
            names.append(row[1])
        if row[2] and row[2] != row[1]:
            names.append(row[2])
        result[str(row[0])] = names

    return result


def _best_merchant_score(
    extracted_merchant: str, candidate_names: list[str]
) -> float:
    """Best similarity score across all merchant names for a candidate."""
    if not extracted_merchant or not candidate_names:
        return 0.0
    return max(_merchant_similarity(extracted_merchant, n) for n in candidate_names)


# ── Auto-matching ────────────────────────────────────────────────────────────


def auto_match_receipt(conn, receipt_id: UUID) -> dict | None:
    """Attempt to auto-match a receipt to a transaction.

    Returns match info dict if matched, None if no match found.
    """
    cur = conn.cursor()

    # Load receipt's extracted fields
    cur.execute("""
        SELECT extracted_date, extracted_amount, extracted_currency, extracted_merchant
        FROM receipt
        WHERE id = %s
    """, (str(receipt_id),))
    row = cur.fetchone()
    if not row:
        return None

    extracted_date, extracted_amount, extracted_currency, extracted_merchant = row

    # Can't auto-match without date + amount
    if extracted_date is None or extracted_amount is None:
        cur.execute("""
            UPDATE receipt
            SET match_status = 'pending_match', updated_at = now()
            WHERE id = %s
        """, (str(receipt_id),))
        conn.commit()
        return None

    tolerance = get_match_tolerance(conn)
    amount_tolerance_pct = get_amount_tolerance_pct(conn)

    date_from = extracted_date - timedelta(days=tolerance)
    date_to = extracted_date + timedelta(days=tolerance)

    # Amount range: allow tolerance for tips etc.
    amt = float(extracted_amount)
    if amount_tolerance_pct > 0:
        factor = amount_tolerance_pct / 100.0
        amount_min = Decimal(str(round(amt * (1 - factor), 4)))
        amount_max = Decimal(str(round(amt * (1 + factor), 4)))
        amount_condition = "ABS(at.amount) BETWEEN %s AND %s"
        amount_params = [str(amount_min), str(amount_max)]
    else:
        amount_condition = "ABS(at.amount) = %s"
        amount_params = [str(extracted_amount)]

    # Build currency filter
    currency_filter = ""
    currency_params: list[str] = []
    if extracted_currency:
        currency_filter = "AND TRIM(at.currency) = %s"
        currency_params = [extracted_currency.strip()]

    # Find candidates: match amount range, date range, currency
    # Exclude transactions already matched to another receipt
    cur.execute(f"""
        SELECT at.id, at.posted_at, at.amount, at.currency,
               at.raw_merchant, at.institution, at.account_ref
        FROM active_transaction at
        WHERE {amount_condition}
          AND at.posted_at BETWEEN %s AND %s
          {currency_filter}
          AND NOT EXISTS (
              SELECT 1 FROM receipt r2
              WHERE r2.matched_transaction_id = at.id
                AND r2.id != %s
          )
        ORDER BY ABS(ABS(at.amount) - %s), ABS(at.posted_at - %s::date)
        LIMIT 10
    """, (*amount_params, date_from, date_to,
          *currency_params,
          str(receipt_id), str(extracted_amount), extracted_date))

    candidates = cur.fetchall()
    merchant_names: dict[str, list[str]] = {}

    if not candidates:
        cur.execute("""
            UPDATE receipt
            SET match_status = 'pending_match', updated_at = now()
            WHERE id = %s
        """, (str(receipt_id),))
        conn.commit()
        log.info("Receipt %s: no candidates found", receipt_id)
        return None

    # Step 1: If multiple candidates, prefer exact amount matches
    if len(candidates) > 1:
        exact = [c for c in candidates if abs(abs(float(c[2])) - amt) < 0.005]
        if len(exact) == 1:
            candidates = exact
            log.info("Receipt %s: narrowed to 1 exact amount match",
                     receipt_id)
        elif len(exact) > 1:
            # Multiple exact matches — narrow pool to just these
            candidates = exact

    # Step 2: If still multiple, try merchant name matching
    if len(candidates) > 1 and extracted_merchant:
        candidate_ids = [str(c[0]) for c in candidates]
        merchant_names = _fetch_candidate_merchant_names(conn, candidate_ids)

        scored = []
        for c in candidates:
            cid = str(c[0])
            names = merchant_names.get(cid, [])
            best_sim = _best_merchant_score(extracted_merchant, names)
            scored.append((c, best_sim))

        # Filter to candidates with strong merchant match (>= 50% token overlap)
        strong = [(c, sim) for c, sim in scored if sim >= 0.50]

        if len(strong) == 1:
            candidates = [strong[0][0]]
            log.info("Receipt %s: narrowed to 1 candidate via merchant match "
                     "(similarity: %.2f)", receipt_id, strong[0][1])
        elif len(strong) > 1:
            # Multiple strong matches — keep just those (better than all)
            candidates = [c for c, sim in strong]
            log.info("Receipt %s: %d candidates with strong merchant match, "
                     "still ambiguous", receipt_id, len(strong))

    if len(candidates) == 1:
        # Unambiguous match (or narrowed to one)
        cand = candidates[0]
        cand_id, cand_date, cand_amount = cand[0], cand[1], cand[2]

        # Confidence based on date distance + amount distance + merchant
        day_diff = abs((cand_date - extracted_date).days)
        amount_diff_pct = abs(float(cand_amount) - amt) / amt * 100 if amt else 0

        base_confidence = 1.0

        # Penalise date distance (graduated for wider window)
        if day_diff <= 1:
            base_confidence -= 0.05 * day_diff
        elif day_diff <= 3:
            base_confidence -= 0.10
        elif day_diff <= 5:
            base_confidence -= 0.15
        else:
            base_confidence -= 0.20

        # Penalise amount distance
        if amount_diff_pct > 0:
            base_confidence -= min(amount_diff_pct / 100, 0.20)

        # Merchant name bonus
        if extracted_merchant:
            cid = str(cand_id)
            if cid not in merchant_names:
                merchant_names = _fetch_candidate_merchant_names(conn, [cid])
            names = merchant_names.get(cid, [])
            best_sim = _best_merchant_score(extracted_merchant, names)
            if best_sim >= 0.50:
                base_confidence += 0.10
            elif best_sim >= 0.25:
                base_confidence += 0.05

        confidence = Decimal(str(round(max(min(base_confidence, 1.0), 0.50), 2)))

        cur.execute("""
            UPDATE receipt
            SET match_status = 'auto_matched',
                matched_transaction_id = %s,
                match_confidence = %s,
                matched_at = now(),
                matched_by = 'auto',
                updated_at = now()
            WHERE id = %s
        """, (str(cand_id), str(confidence), str(receipt_id)))
        conn.commit()

        log.info("Auto-matched receipt %s to transaction %s (confidence: %s)",
                 receipt_id, cand_id, confidence)

        return {
            "matched": True,
            "transaction_id": str(cand_id),
            "confidence": str(confidence),
        }

    # 2+ candidates with no clear winner — leave for manual resolution
    cur.execute("""
        UPDATE receipt
        SET match_status = 'pending_match', updated_at = now()
        WHERE id = %s
    """, (str(receipt_id),))
    conn.commit()

    log.info("Receipt %s: %d candidates found, left as pending_match",
             receipt_id, len(candidates))
    return None


def find_match_candidates(conn, receipt_id: UUID, limit: int = 20) -> list[dict]:
    """Find potential transaction matches for manual matching UI.

    Returns candidate transactions ordered by date proximity + amount closeness.
    Uses wider tolerance than auto-match.
    """
    cur = conn.cursor()

    cur.execute("""
        SELECT extracted_date, extracted_amount, extracted_currency
        FROM receipt
        WHERE id = %s
    """, (str(receipt_id),))
    row = cur.fetchone()
    if not row:
        return []

    extracted_date, extracted_amount, extracted_currency = row

    # Wider search window for manual matching
    tolerance = 14
    params: list = []
    conditions = []

    if extracted_date:
        date_from = extracted_date - timedelta(days=tolerance)
        date_to = extracted_date + timedelta(days=tolerance)
        conditions.append("at.posted_at BETWEEN %s AND %s")
        params.extend([date_from, date_to])

    if extracted_amount:
        # 50% tolerance for manual candidates — show wider range
        amt = float(extracted_amount)
        amount_min = Decimal(str(round(amt * 0.5, 4)))
        amount_max = Decimal(str(round(amt * 1.5, 4)))
        conditions.append("ABS(at.amount) BETWEEN %s AND %s")
        params.extend([str(amount_min), str(amount_max)])

    if extracted_currency:
        conditions.append("TRIM(at.currency) = %s")
        params.append(extracted_currency.strip())

    if not conditions:
        return []

    where = " AND ".join(conditions)

    order = "at.posted_at DESC"

    cur.execute(f"""
        SELECT at.id, at.posted_at, at.amount, at.currency,
               at.raw_merchant, at.institution, at.account_ref
        FROM active_transaction at
        WHERE {where}
        ORDER BY {order}
        LIMIT %s
    """, (*params, limit))

    candidates = []
    for row in cur.fetchall():
        candidates.append({
            "id": str(row[0]),
            "posted_at": str(row[1]),
            "amount": str(row[2]),
            "currency": row[3].strip() if row[3] else None,
            "raw_merchant": row[4],
            "institution": row[5],
            "account_ref": row[6],
        })

    return candidates
