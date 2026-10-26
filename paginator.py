import logging
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from config import Log

log = logging.getLogger(Log.LOG_NAME)

# 仅支持 page 型分页；offset 步长无法从 URL 推断
ALIASES: dict[str, set[str]] = {
    "page": {"page", "p", "pg", "paged", "pageno", "pagenum", "pn", "pageindex"},
}
LOOKUP = {a.lower(): std for std, names in ALIASES.items() for a in names}

# 以下参数名用于识别 offset 型分页，仅作提示，不参与推算
_OFFSET_NAMES = {"offset", "start", "from", "skip", "begin", "startindex"}


def find_param(url: str):
    for k, v in parse_qsl(urlsplit(url).query, keep_blank_values=True):
        std = LOOKUP.get(k.lower())
        if std:
            return std, k, v
    return None


def _has_offset_param(url: str) -> bool:
    return any(k.lower() in _OFFSET_NAMES for k, v in parse_qsl(urlsplit(url).query, keep_blank_values=True))


def next_page_url(url: str) -> str | None:
    hit = find_param(url)
    if not hit:
        if _has_offset_param(url):
            log.info("检测到 offset 型分页，不支持自动翻页，请为该站实现 next_page_url")
        return None
    std, key, raw = hit
    if std != "page" or not raw.isdigit():
        return None
    sp = urlsplit(url)
    q = [(k, str(int(v) + 1) if k == key else v) for k, v in parse_qsl(sp.query, keep_blank_values=True)]
    return urlunsplit((sp.scheme, sp.netloc, sp.path, urlencode(q), ""))
