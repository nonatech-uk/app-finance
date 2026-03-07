"""iCalendar VTODO generation and parsing."""

import hashlib
from datetime import datetime, timezone


def fold_line(line: str) -> str:
    """Fold iCalendar line at 75 octets per RFC 5545."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line

    parts = []
    while len(encoded) > 75:
        # First line: 75 octets, continuation lines: 74 (leading space counts)
        cut = 75 if not parts else 74
        # Don't split in the middle of a multi-byte char
        chunk = encoded[:cut]
        try:
            chunk.decode("utf-8")
        except UnicodeDecodeError:
            cut -= 1
            while cut > 0:
                try:
                    encoded[:cut].decode("utf-8")
                    break
                except UnicodeDecodeError:
                    cut -= 1
            chunk = encoded[:cut]
        parts.append(chunk.decode("utf-8"))
        encoded = encoded[cut:]
    if encoded:
        parts.append(encoded.decode("utf-8"))

    return "\r\n ".join(parts)


def escape_text(text: str) -> str:
    """Escape text for iCalendar property values."""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
        .replace("\r", "")
    )


def transaction_to_vtodo(txn: dict) -> str:
    """Generate a VTODO string from a transaction dict.

    Expected keys: id, posted_at, amount, currency, display_merchant,
    account_ref, institution, raw_memo, category_path, tag_created_at
    """
    uid = str(txn["id"])
    posted = txn["posted_at"]
    amount = txn["amount"]
    currency = txn["currency"]
    merchant = txn.get("display_merchant") or txn.get("raw_merchant") or "Unknown"
    account = txn.get("account_ref", "")
    institution = txn.get("institution", "")
    memo = txn.get("raw_memo") or ""
    category = txn.get("category_path") or ""
    tag_created = txn.get("tag_created_at") or datetime.now(timezone.utc)
    note = txn.get("note") or ""

    # Format amount with sign
    amount_str = f"{amount:+.2f}" if amount else "0.00"

    # Summary includes context (read-only: merchant, amount, account, category)
    summary_parts = [f"{merchant} — {currency} {amount_str} ({posted}) — {institution}/{account}"]
    if category:
        summary_parts.append(f"[{category}]")
    summary = " ".join(summary_parts)

    # Description = just the note (editable — syncs back to transaction_note)
    # Memo is appended as read-only context below a separator
    desc_parts = []
    if note:
        desc_parts.append(note)
    if memo:
        if desc_parts:
            desc_parts.append("---")
        desc_parts.append(f"Memo: {memo}")
    description = "\\n".join(escape_text(p) for p in desc_parts)

    dtstamp = tag_created.strftime("%Y%m%dT%H%M%SZ") if hasattr(tag_created, "strftime") else "20260101T000000Z"
    due_date = posted.strftime("%Y%m%d") if hasattr(posted, "strftime") else str(posted).replace("-", "")

    lines = [
        "BEGIN:VTODO",
        fold_line(f"UID:{uid}"),
        fold_line(f"DTSTAMP:{dtstamp}"),
        fold_line(f"SUMMARY:{escape_text(summary)}"),
    ]
    if description:
        lines.append(fold_line(f"DESCRIPTION:{description}"))
    lines.extend([
        fold_line(f"DUE;VALUE=DATE:{due_date}"),
        "STATUS:NEEDS-ACTION",
        "END:VTODO",
    ])
    return "\r\n".join(lines)


def wrap_vcalendar(*vtodos: str) -> str:
    """Wrap VTODO(s) in a VCALENDAR."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Finance//CalDAV//EN",
    ]
    for vtodo in vtodos:
        lines.append(vtodo)
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def unescape_text(text: str) -> str:
    """Unescape iCalendar property values."""
    return (
        text.replace("\\n", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def unfold_ical(text: str) -> str:
    """Unfold iCalendar continuation lines."""
    return text.replace("\r\n ", "").replace("\r\n\t", "")


def parse_vtodo(ical_text: str) -> dict:
    """Parse a VTODO body, returning STATUS and DESCRIPTION (note).

    Returns dict with 'status' and 'note' keys.
    """
    text = unfold_ical(ical_text)
    result = {"status": None, "note": None}

    for line in text.splitlines():
        line = line.strip()
        if line.upper().startswith("STATUS:"):
            result["status"] = line.split(":", 1)[1].strip().upper()
        elif line.upper().startswith("DESCRIPTION:"):
            raw_desc = line.split(":", 1)[1]
            desc = unescape_text(raw_desc)
            # Strip the read-only memo section (below --- separator)
            if "\n---\n" in desc:
                desc = desc.split("\n---\n")[0]
            elif desc.strip().startswith("Memo:"):
                desc = ""  # Only memo, no user note
            result["note"] = desc.strip() or None

    return result


def parse_vtodo_status(ical_text: str) -> str | None:
    """Extract STATUS from an iCalendar VTODO body."""
    return parse_vtodo(ical_text)["status"]


def make_etag(txn_id: str, tag_created_at, note_updated_at=None) -> str:
    """Generate an ETag for a VTODO."""
    ts = str(tag_created_at) if tag_created_at else ""
    ns = str(note_updated_at) if note_updated_at else ""
    h = hashlib.md5(f"{txn_id}:{ts}:{ns}".encode()).hexdigest()[:16]
    return f'"{h}"'
