import re
from typing import Literal, Optional

import httpx

MIME_RE = re.compile(r"[a-z0-9!#$&^_.+\-]+/[a-z0-9!#$&^_.+\-]+", re.I)


def extract_mime(res: httpx.Response) -> Optional[str]:
    raw = res.headers.get("content-type", "")
    if not raw:
        return None

    m = MIME_RE.search(raw)
    return m.group(0).lower() if m else None


def sniff(res: httpx.Response) -> Literal["json", "html", "xml", "text"]:
    mime = extract_mime(res)
    if mime:
        if mime in ("application/json", "text/json") or mime.endswith("+json"):
            return "json"
        if mime in ("text/html", "application/xhtml+xml"):
            return "html"
        if mime in ("text/xml", "application/xml") or mime.endswith("+xml"):
            return "xml"

    try:
        text = res.text[:1000].lstrip("\ufeff \t\r\n").lower()
    except Exception:
        return "text"

    if text.startswith(("{", "[")):
        return "json"
    if text.startswith("<?xml"):
        return "xml"
    if text.startswith("<"):
        return "html"
    return "text"
