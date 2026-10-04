# -*- coding: utf-8 -*-
"""30aitool.com（WordPress）—— 工具导航

不解析 HTML：/toolsss/ 是客户端渲染的，列表走 WP REST 接口。
    GET {API}?per_page=100&page=1
    tool 151 条 / resource 82 / skill 35 / tool_collection 9
字段是嵌套的（title.rendered / excerpt.rendered），通用的平铺 _pick 取不到，
所以单列一个站。

分页：响应头 X-WP-TotalPages 给总页数，靠 ?page=N 翻。
"""

import re
from urllib.parse import urljoin

API = "https://www.30aitool.com/wp-json/wp/v2/tool"
PER_PAGE = 100

_TAG_RE = re.compile(r"<[^>]+>")


def _rendered(v) -> str:
    """WP 的字段常常套着 {"rendered": "..."} 这层壳，拆掉它。"""
    if isinstance(v, dict):
        v = v.get("rendered", "")
    return _TAG_RE.sub("", str(v or "")).strip()


def parse_list(data, source: str) -> list[dict]:
    """WP REST 返回的是一个数组，每条至少给 url。"""
    if isinstance(data, dict):                 # 万一哪天包了一层
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
