import hashlib
import re
from urllib.parse import urlparse, parse_qsl, urlencode, urlunsplit

_TRACKING = {"utm_source","utm_medium","utm_campaign","utm_term","utm_content",
             "spm","from","ref","share_token","fbclid","gclid","yclid"}

_WS=re.compile(r"\s+")

def _norm_text(s: str) -> str:
    return _WS.sub("", s or "")

def norm_url(url: str) -> str:
    sp = urlparse((url or "").strip())
    host = sp.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    query= [(k,v) for k,v in parse_qsl(sp.query, keep_blank_values=True) if k.lower() not in _TRACKING]
    query.sort()
    return urlunsplit(("https", host, sp.path.rstrip("/") or "/",
                       urlencode(query), ""))

def dedup_key(url: str) -> str:
    return hashlib.md5(norm_url(url).encode("utf-8")).hexdigest()

def content_hash(title: str, content: str) -> str:
    return hashlib.md5((_norm_text(title) + "|" + _norm_text(content)).encode("utf-8")).hexdigest()