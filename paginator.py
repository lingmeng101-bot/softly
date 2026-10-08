from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

ALIASES: dict[str, set[str]] = {
    "page":   {"page", "p", "pg", "paged", "pageno", "pagenum", "pn", "pageindex"},
    "offset": {"offset", "start", "from", "skip", "begin", "startindex"},
}
LOOKUP = {a.lower(): std for std, names in ALIASES.items() for a in names}


def find_param(url: str):
    for k, v in parse_qsl(urlsplit(url).query, keep_blank_values=True):
        std = LOOKUP.get(k.lower())
        if std:
            return std, k, v
    return None 


def next_page_url(url: str) -> str | None:
    hit = find_param(url)
    if not hit:
        return None
    std, key, raw = hit
    if std != "page" or not raw.isdigit():
        return None
    sp = urlsplit(url)
    q = [(k, str(int(v) + 1) if k == key else v)
         for k, v in parse_qsl(sp.query, keep_blank_values=True)]
    return urlunsplit((sp.scheme, sp.netloc, sp.path, urlencode(q), ""))