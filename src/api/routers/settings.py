"""Settings API — admin-only app configuration."""

from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import CurrentUser, get_conn, require_admin
from src.api.models import SettingsResponse, SettingsUpdate

router = APIRouter()

# Keys we expose via the API (whitelist)
SETTING_KEYS = {
    "caldav.enabled", "caldav.tag", "caldav.password",
    "receipt.alert_days", "receipt.match_date_tolerance",
    "receipt.auto_match_enabled", "receipt.amount_tolerance_pct",
    "anthropic.api_key",
}


def _load_settings(conn) -> dict[str, str]:
    """Load all app_setting rows into a dict."""
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM app_setting WHERE key = ANY(%s)", (list(SETTING_KEYS),))
    return dict(cur.fetchall())


def _to_response(raw: dict[str, str]) -> SettingsResponse:
    return SettingsResponse(
        caldav_enabled=raw.get("caldav.enabled", "true").lower() == "true",
        caldav_tag=raw.get("caldav.tag", "todo"),
        caldav_password_set=bool(raw.get("caldav.password", "")),
        # Receipt settings
        receipt_alert_days=int(raw.get("receipt.alert_days", "7")),
        receipt_match_date_tolerance=int(raw.get("receipt.match_date_tolerance", "2")),
        receipt_auto_match_enabled=raw.get("receipt.auto_match_enabled", "true").lower() == "true",
        receipt_amount_tolerance_pct=int(raw.get("receipt.amount_tolerance_pct", "20")),
        anthropic_api_key_set=bool(raw.get("anthropic.api_key", "")),
    )


@router.get("/settings", response_model=SettingsResponse)
def get_settings(
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    return _to_response(_load_settings(conn))


@router.put("/settings", response_model=SettingsResponse)
def update_settings(
    body: SettingsUpdate,
    conn=Depends(get_conn),
    user: CurrentUser = Depends(require_admin),
):
    cur = conn.cursor()

    updates = {}
    if body.caldav_enabled is not None:
        updates["caldav.enabled"] = "true" if body.caldav_enabled else "false"
    if body.caldav_tag is not None:
        tag = body.caldav_tag.strip().lower()
        if not tag:
            tag = "todo"
        updates["caldav.tag"] = tag
    if body.caldav_password is not None:
        updates["caldav.password"] = body.caldav_password

    # Receipt settings
    if body.receipt_alert_days is not None:
        updates["receipt.alert_days"] = str(body.receipt_alert_days)
    if body.receipt_match_date_tolerance is not None:
        updates["receipt.match_date_tolerance"] = str(body.receipt_match_date_tolerance)
    if body.receipt_auto_match_enabled is not None:
        updates["receipt.auto_match_enabled"] = "true" if body.receipt_auto_match_enabled else "false"
    if body.receipt_amount_tolerance_pct is not None:
        updates["receipt.amount_tolerance_pct"] = str(body.receipt_amount_tolerance_pct)

    # Anthropic API key — stored in app_setting table
    if body.anthropic_api_key is not None:
        updates["anthropic.api_key"] = body.anthropic_api_key

    # Validate: can't enable CalDAV without a password
    current = _load_settings(conn)
    final_enabled = updates.get("caldav.enabled", current.get("caldav.enabled", "true"))
    final_password = updates.get("caldav.password", current.get("caldav.password", ""))

    if final_enabled == "true" and not final_password:
        raise HTTPException(400, "An app password is required to enable the CalDAV feed.")

    for key, value in updates.items():
        cur.execute("""
            INSERT INTO app_setting (key, value, updated_at)
            VALUES (%s, %s, now())
            ON CONFLICT (key)
            DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        """, (key, value))

    conn.commit()
    return _to_response(_load_settings(conn))


def get_anthropic_api_key(conn) -> str:
    """Get the Anthropic API key from app_setting table.

    Falls back to settings.anthropic_api_key env var if not in DB.
    """
    cur = conn.cursor()
    cur.execute("SELECT value FROM app_setting WHERE key = 'anthropic.api_key'")
    row = cur.fetchone()
    if row and row[0]:
        return row[0]

    # Fallback to env var / .env file
    from config.settings import settings
    return settings.anthropic_api_key or ""
