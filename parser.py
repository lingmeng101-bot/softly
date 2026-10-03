from typing import Optional , Literal
from guard import check
import httpx
import re
from config import Log
import logging

FIELD_ALIASES = {
    "title":   {"title","name","subject","headline","caption"},
    "url":     {"url","link","href","detailurl"},
    "time":    {"time","date","pubdate","publishtime","publishedat","updatetime"},
    "content": {"content","body","desc","description","summary"},
}

log=logging.getLogger(Log.LOG_NAME)

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

def find_list(data):                    # 找"含最多同构对象"的那个数组
    best, score = None, 0
    def walk(node):
        nonlocal best, score
        if isinstance(node, list):
            ds = [x for x in node if isinstance(x, dict)]
            if len(ds) >= 2:
                keys = set(ds[0])
                overlap = sum(len(keys & set(d)) for d in ds) / (len(keys)*len(ds))
                if len(ds)*overlap > score:
                    best, score = ds, len(ds)*overlap
            for x in node: walk(x)
        elif isinstance(node, dict):
            for v in node.values(): walk(v)
    walk(data); return best

def parse_json(data:dict) -> dict :
    err=check(data)
    if err:
        return [], err
    else:
        for


def parse_judge(res: httpx.Response) -> Literal["dict"]:
    kind=sniff(res)
    if kind=="json":
        return parse_json(res.json())

