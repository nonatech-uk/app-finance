"""Receipt management API — upload, OCR, match, serve files, email webhook."""

import base64
import hmac
import json
import logging
import re
from datetime import date
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from config.settings import settings
from src.api.deps import CurrentUser, get_conn, require_admin
from src.api.models import (
    ReceiptCandidate,
    ReceiptDetail,
    ReceiptItem,
    ReceiptList,
    ReceiptMatchRequest,
    ReceiptMetadataImport,
    ReceiptUpdate,
)

log = logging.getLogger(__name__)
router = APIRouter()

ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/heic",
    "image/heif",
    "application/pdf",
    "text/plain",
}

# Extension map for saving files
EXT_MAP = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/heic": ".jpg",   # converted to JPEG on upload
    "image/heif": ".jpg",   # converted to JPEG on upload
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


# ── Shared receipt processing ────────────────────────────────────────────────


def _process_receipt_from_bytes(
    conn, file_bytes: bytes, filename: str, mime_type: str,
    source: str = "web", uploaded_by: str | None = None, note: str | None = None,
) -> UUID:
    """Save file, run OCR, attempt auto-match. Returns receipt_id.

    Shared by web upload and email webhook endpoints.
    """
    cur = conn.cursor()
    file_size = len(file_bytes)
    ext = EXT_MAP.get(mime_type, "")

    # Create receipt row
    cur.execute("""
        INSERT INTO receipt (
            original_filename, mime_type, file_size, file_path,
            source, uploaded_by, notes
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (filename, mime_type, file_size, "pending", source, uploaded_by, note))
    receipt_id = cur.fetchone()[0]

    # Convert HEIC/HEIF to JPEG
    if mime_type in ("image/heic", "image/heif"):
        try:
            import io
            from pillow_heif import register_heif_opener
            from PIL import Image, ImageOps
            register_heif_opener()
            with Image.open(io.BytesIO(file_bytes)) as img:
                img = ImageOps.exif_transpose(img)
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=90)
                file_bytes = buf.getvalue()
            mime_type = "image/jpeg"
            ext = ".jpg"
            file_size = len(file_bytes)
        except Exception as e:
            log.exception("HEIC conversion failed for %s", filename)
            raise HTTPException(400, f"Failed to convert HEIC image: {e}")

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

    cur.execute("""
        UPDATE receipt SET file_path = %s, thumbnail_path = %s,
               mime_type = %s, file_size = %s WHERE id = %s
    """, (rel_path, thumb_rel, mime_type, file_size, str(receipt_id)))
    conn.commit()

    # Run OCR
    try:
        from src.receipts.ocr import extract_receipt_data
        from src.api.routers.settings import get_anthropic_api_key

        api_key = get_anthropic_api_key(conn)
        ocr_result = extract_receipt_data(str(abs_path), mime_type, api_key=api_key)

        if "error" in ocr_result:
            cur.execute("""
                UPDATE receipt
                SET ocr_status = 'failed', ocr_data = %s,
                    match_status = 'pending_match', updated_at = now()
                WHERE id = %s
            """, (json.dumps({"error": ocr_result["error"]}), str(receipt_id)))
        else:
            cur.execute("""
                UPDATE receipt
                SET ocr_status = 'completed', ocr_text = %s, ocr_data = %s,
                    extracted_date = %s, extracted_amount = %s,
                    extracted_currency = %s, extracted_merchant = %s,
                    match_status = 'pending_match', updated_at = now()
                WHERE id = %s
            """, (
                ocr_result.get("raw_text"), json.dumps(ocr_result),
                ocr_result.get("date"), ocr_result.get("amount"),
                ocr_result.get("currency"), ocr_result.get("merchant"),
                str(receipt_id),
            ))
        conn.commit()
    except Exception as e:
        log.exception("OCR failed for receipt %s", receipt_id)
        cur.execute("""
            UPDATE receipt
            SET ocr_status = 'failed', match_status = 'pending_match', updated_at = now()
            WHERE id = %s
        """, (str(receipt_id),))
        conn.commit()

    # Attempt auto-match
    try:
        from src.receipts.matcher import auto_match_receipt
        auto_match_receipt(conn, receipt_id)
    except Exception as e:
        log.exception("Auto-match failed for receipt %s", receipt_id)

    return receipt_id


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

    file_bytes = file.file.read()
    if not file_bytes:
        raise HTTPException(400, "Empty file")

    receipt_id = _process_receipt_from_bytes(
        conn, file_bytes, file.filename or "receipt", file.content_type,
        source="web", uploaded_by=user.email, note=note,
    )

    cur = conn.cursor()
    return _load_receipt_detail(cur, receipt_id, conn)


# ── Metadata Import (pipeline integration) ───────────────────────────────────


@router.post("/receipts/import-metadata", response_model=ReceiptDetail)
def import_receipt_metadata(
    body: ReceiptMetadataImport,
    conn=Depends(get_conn),
):
    """Import a receipt from structured metadata — no OCR needed.

    Used by the ingestion pipeline which has already classified and
    extracted fields. Optionally accepts base64 file bytes.
    """
    cur = conn.cursor()

    # Determine file details
    mime_type = body.mime_type or "application/octet-stream"
    file_bytes = None
    file_size = 0
    if body.file_bytes:
        file_bytes = base64.b64decode(body.file_bytes)
        file_size = len(file_bytes)

    # Create receipt row
    cur.execute("""
        INSERT INTO receipt (
            original_filename, mime_type, file_size, file_path,
            source, notes,
            ocr_status, ocr_data,
            extracted_date, extracted_amount, extracted_currency, extracted_merchant,
            match_status
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        body.original_filename, mime_type, file_size, "metadata_only",
        body.source, body.note,
        "imported", json.dumps(body.ocr_data) if body.ocr_data else None,
        str(body.extracted_date) if body.extracted_date else None,
        str(body.extracted_amount) if body.extracted_amount else None,
        body.extracted_currency,
        body.extracted_merchant,
        "pending_match",
    ))
    receipt_id = cur.fetchone()[0]

    # Save file if provided
    if file_bytes:
        ext = EXT_MAP.get(mime_type, "")
        year_month = date.today().strftime("%Y/%m")
        rel_path = f"{year_month}/{receipt_id}{ext}"
        abs_path = _storage_root() / rel_path
        _ensure_dir(abs_path)
        abs_path.write_bytes(file_bytes)

        thumb_rel = None
        thumb_path = _storage_root() / f"{year_month}/{receipt_id}_thumb.jpg"
        if _make_thumbnail(abs_path, thumb_path, mime_type):
            thumb_rel = f"{year_month}/{receipt_id}_thumb.jpg"

        cur.execute("""
            UPDATE receipt SET file_path = %s, thumbnail_path = %s WHERE id = %s
        """, (rel_path, thumb_rel, str(receipt_id)))

    conn.commit()

    # Attempt auto-match
    try:
        from src.receipts.matcher import auto_match_receipt
        auto_match_receipt(conn, receipt_id)
    except Exception:
        log.exception("Auto-match failed for imported receipt %s", receipt_id)

    return _load_receipt_detail(cur, receipt_id, conn)


# ── Email Webhook ────────────────────────────────────────────────────────────


def _extract_sender_email(from_field) -> str | None:
    """Extract email address from ForwardEmail's 'from' field.

    Handles multiple formats from simpleParser:
    1. {"value": [{"address": "..."}]}
    2. {"address": "..."}
    3. [{"address": "..."}]
    4. {"text": "Name <email>"}
    5. Plain string "email@example.com"
    """
    if not from_field:
        return None

    if isinstance(from_field, str):
        # Plain email or "Name <email>" format
        m = re.search(r"<([^>]+)>", from_field)
        return m.group(1).lower() if m else from_field.strip().lower()

    if isinstance(from_field, dict):
        # Format 1: {"value": [{"address": "..."}]}
        if "value" in from_field and isinstance(from_field["value"], list):
            for v in from_field["value"]:
                if isinstance(v, dict) and "address" in v:
                    return v["address"].lower()

        # Format 2: {"address": "..."}
        if "address" in from_field:
            return from_field["address"].lower()

        # Format 4: {"text": "Name <email>"}
        if "text" in from_field:
            m = re.search(r"<([^>]+)>", from_field["text"])
            return m.group(1).lower() if m else from_field["text"].strip().lower()

    if isinstance(from_field, list):
        # Format 3: [{"address": "..."}]
        for item in from_field:
            if isinstance(item, dict) and "address" in item:
                return item["address"].lower()

    return None


def _decode_attachment_content(content) -> bytes | None:
    """Decode ForwardEmail attachment content.

    Handles three formats:
    1. Buffer object: {"type": "Buffer", "data": [byte, byte, ...]}
    2. Base64 string
    3. Plain string (for text files)
    """
    if not content:
        return None

    # Format 1: Buffer object
    if isinstance(content, dict) and content.get("type") == "Buffer":
        data = content.get("data", [])
        return bytes(data)

    if isinstance(content, str):
        # Format 2: Base64
        try:
            decoded = base64.b64decode(content, validate=True)
            if decoded:
                return decoded
        except Exception:
            pass

        # Format 3: Plain text
        return content.encode("utf-8")

    return None


@router.post("/receipts/webhook")
async def receive_email_webhook(
    request: Request,
    token: str = Query(""),
    conn=Depends(get_conn),
):
    """Receive receipt emails from ForwardEmail webhook.

    Auth via ?token=SECRET query param (not Authelia).
    Always returns 200 to prevent ForwardEmail retries.
    """
    # Load webhook settings
    cur = conn.cursor()
    cur.execute("""
        SELECT key, value FROM app_setting
        WHERE key IN ('webhook.receipt_enabled', 'webhook.receipt_secret', 'webhook.receipt_allowed_senders')
    """)
    wh_settings = dict(cur.fetchall())

    secret = wh_settings.get("webhook.receipt_secret", "")
    if not secret or not hmac.compare_digest(token, secret):
        return JSONResponse({"error": "unauthorized"}, status_code=403)

    if wh_settings.get("webhook.receipt_enabled", "false").lower() != "true":
        return JSONResponse({"ok": False, "reason": "webhook disabled"})

    # Parse body
    try:
        body = await request.json()
    except Exception:
        log.warning("Email webhook: invalid JSON body")
        return JSONResponse({"ok": False, "reason": "invalid JSON"})

    # Extract sender
    sender = _extract_sender_email(body.get("from"))
    log.info("Email webhook from %s, subject: %s", sender, body.get("subject", ""))

    # Check allowed senders
    allowed_raw = wh_settings.get("webhook.receipt_allowed_senders", "").strip()
    if allowed_raw:
        allowed = {e.strip().lower() for e in allowed_raw.splitlines() if e.strip()}
        if sender and sender not in allowed:
            log.info("Email webhook: sender %s not in allowed list", sender)
            return JSONResponse({"ok": False, "reason": "sender not allowed"})

    subject = body.get("subject", "")
    receipts_created = 0

    # Process attachments
    attachments = body.get("attachments", [])
    for att in attachments:
        filename = att.get("filename") or att.get("name") or "attachment"
        content_type = att.get("contentType") or att.get("type") or ""

        # Normalise content type (remove parameters like charset)
        content_type = content_type.split(";")[0].strip().lower()

        if content_type not in ALLOWED_MIME_TYPES:
            log.info("Email webhook: skipping attachment %s (%s)", filename, content_type)
            continue

        file_bytes = _decode_attachment_content(att.get("content"))
        if not file_bytes:
            log.warning("Email webhook: could not decode attachment %s", filename)
            continue

        try:
            _process_receipt_from_bytes(
                conn, file_bytes, filename, content_type,
                source="email", uploaded_by=sender,
                note=f"Email: {subject}" if subject else None,
            )
            receipts_created += 1
        except Exception as e:
            log.exception("Email webhook: failed to process attachment %s", filename)

    # If no attachments, try email body as text receipt
    if not attachments or receipts_created == 0:
        text_body = body.get("text", "")
        html_body = body.get("html", "")
        email_text = text_body or ""

        # Strip HTML tags as fallback
        if not email_text and html_body:
            email_text = re.sub(r"<[^>]+>", "", html_body).strip()

        if email_text and len(email_text) > 20:
            # Prepend subject for context
            full_text = f"Subject: {subject}\n\n{email_text}" if subject else email_text

            try:
                _process_receipt_from_bytes(
                    conn, full_text.encode("utf-8"),
                    f"email-{date.today().isoformat()}.txt", "text/plain",
                    source="email", uploaded_by=sender,
                    note=f"Email: {subject}" if subject else None,
                )
                receipts_created += 1
            except Exception as e:
                log.exception("Email webhook: failed to process email body as text")

    log.info("Email webhook: created %d receipt(s) from %s", receipts_created, sender)
    return JSONResponse({"ok": True, "receipts_created": receipts_created})


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


# ── Update OCR Fields ────────────────────────────────────────────────────────


@router.patch("/receipts/{receipt_id}")
def update_receipt(
    receipt_id: UUID,
    body: ReceiptUpdate,
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    """Update OCR-extracted fields on a receipt, then re-attempt auto-match."""
    cur = conn.cursor()

    # Verify receipt exists
    cur.execute("SELECT id FROM receipt WHERE id = %s", (str(receipt_id),))
    if not cur.fetchone():
        raise HTTPException(404, "Receipt not found")

    # Build SET clause from provided fields only
    provided = body.model_fields_set
    if not provided:
        raise HTTPException(400, "No fields to update")

    set_parts = []
    params: list = []
    for field in ("extracted_date", "extracted_amount", "extracted_currency", "extracted_merchant"):
        if field in provided:
            set_parts.append(f"{field} = %s")
            params.append(getattr(body, field))

    set_parts.append("updated_at = now()")
    params.append(str(receipt_id))

    cur.execute(
        f"UPDATE receipt SET {', '.join(set_parts)} WHERE id = %s",
        params,
    )
    conn.commit()

    # Re-attempt auto-match if date or amount changed
    if {"extracted_date", "extracted_amount", "extracted_currency"} & provided:
        try:
            # Reset match status so auto-match can re-evaluate
            cur.execute("""
                UPDATE receipt
                SET match_status = 'pending_match',
                    matched_transaction_id = NULL,
                    match_confidence = NULL,
                    matched_at = NULL,
                    matched_by = NULL
                WHERE id = %s AND match_status NOT IN ('manually_matched')
            """, (str(receipt_id),))
            conn.commit()

            from src.receipts.matcher import auto_match_receipt
            auto_match_receipt(conn, receipt_id)
        except Exception as e:
            log.exception("Auto-match failed after OCR update for receipt %s", receipt_id)

    return _load_receipt_detail(cur, receipt_id, conn)


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
