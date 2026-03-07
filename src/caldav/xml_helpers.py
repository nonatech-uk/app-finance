"""WebDAV/CalDAV XML parsing and response generation."""

import xml.etree.ElementTree as ET

DAV = "DAV:"
CALDAV = "urn:ietf:params:xml:ns:caldav"
CS = "http://calendarserver.org/ns/"
ICAL = "http://apple.com/ns/ical/"

# Register namespace prefixes for clean output
ET.register_namespace("D", DAV)
ET.register_namespace("C", CALDAV)
ET.register_namespace("CS", CS)
ET.register_namespace("IC", ICAL)


def parse_propfind(body: bytes) -> list[tuple[str, str]] | None:
    """Parse PROPFIND request body, return list of (namespace, localname).

    Returns None if body is empty or requests allprop.
    """
    if not body or not body.strip():
        return None  # allprop

    root = ET.fromstring(body)

    # Check for allprop
    if root.find(f"{{{DAV}}}allprop") is not None:
        return None

    props = []
    prop_el = root.find(f"{{{DAV}}}prop")
    if prop_el is not None:
        for child in prop_el:
            ns = child.tag.split("}")[0].lstrip("{") if "}" in child.tag else DAV
            local = child.tag.split("}")[1] if "}" in child.tag else child.tag
            props.append((ns, local))

    return props or None


def parse_report(body: bytes) -> dict:
    """Parse REPORT request body.

    Returns dict with:
        report_type: 'calendar-multiget' | 'calendar-query' | 'sync-collection'
        props: list of (namespace, localname)
        hrefs: list of href strings (for multiget)
        sync_token: str or None (for sync-collection)
    """
    root = ET.fromstring(body)

    ns = root.tag.split("}")[0].lstrip("{") if "}" in root.tag else ""
    local = root.tag.split("}")[1] if "}" in root.tag else root.tag

    result = {
        "report_type": local,
        "props": [],
        "hrefs": [],
        "sync_token": None,
    }

    prop_el = root.find(f"{{{DAV}}}prop")
    if prop_el is not None:
        for child in prop_el:
            child_ns = child.tag.split("}")[0].lstrip("{") if "}" in child.tag else DAV
            child_local = child.tag.split("}")[1] if "}" in child.tag else child.tag
            result["props"].append((child_ns, child_local))

    for href_el in root.findall(f"{{{DAV}}}href"):
        if href_el.text:
            result["hrefs"].append(href_el.text.strip())

    sync_el = root.find(f"{{{DAV}}}sync-token")
    if sync_el is not None and sync_el.text:
        result["sync_token"] = sync_el.text.strip()

    return result


def multistatus(*responses: dict) -> bytes:
    """Build a DAV:multistatus XML response.

    Each response dict has:
        href: str
        props: list of (namespace, localname, value_or_element)
        not_found: list of (namespace, localname)  [optional]
    """
    ms = ET.Element(f"{{{DAV}}}multistatus")

    for resp in responses:
        r = ET.SubElement(ms, f"{{{DAV}}}response")
        href = ET.SubElement(r, f"{{{DAV}}}href")
        href.text = resp["href"]

        if resp.get("props"):
            ps = ET.SubElement(r, f"{{{DAV}}}propstat")
            prop = ET.SubElement(ps, f"{{{DAV}}}prop")
            for ns, local, value in resp["props"]:
                el = ET.SubElement(prop, f"{{{ns}}}{local}")
                if isinstance(value, str):
                    el.text = value
                elif isinstance(value, ET.Element):
                    el.append(value)
                elif isinstance(value, list):
                    for child in value:
                        el.append(child)
                # None = empty element
            status = ET.SubElement(ps, f"{{{DAV}}}status")
            status.text = "HTTP/1.1 200 OK"

        if resp.get("not_found"):
            ps = ET.SubElement(r, f"{{{DAV}}}propstat")
            prop = ET.SubElement(ps, f"{{{DAV}}}prop")
            for ns, local in resp["not_found"]:
                ET.SubElement(prop, f"{{{ns}}}{local}")
            status = ET.SubElement(ps, f"{{{DAV}}}status")
            status.text = "HTTP/1.1 404 Not Found"

        # For sync-collection status-only responses (removed items)
        if resp.get("status"):
            st = ET.SubElement(r, f"{{{DAV}}}status")
            st.text = resp["status"]

    return b'<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(ms, encoding="unicode").encode("utf-8")


def make_href_element(href_text: str) -> ET.Element:
    """Create a DAV:href element."""
    el = ET.Element(f"{{{DAV}}}href")
    el.text = href_text
    return el


def make_resourcetype(*types: tuple[str, str]) -> list[ET.Element]:
    """Create child elements for resourcetype.

    Each type is (namespace, localname).
    """
    return [ET.Element(f"{{{ns}}}{local}") for ns, local in types]


def make_comp(name: str) -> ET.Element:
    """Create a CalDAV comp element with name attribute."""
    el = ET.Element(f"{{{CALDAV}}}comp")
    el.set("name", name)
    return el
