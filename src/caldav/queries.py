"""Database queries for CalDAV todo-tagged transactions."""


def get_caldav_settings(conn) -> dict[str, str]:
    """Load CalDAV settings from app_setting table."""
    cur = conn.cursor()
    cur.execute("""
        SELECT key, value FROM app_setting
        WHERE key LIKE 'caldav.%'
    """)
    return dict(cur.fetchall())


def get_tag_name(conn) -> str:
    """Get the configured tag name (default: 'todo')."""
    cur = conn.cursor()
    cur.execute("SELECT value FROM app_setting WHERE key = 'caldav.tag'")
    row = cur.fetchone()
    return row[0] if row else "todo"


def get_todo_transactions(conn, tag: str = "todo") -> list[dict]:
    """Fetch all active transactions with the given tag, with merchant/category info."""
    cur = conn.cursor()
    cur.execute("""
        SELECT
            rt.id,
            rt.posted_at,
            rt.amount,
            rt.currency,
            rt.institution,
            rt.account_ref,
            rt.raw_merchant,
            rt.raw_memo,
            COALESCE(cm_override.display_name, cm_override.name,
                     cm.display_name, cm.name,
                     ct.cleaned_merchant, rt.raw_merchant) AS display_merchant,
            COALESCE(tcat.full_path, cat_override.full_path, cat.full_path) AS category_path,
            tt.created_at AS tag_created_at,
            tn.note,
            tn.updated_at AS note_updated_at
        FROM active_transaction rt
        JOIN transaction_tag tt ON tt.raw_transaction_id = rt.id AND tt.tag = %s
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
        ORDER BY rt.posted_at DESC
    """, (tag,))

    columns = [desc[0] for desc in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def get_todo_transaction(conn, uid: str, tag: str = "todo") -> dict | None:
    """Fetch a single tagged transaction by UUID."""
    cur = conn.cursor()
    cur.execute("""
        SELECT
            rt.id,
            rt.posted_at,
            rt.amount,
            rt.currency,
            rt.institution,
            rt.account_ref,
            rt.raw_merchant,
            rt.raw_memo,
            COALESCE(cm_override.display_name, cm_override.name,
                     cm.display_name, cm.name,
                     ct.cleaned_merchant, rt.raw_merchant) AS display_merchant,
            COALESCE(tcat.full_path, cat_override.full_path, cat.full_path) AS category_path,
            tt.created_at AS tag_created_at,
            tn.note,
            tn.updated_at AS note_updated_at
        FROM active_transaction rt
        JOIN transaction_tag tt ON tt.raw_transaction_id = rt.id AND tt.tag = %s
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
        WHERE rt.id = %s::uuid
    """, (tag, uid))

    row = cur.fetchone()
    if not row:
        return None
    columns = [desc[0] for desc in cur.description]
    return dict(zip(columns, row))


def get_ctag(conn, tag: str = "todo") -> str:
    """Get current CTag — changes when tags or notes change."""
    cur = conn.cursor()
    cur.execute("""
        SELECT COALESCE(
            MAX(GREATEST(tt.created_at, COALESCE(tn.updated_at, tt.created_at))),
            '1970-01-01'::timestamptz
        )::text
        FROM transaction_tag tt
        LEFT JOIN transaction_note tn ON tn.raw_transaction_id = tt.raw_transaction_id
        WHERE tt.tag = %s
    """, (tag,))
    return cur.fetchone()[0]


def remove_tag(conn, uid: str, tag: str = "todo") -> bool:
    """Remove the tag from a transaction. Returns True if removed."""
    cur = conn.cursor()
    cur.execute("""
        DELETE FROM transaction_tag
        WHERE raw_transaction_id = %s::uuid AND tag = %s
    """, (uid, tag))
    conn.commit()
    return cur.rowcount > 0


def has_tag(conn, uid: str, tag: str = "todo") -> bool:
    """Check if a transaction has the given tag."""
    cur = conn.cursor()
    cur.execute("""
        SELECT 1 FROM transaction_tag
        WHERE raw_transaction_id = %s::uuid AND tag = %s
    """, (uid, tag))
    return cur.fetchone() is not None


def update_note(conn, uid: str, note: str | None) -> bool:
    """Update or create the transaction note. Returns True if changed."""
    cur = conn.cursor()
    if note:
        cur.execute("""
            INSERT INTO transaction_note (raw_transaction_id, note)
            VALUES (%s::uuid, %s)
            ON CONFLICT (raw_transaction_id)
            DO UPDATE SET note = EXCLUDED.note, updated_at = now()
            WHERE transaction_note.note IS DISTINCT FROM EXCLUDED.note
        """, (uid, note))
    else:
        cur.execute("""
            DELETE FROM transaction_note
            WHERE raw_transaction_id = %s::uuid
        """, (uid,))
    conn.commit()
    return cur.rowcount > 0
