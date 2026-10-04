
import re
from urllib.parse import urljoin

API = "https://www.30aitool.com/wp-json/wp/v2/tool"
PER_PAGE = 100

_TAG_RE = re.compile(r"<[^>]+>")


def _rendered(v) -> str:

    if isinstance(v, dict):
        v = v.get("rendered", "")
    return _TAG_RE.sub("", str(v or "")).strip()


def parse_list(data, source: str) -> list[dict]:

    if isinstance(data, dict):
        data = data.get("data") or data.get("items") or []

    records = []
    for item in data or []:
        if not isinstance(item, dict):
            continue
        url = urljoin(source, str(item.get("link") or ""))
        title = _rendered(item.get("title"))
        if not url or not title:
            continue
        records.append({
            "day": str(item.get("date") or "")[:10],
            "title": title,
            "summary": _rendered(item.get("excerpt"))[:60],
            "url": url,
        })
    return records
