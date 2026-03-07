"""Settings API — admin-only app configuration."""

from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import CurrentUser, get_conn, require_admin
from src.api.models import SettingsResponse, SettingsUpdate

router = APIRouter()

# Keys we expose via the API (whitelist)
SETTING_KEYS = {"caldav.enabled", "caldav.tag", "caldav.password"}


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

    # Validate: can't enable CalDAV without a password
    # Work out what the final state will be after applying updates
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
