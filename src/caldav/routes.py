"""CalDAV routes for Apple Reminders integration.

Serves todo-tagged transactions as VTODO items. Completing a task
in Reminders removes the 'todo' tag.

Uses Starlette Route directly since FastAPI doesn't support
PROPFIND/REPORT HTTP methods.
"""

import base64
import xml.etree.ElementTree as ET

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route, Router

from src.api import deps
from src.caldav.queries import (
    get_caldav_settings,
    get_ctag,
    get_tag_name,
    get_todo_transaction,
    get_todo_transactions,
    has_tag,
    remove_tag,
    update_note,
)
from src.caldav.vtodo import make_etag, parse_vtodo, transaction_to_vtodo, wrap_vcalendar
from src.caldav.xml_helpers import (
    CALDAV,
    CS,
    DAV,
    ICAL,
    make_comp,
    make_href_element,
    make_resourcetype,
    multistatus,
    parse_propfind,
    parse_report,
)

CALENDAR_PATH = "/caldav/calendars/tasks/"
CALENDAR_COLOR = "#4A90D9FF"


def _get_conn():
    assert deps.pool is not None, "Connection pool not initialised"
    return deps.pool.getconn()


def _put_conn(conn):
    deps.pool.putconn(conn)


def _dav_headers() -> dict:
    return {
        "DAV": "1, 3, calendar-access",
        "Content-Type": "application/xml; charset=utf-8",
    }


def _xml_response(body: bytes, status: int = 207) -> Response:
    return Response(
        content=body,
        status_code=status,
        headers=_dav_headers(),
    )


def _check_auth(request: Request, conn) -> Response | None:
    """Check HTTP Basic auth against caldav.password setting.

    Returns None if auth is OK, or a 401 Response if auth fails.
    If no password is configured (empty string), auth is not required.
    """
    settings = get_caldav_settings(conn)
    password = settings.get("caldav.password", "")

    if not password:
        return None  # No password set — open access

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        return Response(
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Finance CalDAV"'},
            content=b"Authentication required",
        )

    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
        _, provided_password = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError):
        return Response(
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Finance CalDAV"'},
            content=b"Invalid credentials",
        )

    if provided_password != password:
        return Response(
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Finance CalDAV"'},
            content=b"Invalid credentials",
        )

    return None


def _check_enabled(conn) -> Response | None:
    """Check if CalDAV is enabled. Returns 404 response if disabled."""
    settings = get_caldav_settings(conn)
    enabled = settings.get("caldav.enabled", "true").lower() == "true"
    if not enabled:
        return Response(
            status_code=404,
            content=b"CalDAV task feed is disabled",
            headers={"Content-Type": "text/plain"},
        )
    return None


# ── Root ─────────────────────────────────────────────────────────────────────


async def handle_root(request: Request) -> Response:
    if request.method == "OPTIONS":
        return _options_response()

    conn = _get_conn()
    try:
        denied = _check_enabled(conn)
        if denied:
            return denied
        denied = _check_auth(request, conn)
        if denied:
            return denied

        body = await request.body()
        props = parse_propfind(body)

        response_props = []
        not_found = []

        # Properties we support at root level
        known = {
            (DAV, "resourcetype"): (DAV, "resourcetype", make_resourcetype((DAV, "collection"))),
            (DAV, "current-user-principal"): (DAV, "current-user-principal", make_href_element("/caldav/principal/")),
            (DAV, "displayname"): (DAV, "displayname", "Finance CalDAV"),
        }

        if props is None:
            response_props = list(known.values())
        else:
            for ns, local in props:
                key = (ns, local)
                if key in known:
                    response_props.append(known[key])
                else:
                    not_found.append(key)

        resp = {"href": "/caldav/", "props": response_props}
        if not_found:
            resp["not_found"] = not_found

        return _xml_response(multistatus(resp))
    finally:
        _put_conn(conn)


# ── Principal ────────────────────────────────────────────────────────────────


async def handle_principal(request: Request) -> Response:
    if request.method == "OPTIONS":
        return _options_response()

    conn = _get_conn()
    try:
        denied = _check_enabled(conn)
        if denied:
            return denied
        denied = _check_auth(request, conn)
        if denied:
            return denied

        body = await request.body()
        props = parse_propfind(body)

        known = {
            (DAV, "resourcetype"): (DAV, "resourcetype", make_resourcetype((DAV, "collection"))),
            (CALDAV, "calendar-home-set"): (CALDAV, "calendar-home-set", make_href_element("/caldav/calendars/")),
            (DAV, "displayname"): (DAV, "displayname", "Finance User"),
        }

        response_props = []
        not_found = []
        if props is None:
            response_props = list(known.values())
        else:
            for ns, local in props:
                key = (ns, local)
                if key in known:
                    response_props.append(known[key])
                else:
                    not_found.append(key)

        resp = {"href": "/caldav/principal/", "props": response_props}
        if not_found:
            resp["not_found"] = not_found

        return _xml_response(multistatus(resp))
    finally:
        _put_conn(conn)


# ── Calendar Home ────────────────────────────────────────────────────────────


async def handle_calendar_home(request: Request) -> Response:
    if request.method == "OPTIONS":
        return _options_response()

    conn = _get_conn()
    try:
        denied = _check_enabled(conn)
        if denied:
            return denied
        denied = _check_auth(request, conn)
        if denied:
            return denied

        body = await request.body()
        depth = request.headers.get("Depth", "0")

        home_props = [
            (DAV, "resourcetype", make_resourcetype((DAV, "collection"))),
            (DAV, "displayname", "Calendars"),
        ]

        responses = [{"href": "/caldav/calendars/", "props": home_props}]

        if depth == "1":
            tag = get_tag_name(conn)
            ctag = get_ctag(conn, tag)
            cal_props = _calendar_props(ctag)
            responses.append({"href": CALENDAR_PATH, "props": cal_props})

        return _xml_response(multistatus(*responses))
    finally:
        _put_conn(conn)


# ── Calendar ─────────────────────────────────────────────────────────────────


async def handle_calendar(request: Request) -> Response:
    if request.method == "OPTIONS":
        return _options_response()

    conn = _get_conn()
    try:
        denied = _check_enabled(conn)
        if denied:
            return denied
        denied = _check_auth(request, conn)
        if denied:
            return denied

        if request.method == "REPORT":
            return await _handle_report(request, conn)

        # PROPFIND
        body = await request.body()
        depth = request.headers.get("Depth", "0")
        props = parse_propfind(body)

        tag = get_tag_name(conn)
        ctag = get_ctag(conn, tag)

        cal_known = _calendar_prop_map(ctag)
        response_props = []
        not_found = []

        if props is None:
            response_props = list(cal_known.values())
        else:
            for ns, local in props:
                key = (ns, local)
                if key in cal_known:
                    response_props.append(cal_known[key])
                else:
                    not_found.append(key)

        responses = [{"href": CALENDAR_PATH, "props": response_props}]
        if not_found:
            responses[0]["not_found"] = not_found

        if depth == "1":
            txns = get_todo_transactions(conn, tag)
            for txn in txns:
                uid = str(txn["id"])
                etag = make_etag(uid, txn["tag_created_at"], txn.get("note_updated_at"))
                responses.append({
                    "href": f"{CALENDAR_PATH}{uid}.ics",
                    "props": [
                        (DAV, "getetag", etag),
                        (DAV, "getcontenttype", "text/calendar; charset=utf-8"),
                    ],
                })

        return _xml_response(multistatus(*responses))
    finally:
        _put_conn(conn)


async def _handle_report(request: Request, conn) -> Response:
    body = await request.body()
    report = parse_report(body)
    tag = get_tag_name(conn)

    if report["report_type"] == "sync-collection":
        return _sync_collection(conn, report, tag)

    if report["report_type"] == "calendar-multiget":
        return _calendar_multiget(conn, report, tag)

    # calendar-query: return all VTODOs
    return _calendar_query(conn, report, tag)


def _calendar_query(conn, report: dict, tag: str) -> Response:
    txns = get_todo_transactions(conn, tag)
    wants_data = any(
        ns == CALDAV and local == "calendar-data" for ns, local in report["props"]
    )

    responses = []
    for txn in txns:
        uid = str(txn["id"])
        etag = make_etag(uid, txn["tag_created_at"], txn.get("note_updated_at"))
        props = [(DAV, "getetag", etag)]
        if wants_data:
            vtodo = transaction_to_vtodo(txn)
            ical = wrap_vcalendar(vtodo)
            props.append((CALDAV, "calendar-data", ical))
        responses.append({"href": f"{CALENDAR_PATH}{uid}.ics", "props": props})

    return _xml_response(multistatus(*responses))


def _calendar_multiget(conn, report: dict, tag: str) -> Response:
    wants_data = any(
        ns == CALDAV and local == "calendar-data" for ns, local in report["props"]
    )

    responses = []
    for href in report["hrefs"]:
        # Extract UID from href like /caldav/calendars/tasks/{uid}.ics
        uid = href.rstrip("/").rsplit("/", 1)[-1].replace(".ics", "")
        txn = get_todo_transaction(conn, uid, tag)
        if txn:
            etag = make_etag(uid, txn["tag_created_at"], txn.get("note_updated_at"))
            props = [(DAV, "getetag", etag)]
            if wants_data:
                vtodo = transaction_to_vtodo(txn)
                ical = wrap_vcalendar(vtodo)
                props.append((CALDAV, "calendar-data", ical))
            responses.append({"href": href, "props": props})
        else:
            responses.append({"href": href, "status": "HTTP/1.1 404 Not Found"})

    return _xml_response(multistatus(*responses))


def _sync_collection(conn, report: dict, tag: str) -> Response:
    """Handle sync-collection REPORT.

    Simple approach: if sync-token matches current ctag, return empty.
    Otherwise return all current items (full re-sync).
    """
    ctag = get_ctag(conn, tag)
    client_token = report.get("sync_token") or ""
    sync_token_url = f"https://finance.mees.st/caldav/sync/{ctag}"

    # If client has current token, nothing changed
    if client_token == sync_token_url:
        ms = multistatus()
        # Inject sync-token into multistatus
        root = ET.fromstring(ms)
        st = ET.SubElement(root, f"{{{DAV}}}sync-token")
        st.text = sync_token_url
        body = b'<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode").encode("utf-8")
        return _xml_response(body)

    # Full re-sync
    txns = get_todo_transactions(conn, tag)
    wants_data = any(
        ns == CALDAV and local == "calendar-data" for ns, local in report["props"]
    )

    responses = []
    for txn in txns:
        uid = str(txn["id"])
        etag = make_etag(uid, txn["tag_created_at"], txn.get("note_updated_at"))
        props = [(DAV, "getetag", etag)]
        if wants_data:
            vtodo = transaction_to_vtodo(txn)
            ical = wrap_vcalendar(vtodo)
            props.append((CALDAV, "calendar-data", ical))
        responses.append({"href": f"{CALENDAR_PATH}{uid}.ics", "props": props})

    ms_bytes = multistatus(*responses)
    root = ET.fromstring(ms_bytes)
    st = ET.SubElement(root, f"{{{DAV}}}sync-token")
    st.text = sync_token_url
    body = b'<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode").encode("utf-8")
    return _xml_response(body)


# ── Individual VTODO ─────────────────────────────────────────────────────────


async def handle_vtodo(request: Request) -> Response:
    if request.method == "OPTIONS":
        return _options_response()

    conn = _get_conn()
    try:
        denied = _check_enabled(conn)
        if denied:
            return denied
        denied = _check_auth(request, conn)
        if denied:
            return denied

        uid = request.path_params["uid"]
        tag = get_tag_name(conn)

        if request.method == "GET":
            return _get_vtodo(conn, uid, tag)
        elif request.method == "PUT":
            body = await request.body()
            return _put_vtodo(conn, uid, body, tag)
        elif request.method == "DELETE":
            return _delete_vtodo(conn, uid, tag)

        return Response(status_code=405)
    finally:
        _put_conn(conn)


def _get_vtodo(conn, uid: str, tag: str) -> Response:
    txn = get_todo_transaction(conn, uid, tag)

    if not txn:
        return Response(status_code=404)

    vtodo = transaction_to_vtodo(txn)
    ical = wrap_vcalendar(vtodo)
    etag = make_etag(uid, txn["tag_created_at"], txn.get("note_updated_at"))

    return Response(
        content=ical,
        status_code=200,
        headers={
            "Content-Type": "text/calendar; charset=utf-8",
            "ETag": etag,
        },
    )


def _put_vtodo(conn, uid: str, body: bytes, tag: str) -> Response:
    """Handle PUT — completion removes todo tag, notes sync back.

    - New UIDs (creating reminders): 403 Forbidden
    - STATUS:COMPLETED: removes the tag
    - DESCRIPTION changes: updates transaction_note
    """
    # Block creation of new tasks — we only serve existing transactions
    if not has_tag(conn, uid, tag):
        return Response(
            content=f"Cannot create tasks via CalDAV. Tag a transaction as '{tag}' in the finance UI.".encode(),
            status_code=403,
            headers={"Content-Type": "text/plain"},
        )

    parsed = parse_vtodo(body.decode("utf-8", errors="replace"))

    # Completion → remove the tag
    if parsed["status"] == "COMPLETED":
        remove_tag(conn, uid, tag)
        return Response(status_code=204)

    # Notes sync — update transaction_note from DESCRIPTION
    if parsed["note"] is not None:
        update_note(conn, uid, parsed["note"])
    elif parsed["note"] is None:
        # User cleared the notes field — remove it
        update_note(conn, uid, None)

    return Response(status_code=204)


def _delete_vtodo(conn, uid: str, tag: str) -> Response:
    """Handle DELETE — remove the tag."""
    removed = remove_tag(conn, uid, tag)

    if not removed:
        return Response(status_code=404)
    return Response(status_code=204)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _options_response() -> Response:
    return Response(
        status_code=200,
        headers={
            "DAV": "1, 3, calendar-access",
            "Allow": "OPTIONS, PROPFIND, REPORT, GET, PUT, DELETE",
            "Content-Length": "0",
        },
    )


def _calendar_props(ctag: str) -> list:
    """Build the standard set of calendar properties."""
    return list(_calendar_prop_map(ctag).values())


def _calendar_prop_map(ctag: str) -> dict:
    """Map of (namespace, localname) → (namespace, localname, value) for the tasks calendar."""
    sync_token = f"https://finance.mees.st/caldav/sync/{ctag}"
    return {
        (DAV, "resourcetype"): (
            DAV, "resourcetype",
            make_resourcetype((DAV, "collection"), (CALDAV, "calendar")),
        ),
        (DAV, "displayname"): (DAV, "displayname", "Finance Tasks"),
        (CALDAV, "supported-calendar-component-set"): (
            CALDAV, "supported-calendar-component-set",
            [make_comp("VTODO")],
        ),
        (CS, "getctag"): (CS, "getctag", ctag),
        (DAV, "sync-token"): (DAV, "sync-token", sync_token),
        (ICAL, "calendar-color"): (ICAL, "calendar-color", CALENDAR_COLOR),
        (DAV, "current-user-privilege-set"): (
            DAV, "current-user-privilege-set",
            _privilege_set(),
        ),
    }


def _privilege_set() -> list[ET.Element]:
    """Build current-user-privilege-set with read + write."""
    privs = []
    for priv_name in ("read", "write", "write-content"):
        p = ET.Element(f"{{{DAV}}}privilege")
        ET.SubElement(p, f"{{{DAV}}}{priv_name}")
        privs.append(p)
    return privs


# ── Well-known + server root discovery ───────────────────────────────────────


async def handle_well_known(request: Request) -> Response:
    """Handle /.well-known/caldav — Apple sends PROPFIND here, not just GET."""
    if request.method == "OPTIONS":
        return _options_response()
    # Redirect for both GET and PROPFIND
    return Response(
        status_code=301,
        headers={
            "Location": "/caldav/",
            "DAV": "1, 3, calendar-access",
        },
    )


async def handle_server_root(request: Request) -> Response:
    """Handle PROPFIND/OPTIONS on / — Apple checks DAV capabilities here."""
    if request.method == "OPTIONS":
        return _options_response()

    conn = _get_conn()
    try:
        denied = _check_enabled(conn)
        if denied:
            return denied
        denied = _check_auth(request, conn)
        if denied:
            return denied

        body = await request.body()
        props = parse_propfind(body)

        known = {
            (DAV, "resourcetype"): (DAV, "resourcetype", make_resourcetype((DAV, "collection"))),
            (DAV, "current-user-principal"): (DAV, "current-user-principal", make_href_element("/caldav/principal/")),
        }

        response_props = []
        not_found = []
        if props is None:
            response_props = list(known.values())
        else:
            for ns, local in props:
                key = (ns, local)
                if key in known:
                    response_props.append(known[key])
                else:
                    not_found.append(key)

        resp = {"href": "/", "props": response_props}
        if not_found:
            resp["not_found"] = not_found

        return _xml_response(multistatus(resp))
    finally:
        _put_conn(conn)


# Starlette routes for paths outside /caldav/ that Apple needs
well_known_routes = Router(routes=[
    Route("/caldav", endpoint=handle_well_known, methods=["GET", "PROPFIND", "OPTIONS"]),
])

server_root_routes = Router(routes=[
    Route("/", endpoint=handle_server_root, methods=["PROPFIND", "OPTIONS"]),
])


# ── Router ───────────────────────────────────────────────────────────────────

caldav_router = Router(routes=[
    Route("/", endpoint=handle_root, methods=["PROPFIND", "OPTIONS"]),
    Route("/principal/", endpoint=handle_principal, methods=["PROPFIND", "OPTIONS"]),
    Route("/calendars/", endpoint=handle_calendar_home, methods=["PROPFIND", "OPTIONS"]),
    Route("/calendars/tasks/", endpoint=handle_calendar, methods=["PROPFIND", "REPORT", "OPTIONS"]),
    Route("/calendars/tasks/{uid}.ics", endpoint=handle_vtodo, methods=["GET", "PUT", "DELETE", "OPTIONS"]),
])
