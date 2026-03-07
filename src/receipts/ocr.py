"""OCR extraction from receipt images/PDFs using Claude Vision API."""

import base64
import json
import logging
from pathlib import Path

import anthropic

log = logging.getLogger(__name__)

RECEIPT_PROMPT = """\
You are analysing a receipt or invoice image. Extract the following fields as JSON:

{
  "date": "YYYY-MM-DD",        // the transaction/purchase date
  "amount": 12.50,             // the total amount paid (positive number)
  "currency": "GBP",           // ISO 4217 3-letter currency code
  "merchant": "Costa Coffee",  // the business/store name
  "items": [                   // line items if visible (optional)
    {"description": "Latte", "amount": 3.50},
    {"description": "Croissant", "amount": 2.00}
  ],
  "confidence": 0.95           // your confidence in the extraction (0.0-1.0)
}

Rules:
- If a field is not visible or unclear, set it to null.
- The amount should be the TOTAL paid, not subtotal.
- Currency should be inferred from the receipt's country/symbols if not explicit.
- Date should be ISO format YYYY-MM-DD.
- Items array can be empty [] if line items aren't visible.
- Return ONLY valid JSON, no markdown or explanation.
"""

# Mime types supported for direct image upload to Claude
IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


def extract_receipt_data(file_path: str, mime_type: str, api_key: str = "") -> dict:
    """Call Claude Vision API to extract structured data from a receipt.

    Args:
        file_path: Path to the receipt file on disk.
        mime_type: MIME type of the file.
        api_key: Anthropic API key. If empty, returns an error.

    Returns dict with keys: date, amount, currency, merchant, items, confidence, raw_text
    On failure returns dict with error key.
    """
    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    if not api_key:
        return {"error": "Anthropic API key not configured — set it in Settings."}

    client = anthropic.Anthropic(api_key=api_key)

    try:
        if mime_type in IMAGE_TYPES:
            return _extract_from_image(client, path, mime_type)
        elif mime_type == "application/pdf":
            return _extract_from_pdf(client, path)
        elif mime_type == "text/plain":
            return _extract_from_text(client, path)
        else:
            return {"error": f"Unsupported mime type: {mime_type}"}
    except Exception as e:
        log.exception("OCR extraction failed for %s", file_path)
        return {"error": str(e)}


def _extract_from_image(client: anthropic.Anthropic, path: Path, mime_type: str) -> dict:
    """Extract data from an image file using Claude Vision."""
    image_data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": image_data,
                    },
                },
                {
                    "type": "text",
                    "text": RECEIPT_PROMPT,
                },
            ],
        }],
    )

    return _parse_response(message)


def _extract_from_pdf(client: anthropic.Anthropic, path: Path) -> dict:
    """Extract data from a PDF file.

    Sends the PDF as a document to Claude (Claude supports PDF natively).
    Falls back to pdf2image if needed.
    """
    pdf_data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")

    try:
        # Try native PDF support first (Claude 3.5+ supports PDFs)
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": RECEIPT_PROMPT,
                    },
                ],
            }],
        )
        return _parse_response(message)
    except Exception as e:
        log.warning("Native PDF failed, trying pdf2image: %s", e)
        return _extract_pdf_as_image(client, path)


def _extract_pdf_as_image(client: anthropic.Anthropic, path: Path) -> dict:
    """Convert first page of PDF to image and send to Claude."""
    try:
        from pdf2image import convert_from_path
        import io

        images = convert_from_path(str(path), first_page=1, last_page=1, dpi=200)
        if not images:
            return {"error": "Could not convert PDF to image"}

        buf = io.BytesIO()
        images[0].save(buf, format="JPEG", quality=90)
        image_data = base64.standard_b64encode(buf.getvalue()).decode("utf-8")

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": RECEIPT_PROMPT,
                    },
                ],
            }],
        )
        return _parse_response(message)
    except ImportError:
        return {"error": "pdf2image not installed — cannot convert PDF"}


def _extract_from_text(client: anthropic.Anthropic, path: Path) -> dict:
    """Extract data from a plain text receipt."""
    text_content = path.read_text(encoding="utf-8", errors="replace")[:4000]

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": f"Here is a receipt in plain text:\n\n{text_content}\n\n{RECEIPT_PROMPT}",
        }],
    )

    return _parse_response(message)


def _parse_response(message) -> dict:
    """Parse Claude's response into structured data."""
    raw_text = message.content[0].text.strip()

    # Strip markdown code fences if present
    if raw_text.startswith("```"):
        lines = raw_text.split("\n")
        # Remove first and last lines (``` markers)
        lines = [l for l in lines if not l.strip().startswith("```")]
        raw_text = "\n".join(lines).strip()

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        log.warning("Failed to parse OCR response as JSON: %s", raw_text[:200])
        return {
            "error": "Failed to parse OCR response",
            "raw_text": raw_text,
        }

    # Normalise the result
    result = {
        "date": data.get("date"),
        "amount": data.get("amount"),
        "currency": data.get("currency"),
        "merchant": data.get("merchant"),
        "items": data.get("items", []),
        "confidence": data.get("confidence"),
        "raw_text": raw_text,
    }

    return result
