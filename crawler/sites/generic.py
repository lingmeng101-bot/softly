import logging
import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from config import Log
from crawler.guard import check
from models import Article

log = logging.getLogger(Log.LOG_NAME)

# 发布时间识别
DATE_RE = re.compile(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}")

# 字段别名归一
FIELD_ALIASES = {
    "title": {"title", "name", "subject", "headline", "caption"},
    "url": {"url", "link", "href", "detailurl"},
    "time": {"time", "date", "pubdate", "publishtime", "publishedat", "updatetime"},
    "content": {"content", "body", "desc", "description", "summary"},
}


def _pick(obj: dict, std: str):
    aliases = FIELD_ALIASES.get(std, set())
    low = {re.sub(r"[_\-]", "", k.lower()): v for k, v in obj.items()}
    for alias in aliases:
        if alias in low:
            return low[alias]
    return None


def _text(v) -> str:
    # 兼容 {"rendered": ...} 一类嵌套字段
    if isinstance(v, dict):
        v = v.get("rendered") or v.get("text") or v.get("value")
    if isinstance(v, (list, tuple)):
        v = " ".join(_text(x) for x in v)
    return "" if v is None else str(v).strip()


def find_list(data: Any) -> list[dict[str, Any]] | None:
    best: list | None = None
    best_score: float = 0.0

    def walk(node):
        nonlocal best, best_score
        if isinstance(node, list):
            ds = [x for x in node if isinstance(x, dict)]
            if len(ds) >= 2:
                keys = set(ds[0])
                if keys:
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
    items = find_list(data)
    if items is None:
        return []
    result = []
    for item in items:
        raw = _pick(item, "url")
        # urljoin 对空串会返回列表页自身，需先判空
        if not raw or not str(raw).strip():
            continue
        url = urljoin(source, str(raw).strip())
        result.append(
            Article(
                source=source,
                url=url,
                title=_text(_pick(item, "title")),
                publish_time=_text(_pick(item, "time")) or None,
                content=_text(_pick(item, "content")),
            )
        )
    return result


def find_blocks(soup):  # 找"同一个 tag+class 重复最多"的那组兄弟
    best, n = [], 0
    for parent in soup.find_all(True):
        groups: dict[tuple, list] = {}
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
        if not a:
            continue
        m = DATE_RE.search(block.get_text(" ", strip=True))
        out.append(
            Article(source=source, url=urljoin(source, a["href"]), title=a.get_text(strip=True), publish_time=m.group(0) if m else None)
        )
    return out


def parse_json(data: Any, source: str):
    err = check(data)
    if err:
        log.warning("接口报错：%s", err)
        return []
    return auto_json(data, source)


def parse_list(payload, source: str) -> list:

    if isinstance(payload, (dict, list)):
        return parse_json(payload, source)
    return auto_html(BeautifulSoup(payload, "lxml"), source=source)
