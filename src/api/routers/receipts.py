"""Receipt management API — upload, OCR, match, serve files."""

import logging
import os
import shutil
from datetime import date
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from config.settings import settings
from src.api.deps import CurrentUser, get_conn, require_admin
from src.api.models import (
    ReceiptCandidate,
    ReceiptDetail,
    ReceiptItem,
    ReceiptList,
    ReceiptMatchRequest,
)

log = logging.getLogger(__name__)
router = APIRouter()

ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "application/pdf",
    "text/plain",
}

# Extension map for saving files
EXT_MAP = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
}


def _storage_root() -> Path:
    return Path(settings.receipt_storage_path)


def _ensure_dir(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)


def _make_thumbnail(src_path: Path, thumb_path: Path, mime_type: str):
    """Generate a thumbnail for image files. Skip for PDFs/text."""
    if not mime_type.startswith("image/"):
        return False

    try:
        from PIL import Image, ImageOps

        with Image.open(src_path) as img:
            # Apply EXIF orientation (phone cameras store rotation in metadata)
            img = ImageOps.exif_transpose(img)
            img.thumbnail((400, 400))
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            _ensure_dir(thumb_path)
            img.save(thumb_path, "JPEG", quality=80)
        return True
    except ImportError:
        log.warning("Pillow not installed — skipping thumbnail generation")
        return False
    except Exception as e:
        log.warning("Failed to create thumbnail: %s", e)
        return False


# ── Upload ───────────────────────────────────────────────────────────────────


@router.post("/receipts/upload", response_model=ReceiptDetail)
def upload_receipt(
    file: UploadFile = File(...),
    note: str = Form(None),
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    """Upload a receipt file, run OCR, and attempt auto-match."""
    if not file.content_type or file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            400,
            f"Unsupported file type: {file.content_type}. "
            f"Allowed: {', '.join(sorted(ALLOWED_MIME_TYPES))}",
        )

    # Read file
    file_bytes = file.file.read()
    if not file_bytes:
        raise HTTPException(400, "Empty file")

    file_size = len(file_bytes)
    mime_type = file.content_type
    original_filename = file.filename or "receipt"
    ext = EXT_MAP.get(mime_type, "")

    cur = conn.cursor()

    # Create receipt row first to get the ID
    cur.execute("""
        INSERT INTO receipt (
            original_filename, mime_type, file_size, file_path,
            source, uploaded_by, notes
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id, created_at
    """, (
        original_filename, mime_type, file_size,
        "pending",  # placeholder, updated after file save
        "web", user.email, note,
    ))
    row = cur.fetchone()
    receipt_id = row[0]
    created_at = row[1]

    # Save file to disk
    year_month = date.today().strftime("%Y/%m")
    rel_path = f"{year_month}/{receipt_id}{ext}"
    abs_path = _storage_root() / rel_path
    _ensure_dir(abs_path)
    abs_path.write_bytes(file_bytes)

    # Generate thumbnail
    thumb_rel = None
    thumb_path = _storage_root() / f"{year_month}/{receipt_id}_thumb.jpg"
    if _make_thumbnail(abs_path, thumb_path, mime_type):
        thumb_rel = f"{year_month}/{receipt_id}_thumb.jpg"

    # Update file path
    cur.execute("""
        UPDATE receipt
        SET file_path = %s, thumbnail_path = %s
        WHERE id = %s
    """, (rel_path, thumb_rel, str(receipt_id)))

    conn.commit()

    # Run OCR
    ocr_result = {}
    try:
        from src.receipts.ocr import extract_receipt_data
        from src.api.routers.settings import get_anthropic_api_key
        api_key = get_anthropic_api_key(conn)
        ocr_result = extract_receipt_data(str(abs_path), mime_type, api_key=api_key)

        if "error" in ocr_result:
            cur.execute("""
                UPDATE receipt
                SET ocr_status = 'failed',
                    ocr_data = %s,
                    match_status = 'pending_match',
                    updated_at = now()
                WHERE id = %s
            """, (
                __import__("json").dumps({"error": ocr_result["error"]}),
                str(receipt_id),
            ))
        else:
            import json
            cur.execute("""
                UPDATE receipt
                SET ocr_status = 'completed',
                    ocr_text = %s,
                    ocr_data = %s,
                    extracted_date = %s,
                    extracted_amount = %s,
                    extracted_currency = %s,
                    extracted_merchant = %s,
                    match_status = 'pending_match',
                    updated_at = now()
                WHERE id = %s
            """, (
                ocr_result.get("raw_text"),
                json.dumps(ocr_result),
                ocr_result.get("date"),
                ocr_result.get("amount"),
                ocr_result.get("currency"),
                ocr_result.get("merchant"),
                str(receipt_id),
            ))

        conn.commit()
    except Exception as e:
        log.exception("OCR failed for receipt %s", receipt_id)
        cur.execute("""
            UPDATE receipt
            SET ocr_status = 'failed',
                match_status = 'pending_match',
                updated_at = now()
            WHERE id = %s
        """, (str(receipt_id),))
        conn.commit()

    # Attempt auto-match
    match_result = None
    try:
        from src.receipts.matcher import auto_match_receipt
        match_result = auto_match_receipt(conn, receipt_id)
    except Exception as e:
        log.exception("Auto-match failed for receipt %s", receipt_id)

    # Return full detail
    return _load_receipt_detail(cur, receipt_id, conn)


# ── List / Queue ─────────────────────────────────────────────────────────────


@router.get("/receipts", response_model=ReceiptList)
def list_receipts(
    status: str = "all",
    limit: int = 50,
    offset: int = 0,
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    """List receipts with optional status filter."""
    cur = conn.cursor()

    where = ""
    params: list = []
    if status and status != "all":
        where = "WHERE match_status = %s"
        params.append(status)

    cur.execute(f"SELECT COUNT(*) FROM receipt {where}", params)
    total = cur.fetchone()[0]

    cur.execute(f"""
        SELECT id, original_filename, mime_type, file_size,
               ocr_status, extracted_date, extracted_amount, extracted_currency,
               extracted_merchant,
               match_status, matched_transaction_id, match_confidence,
               matched_at, matched_by,
               source, uploaded_at, uploaded_by, notes
        FROM receipt
        {where}
        ORDER BY uploaded_at DESC
        LIMIT %s OFFSET %s
    """, (*params, limit, offset))

    items = []
    for row in cur.fetchall():
        items.append(ReceiptItem(
            id=row[0],
            original_filename=row[1],
            mime_type=row[2],
            file_size=row[3],
            ocr_status=row[4],
            extracted_date=row[5],
            extracted_amount=row[6],
            extracted_currency=row[7].strip() if row[7] else None,
            extracted_merchant=row[8],
            match_status=row[9],
            matched_transaction_id=row[10],
            match_confidence=row[11],
            matched_at=row[12],
            matched_by=row[13],
            source=row[14],
            uploaded_at=row[15],
            uploaded_by=row[16],
            notes=row[17],
        ))

    return ReceiptList(items=items, total=total)


# ── Detail ───────────────────────────────────────────────────────────────────


@router.get("/receipts/{receipt_id}", response_model=ReceiptDetail)
def get_receipt(
    receipt_id: UUID,
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    """Get full receipt detail."""
    cur = conn.cursor()
    detail = _load_receipt_detail(cur, receipt_id, conn)
    if not detail:
        raise HTTPException(404, "Receipt not found")
    return detail


# ── File Serving ─────────────────────────────────────────────────────────────


@router.get("/receipts/{receipt_id}/file")
def serve_receipt_file(
    receipt_id: UUID,
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    """Stream the original receipt file."""
    cur = conn.cursor()
    cur.execute(
        "SELECT file_path, mime_type, original_filename FROM receipt WHERE id = %s",
        (str(receipt_id),),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Receipt not found")

    file_path = _storage_root() / row[0]
    if not file_path.exists():
        raise HTTPException(404, "File not found on disk")

    def iter_file():
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                yield chunk

    return StreamingResponse(
        iter_file(),
        media_type=row[1],
        headers={
            "Content-Disposition": f'inline; filename="{row[2]}"',
            "Content-Length": str(file_path.stat().st_size),
        },
    )


@router.get("/receipts/{receipt_id}/thumbnail")
def serve_receipt_thumbnail(
    receipt_id: UUID,
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    """Stream the receipt thumbnail (images only)."""
    cur = conn.cursor()
    cur.execute(
        "SELECT thumbnail_path, mime_type FROM receipt WHERE id = %s",
        (str(receipt_id),),
    )
    row = cur.fetchone()
    if not row or not row[0]:
        raise HTTPException(404, "Thumbnail not available")

    thumb_path = _storage_root() / row[0]
    if not thumb_path.exists():
        raise HTTPException(404, "Thumbnail file not found")

    def iter_file():
        with open(thumb_path, "rb") as f:
            while chunk := f.read(65536):
                yield chunk

    return StreamingResponse(
        iter_file(),
        media_type="image/jpeg",
    )


# ── Match / Unmatch ──────────────────────────────────────────────────────────


@router.post("/receipts/{receipt_id}/match")
def match_receipt(
    receipt_id: UUID,
    body: ReceiptMatchRequest,
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    """Manually match a receipt to a transaction."""
    cur = conn.cursor()

    # Verify receipt exists
    cur.execute("SELECT id FROM receipt WHERE id = %s", (str(receipt_id),))
    if not cur.fetchone():
        raise HTTPException(404, "Receipt not found")

    # Verify transaction exists
    cur.execute("SELECT id FROM raw_transaction WHERE id = %s", (str(body.transaction_id),))
    if not cur.fetchone():
        raise HTTPException(404, "Transaction not found")

    cur.execute("""
        UPDATE receipt
        SET match_status = 'manually_matched',
            matched_transaction_id = %s,
            match_confidence = 1.00,
            matched_at = now(),
            matched_by = %s,
            updated_at = now()
        WHERE id = %s
    """, (str(body.transaction_id), user.email, str(receipt_id)))

    conn.commit()
    return {"ok": True}


@router.post("/receipts/{receipt_id}/unmatch")
def unmatch_receipt(
    receipt_id: UUID,
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    """Remove a receipt's match, setting it back to pending."""
    cur = conn.cursor()

    cur.execute("""
        UPDATE receipt
        SET match_status = 'pending_match',
            matched_transaction_id = NULL,
            match_confidence = NULL,
            matched_at = NULL,
            matched_by = NULL,
            updated_at = now()
        WHERE id = %s
    """, (str(receipt_id),))

    if cur.rowcount == 0:
        raise HTTPException(404, "Receipt not found")

    conn.commit()
    return {"ok": True}


# ── Candidates ───────────────────────────────────────────────────────────────


@router.get("/receipts/{receipt_id}/candidates")
def get_match_candidates(
    receipt_id: UUID,
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    """Find potential transaction matches for manual matching."""
    from src.receipts.matcher import find_match_candidates

    candidates = find_match_candidates(conn, receipt_id)
    return {"candidates": candidates}


# ── Delete ───────────────────────────────────────────────────────────────────


@router.delete("/receipts/{receipt_id}")
def delete_receipt(
    receipt_id: UUID,
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    """Delete a receipt and its files from disk."""
    cur = conn.cursor()

    cur.execute(
        "SELECT file_path, thumbnail_path FROM receipt WHERE id = %s",
        (str(receipt_id),),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Receipt not found")

    # Delete files
    for rel_path in [row[0], row[1]]:
        if rel_path:
            abs_path = _storage_root() / rel_path
            if abs_path.exists():
                abs_path.unlink()

    # Delete DB row
    cur.execute("DELETE FROM receipt WHERE id = %s", (str(receipt_id),))
    conn.commit()

    return {"ok": True}


# ── Transaction Receipts ─────────────────────────────────────────────────────


@router.get("/transactions/{transaction_id}/receipts")
def get_transaction_receipts(
    transaction_id: UUID,
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    """Get all receipts matched to a transaction."""
    cur = conn.cursor()

    cur.execute("""
        SELECT id, original_filename, mime_type, file_size,
               ocr_status, extracted_date, extracted_amount, extracted_currency,
               extracted_merchant,
               match_status, matched_transaction_id, match_confidence,
               matched_at, matched_by,
               source, uploaded_at, uploaded_by, notes
        FROM receipt
        WHERE matched_transaction_id = %s
        ORDER BY uploaded_at DESC
    """, (str(transaction_id),))

    items = []
    for row in cur.fetchall():
        items.append(ReceiptItem(
            id=row[0],
            original_filename=row[1],
            mime_type=row[2],
            file_size=row[3],
            ocr_status=row[4],
            extracted_date=row[5],
            extracted_amount=row[6],
            extracted_currency=row[7].strip() if row[7] else None,
            extracted_merchant=row[8],
            match_status=row[9],
            matched_transaction_id=row[10],
            match_confidence=row[11],
            matched_at=row[12],
            matched_by=row[13],
            source=row[14],
            uploaded_at=row[15],
            uploaded_by=row[16],
            notes=row[17],
        ))

    return {"items": items}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _load_receipt_detail(cur, receipt_id: UUID, conn) -> ReceiptDetail | None:
    """Load full receipt detail from DB."""
    cur.execute("""
        SELECT id, original_filename, mime_type, file_size,
               file_path, thumbnail_path,
               ocr_status, ocr_text, ocr_data,
               extracted_date, extracted_amount, extracted_currency,
               extracted_merchant,
               match_status, matched_transaction_id, match_confidence,
               matched_at, matched_by,
               source, uploaded_at, uploaded_by, notes
        FROM receipt
        WHERE id = %s
    """, (str(receipt_id),))
    row = cur.fetchone()
    if not row:
        return None

    # Load matched transaction summary if matched
    matched_txn = None
    if row[14]:  # matched_transaction_id
        cur.execute("""
            SELECT id, posted_at, amount, currency, raw_merchant,
                   institution, account_ref
            FROM active_transaction
            WHERE id = %s
        """, (str(row[14]),))
        txn_row = cur.fetchone()
        if txn_row:
            matched_txn = {
                "id": str(txn_row[0]),
                "posted_at": str(txn_row[1]),
                "amount": str(txn_row[2]),
                "currency": txn_row[3].strip() if txn_row[3] else None,
                "raw_merchant": txn_row[4],
                "institution": txn_row[5],
                "account_ref": txn_row[6],
            }

    import json
    ocr_data = row[8]
    if isinstance(ocr_data, str):
        try:
            ocr_data = json.loads(ocr_data)
        except json.JSONDecodeError:
            ocr_data = None

    return ReceiptDetail(
        id=row[0],
        original_filename=row[1],
        mime_type=row[2],
        file_size=row[3],
        file_path=row[4],
        thumbnail_path=row[5],
        ocr_status=row[6],
        ocr_text=row[7],
        ocr_data=ocr_data,
        extracted_date=row[9],
        extracted_amount=row[10],
        extracted_currency=row[11].strip() if row[11] else None,
        extracted_merchant=row[12],
        match_status=row[13],
        matched_transaction_id=row[14],
        match_confidence=row[15],
        matched_at=row[16],
        matched_by=row[17],
        source=row[18],
        uploaded_at=row[19],
        uploaded_by=row[20],
        notes=row[21],
        matched_transaction=matched_txn,
    )
