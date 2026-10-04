from typing import Optional , Literal ,Any , List ,Dict
from bs4 import BeautifulSoup
from models import Article
from urllib.parse import urljoin
from guard import check
import httpx
import re
from config import Log
import logging

#时间
DATE_RE = re.compile(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}")

MIME_RE = re.compile(r"[a-z0-9!#$&^_.+\-]+/[a-z0-9!#$&^_.+\-]+", re.I)

#统一
FIELD_ALIASES = {
    "title":   {"title","name","subject","headline","caption"},
    "url":     {"url","link","href","detailurl"},
    "time":    {"time","date","pubdate","publishtime","publishedat","updatetime"},
    "content": {"content","body","desc","description","summary"},
}

def _pick(obj: dict, std: str):
    aliases = FIELD_ALIASES.get(std, set())
    low = {re.sub(r"[_\-]", "", k.lower()): v for k, v in obj.items()}
    for alias in aliases:
        if alias in low:
            return low[alias]
    return None
log=logging.getLogger(Log.LOG_NAME)

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

def find_list(data: Any) -> Optional[List[Dict[str, Any]]]:
    best, best_score = None, 0

    def walk(node):
        nonlocal best, best_score
        if isinstance(node, list):
            ds = [x for x in node if isinstance(x, dict)]
            if len(ds) >= 2:
                keys = set(ds[0])
                if keys:                                   # ← 就这一句是我欠的
                    overlap = sum(len(keys & set(d)) for d in ds) / (len(keys) * len(ds))
                    if len(ds) * overlap > best_score:
                        best, best_score = ds, len(ds) * overlap
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)

    walk(data)
    return best

def auto_json(data, source):
    items=find_list(data)
    if items is None:
        return []
    result=[]
    for item in items:
        url=urljoin(source, _pick(item,"url") or "")
        if not url:
            continue
        result.append(Article(source=source, url=url,
                              title=_pick(item,"title") or "",
                              publish_time=_pick(item,"time"),
                              content=_pick(item,"content") or ""))
    return result
def find_blocks(soup):                  # 找"同一个 tag+class 重复最多"的那组兄弟
    best, n = [], 0
    for parent in soup.find_all(True):
        groups = {}
        for c in parent.find_all(recursive=False):
            sig = (c.name, tuple(sorted(c.get("class", []))))
            groups.setdefault(sig, []).append(c)
        for kids in groups.values():
            if len(kids) >= 3 and any(k.find("a") for k in kids) and len(kids) > n:
                best, n = kids, len(kids)
    return best

def auto_html(soup, source):
    out = []
    for block in find_blocks(soup):
        a = block.find("a", href=True)
        if not a: continue
        m = DATE_RE.search(block.get_text(" ", strip=True))
        out.append(Article(source=source, url=urljoin(source, a["href"]),
                          title=a.get_text(strip=True),
                          publish_time=m.group(0) if m else None))
    return out

def parse_json(data: Any, source: str):
    err = check(data)
    if err:
        log.warning("接口报错：%s", err)
        return []
    return auto_json(data, source)

def parse_judge(res: httpx.Response):
    kind = sniff(res)
    page = str(res.url)
    if kind == "json":
        try:
            return parse_json(res.json(), source=page)
        except Exception as e:
            log.warning("自称 json 但解析失败，退回 html：%s", e)
            kind = "html"
    if kind in ("html", "xml", "text"):
        return auto_html(BeautifulSoup(res.text, "lxml"), source=page)
    return []