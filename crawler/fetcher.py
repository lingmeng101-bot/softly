import json
import logging
import random
import time
from typing import NamedTuple

import httpx
from charset_normalizer import detect
from tenacity import (
    retry,
    stop_after_attempt,
    wait_random_exponential,
    retry_if_exception,
    before_sleep_log
)
import config
from config import USER_AGENTS, DEFAULT_HEADERS, Log, Route
from crawler.sites import host_of

log = logging.getLogger(Log.LOG_NAME)

# ---- 可选依赖：缺任何一层都能跑，只是降级链少一层 ----
try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    cffi_requests = None

try:
    import httpcloak
except ImportError:
    httpcloak = None

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


# curl_cffi / httpcloak 自带与指纹匹配的 UA 和 Accept-*，上层只透传业务 header
_BUSINESS_HEADERS = {"referer", "x-requested-with", "ali-ref"}


class FetchFailed(Exception):
    # 降级链判死：换浏览器也一样（如 404），或所有层都失败
    pass


class ProxyDown(Exception):
    # 连续两层连接失败，判定代理挂了，别再往上跳 playwright
    pass


class _TierFail(Exception):
    # 单层失败：记 reason 后交给下一层
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class Fetched(NamedTuple):
    res: object
    tier: str
    attempts: list[tuple[str, str]]


class BrowserResponse:
    # 把 playwright 页面包成 sniff / pipeline 认识的形状

    def __init__(self, html: str, status_code: int, url: str, headers: dict):
        self._html = html
        self.status_code = status_code
        self.url = url
        self.headers = headers or {}

    @property
    def text(self) -> str:
        return self._html

    @property
    def content(self) -> bytes:
        return self._html.encode("utf-8", errors="replace")

    def json(self):
        return json.loads(self._html)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise FetchFailed(f"HTTP {self.status_code}")


def _short(e) -> str:
    s = str(e).replace("\n", " ").strip()
    return s[:160] or type(e).__name__


def _looks_connect(e) -> bool:
    return "connect" in type(e).__name__.lower() or "connect" in str(e).lower()


def _flat_headers(headers) -> dict:
    # httpcloak 的响应头值是 list（同名头多值），压平成字符串，sniff / classify 才认
    if not isinstance(headers, dict):
        try:
            headers = dict(headers or {})
        except Exception:
            return {}
    out = {}
    for k, v in headers.items():
        if isinstance(v, (list, tuple)):
            v = v[0] if v else ""
        out[k] = v
    return out


def _header_value(res, name: str) -> str:
    headers = getattr(res, "headers", None) or {}
    if not hasattr(headers, "get"):
        return ""
    value = headers.get(name)
    if value is None:
        value = headers.get(name.title())
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value or "").lower()


def _slice_business(headers: dict | None) -> dict:
    if not headers:
        return {}
    return {k: v for k, v in headers.items() if k.lower() in _BUSINESS_HEADERS}


def autodetect(content:bytes)->str :
    result=detect(content)
    if (result.get("confidence") or 0) < 0.5 :
        return "utf-8"
    return result.get('encoding') or 'utf-8'


#headers参数配置
def build_header(headers:dict|None =None) -> dict[str, str]:
    h=dict(DEFAULT_HEADERS)
    h['User-Agent'] = random.choice(USER_AGENTS)
    if headers:
        h.update(headers)
    return h


def proxy_alive() -> bool:
    # 探一下 Clash 通不通，挂了就别把国外站挨个超时一遍
    t0 = time.perf_counter()
    try:
        httpx.get(
            config.PROXY_PROBE_URL,
            proxy=config.PROXY_HTTP,
            timeout=config.PROXY_PROBE_TIMEOUT,
        ).raise_for_status()
    except Exception as e:
        log.error("[proxy] probe FAILED timeout=%.1fs (%s) → 跳过不走 DIRECT 的站点",
                  config.PROXY_PROBE_TIMEOUT, _short(e))
        return False
    log.info("[proxy] probe ok  latency=%dms", (time.perf_counter() - t0) * 1000)
    return True


#每层、每个方向一个 client/session，懒创建；DIRECT 一律不设 proxy
_clients: dict[str, httpx.Client] = {}


def _client_for(route: Route) -> httpx.Client:
    key = "direct" if route == Route.DIRECT else "proxy"
    client = _clients.get(key)
    if client is None:
        kw = dict(
            default_encoding=autodetect,
            timeout=httpx.Timeout(connect=5, read=10, write=10, pool=5),
            follow_redirects=True,
        )
        if key == "proxy":
            kw["proxy"] = config.PROXY_HTTP
        client = httpx.Client(**kw)
        _clients[key] = client
    return client


def _should_retry(exc: BaseException) -> bool:
    if isinstance (exc,httpx.RequestError):
        return True
    if isinstance (exc,httpx.HTTPStatusError):
        code=exc.response.status_code
        return code == 429 or 500<=code<600
    return False


@retry(
    stop=stop_after_attempt(4),
    wait=wait_random_exponential(multiplier=1, max=10),
    before_sleep=before_sleep_log(log, logging.WARNING),
    retry=retry_if_exception(_should_retry),
    reraise=True,
)
def _http_get(url: str, headers: dict[str, str] | None, route: Route) -> httpx.Response:
    log.info("开始抓取 url=%s", url)
    res = _client_for(route).get(url, headers=build_header(headers))
    res.raise_for_status()
    return res


def _challenge_hit(res) -> str | None:
    ct = _header_value(res, "content-type")
    if "html" not in ct:
        return None
    try:
        text = res.text or ""
    except Exception:
        return None
    if len(text) > 200_000:
        return None
    head = text[:4000].lower()
    strong = [m for m in ("just a moment", "cf-challenge", "attention required") if m in head]
    if strong:
        return strong[0]
    hits = [m for m in config.CHALLENGE_MARKERS if m in head]
    return ",".join(hits[:2]) if len(hits) >= 2 else None


def _is_challenge(res) -> bool:
    return _challenge_hit(res) is not None


def looks_like_shell(res) -> bool:
    # 200 但只有壳（列表靠 JS 渲染）——传输层看不出来，只能看内容
    ct = _header_value(res, "content-type")
    if "html" not in ct:
        return False
    try:
        text = res.text or ""
    except Exception:
        return False
    return text.count("<a ") < 5 or len(text) < 3000


def _classify(res, tier: str) -> None:
    code = int(getattr(res, "status_code", 200) or 0)
    if code in config.BLOCKED_CODES:
        raise _TierFail(f"HTTP {code}")
    if code >= 500:
        raise _TierFail(f"HTTP {code}")
    if code >= 400:
        # 401 / 404 / 410 换浏览器也一样，别浪费后面三层
        raise FetchFailed(f"{tier} HTTP {code}，换浏览器也一样，不再降级")
    hit = _challenge_hit(res)
    if hit:
        raise _TierFail(f"challenge({hit})")


# ---------------- T0：httpx ----------------

def _tier_httpx(url: str, headers, route: Route):
    try:
        res = _http_get(url, headers, route)
    except httpx.HTTPStatusError as e:
        _classify(e.response, "httpx")
        raise _TierFail(f"HTTP {e.response.status_code}")
    except httpx.ConnectError as e:
        raise _TierFail("connect: " + _short(e))
    except httpx.TimeoutException as e:
        raise _TierFail("timeout: " + _short(e))
    except httpx.RequestError as e:
        raise _TierFail("error: " + type(e).__name__)
    _classify(res, "httpx")
    return res


# ---------------- T1：curl_cffi ----------------

_cffi_sessions: dict[str, object] = {}


def _cffi_session(route: Route):
    key = "direct" if route == Route.DIRECT else "proxy"
    session = _cffi_sessions.get(key)
    if session is None:
        kw = dict(timeout=30, allow_redirects=True)
        if key == "proxy":
            kw["proxies"] = {"http": config.PROXY_HTTP, "https": config.PROXY_HTTP}
        session = cffi_requests.Session(**kw)
        _cffi_sessions[key] = session
    return session


def _tier_curl_cffi(url: str, headers, route: Route):
    session = _cffi_session(route)
    try:
        res = session.get(
            url,
            headers=_slice_business(headers) or None,
            impersonate=config.CURL_CFFI_IMPERSONATE,
            timeout=30,
            allow_redirects=True,
        )
    except Exception as e:
        if _looks_connect(e):
            raise _TierFail("connect: " + _short(e))
        raise _TierFail(f"error {type(e).__name__}: {_short(e)}")
    _classify(res, "curl_cffi")
    return res


# ---------------- T2：httpcloak ----------------

_cloak_sessions: dict[str, object] = {}


def _httpcloak_ready() -> bool:
    try:
        httpcloak.available_presets()
        return True
    except Exception as e:
        log.warning("httpcloak 不可用（%s），降级链跳过该层", _short(e))
        return False


def _cloak_session(route: Route):
    key = "direct" if route == Route.DIRECT else "proxy"
    session = _cloak_sessions.get(key)
    if session is None:
        session = httpcloak.Session(
            preset=config.HTTPCLOAK_PRESET,
            proxy=None if key == "direct" else config.PROXY_HTTP,
            timeout=30,
            http_version=config.HTTPCLOAK_HTTP_VERSION,   # 必须 auto，见任务书 h3 坑
        )
        _cloak_sessions[key] = session
    return session


def _tier_httpcloak(url: str, headers, route: Route):
    session = _cloak_session(route)
    try:
        res = session.get(url, headers=_slice_business(headers) or None)
    except Exception as e:
        if _looks_connect(e):
            raise _TierFail("connect: " + _short(e))
        raise _TierFail(f"error {type(e).__name__}: {_short(e)}")
    res.headers = _flat_headers(res.headers)
    _classify(res, "httpcloak")
    return res


# ---------------- T3：playwright ----------------

_pw = None
_pw_browser = None
_pw_broken: str | None = None


def _ensure_browser(route: Route):
    global _pw, _pw_browser, _pw_broken
    if _pw_broken:
        raise _TierFail("playwright 不可用: " + _pw_broken)
    if _pw_browser is not None:
        return _pw_browser
    try:
        _pw = sync_playwright().start()
        kwargs = dict(
            headless=config.PLAYWRIGHT_HEADLESS,
            args=["--disable-blink-features=AutomationControlled"],
        )
        if route != Route.DIRECT:
            kwargs["proxy"] = {"server": config.PROXY_HTTP}
        _pw_browser = _pw.chromium.launch(**kwargs)
    except Exception as e:
        _pw_broken = f"{type(e).__name__}: {_short(e)}"
        log.warning("[降级] playwright 启动失败，后续跳过该层: %s", _short(e))
        raise _TierFail("playwright 不可用: " + _pw_broken)
    return _pw_browser


def _tier_playwright(url: str, headers, route: Route):
    browser = _ensure_browser(route)
    try:
        page = browser.new_page()
        try:
            resp = page.goto(url, wait_until=config.PLAYWRIGHT_WAIT,
                             timeout=config.PLAYWRIGHT_TIMEOUT_MS)
            res = BrowserResponse(
                page.content(),
                resp.status if resp else 200,
                page.url,
                dict(resp.headers if resp else {}),
            )
        finally:
            page.close()
    except _TierFail:
        raise
    except Exception as e:
        raise _TierFail(f"error {type(e).__name__}: {_short(e)}")
    _classify(res, "playwright")
    return res


# ---------------- 可用层与粘滞 ----------------

_READY = {
    "httpx": True,
    "curl_cffi": cffi_requests is not None,
    "httpcloak": httpcloak is not None and _httpcloak_ready(),
    "playwright": sync_playwright is not None,
}
TIERS = tuple(t for t in config.FETCH_TIERS if _READY.get(t))
TIER_INDEX = {t: i for i, t in enumerate(TIERS)}

if cffi_requests is None:
    log.warning("curl_cffi 未安装，降级链跳过该层")
if httpcloak is None:
    log.warning("httpcloak 未安装，降级链跳过该层")
if sync_playwright is None:
    log.warning("playwright 未安装，降级链跳过该层")

_TIER_HINT: dict[str, str] = {}
TIER_HITS: dict[str, int] = {}


def reset_hints() -> None:
    _TIER_HINT.clear()


def log_tiers() -> None:
    log.info("降级链: %s", " → ".join(TIERS))


def _remember_tier(host: str, tier: str) -> None:
    # 只允许往上提，某个站一旦需要 curl_cffi，之后一直从 curl_cffi 起
    cur = _TIER_HINT.get(host)
    if cur is None or TIER_INDEX[tier] > TIER_INDEX[cur]:
        _TIER_HINT[host] = tier


def _start_tier(url: str, min_tier: str | None) -> str:
    host = host_of(url)
    hint = _TIER_HINT.get(host)
    start = hint if hint in TIER_INDEX else TIERS[0]
    if min_tier and min_tier in TIER_INDEX:
        if TIER_INDEX[min_tier] > TIER_INDEX[start]:
            start = min_tier
    return start


_TIER_FUNCS = {
    "httpx": _tier_httpx,
    "curl_cffi": _tier_curl_cffi,
    "httpcloak": _tier_httpcloak,
    "playwright": _tier_playwright,
}


def rotate_node(group: str | None = None) -> bool:
    # 走 Clash 的 external-controller 换节点；失败一律当没发生
    group = group or config.CLASH_GROUP
    headers = {"Authorization": f"Bearer {config.CLASH_SECRET}"} if config.CLASH_SECRET else {}
    try:
        r = httpx.get(f"{config.CLASH_API}/proxies/{group}", headers=headers, timeout=3)
        r.raise_for_status()
        data = r.json()
        nodes = [n["name"] for n in data.get("all") or [] if isinstance(n, dict)]
        cur = data.get("now")
        nxt = next((n for n in nodes if n != cur), None)
        if not nxt:
            return False
        httpx.put(f"{config.CLASH_API}/proxies/{group}", headers=headers,
                  json={"name": nxt}, timeout=3).raise_for_status()
        log.warning("换节点: %s → %s", cur, nxt)
        return True
    except Exception as e:
        log.info("换节点失败（忽略）: %s", _short(e))
        return False


def fetch(
    url: str,
    headers: dict[str, str] | None = None,
    *,
    min_tier: str | None = None,
    route: Route | str = Route.AUTO,
    rotate_on_block: bool = False,
    proxy_group: str | None = None,
) -> Fetched:
    route = Route(route)
    start = _start_tier(url, min_tier)
    host = host_of(url)
    attempts: list[tuple[str, str]] = []
    connect_fails = 0
    last_reason = ""

    for i in range(TIER_INDEX[start], len(TIERS)):
        tier = TIERS[i]
        nxt = TIERS[i + 1] if i + 1 < len(TIERS) else "全灭"
        try:
            res = _TIER_FUNCS[tier](url, headers, route)
        except _TierFail as e:
            attempts.append((tier, e.reason))
            last_reason = e.reason
            connect_fails = connect_fails + 1 if e.reason.startswith("connect") else 0
            log.warning("[降级] %s  %s → %s  原因=%s", host, tier, nxt, e.reason)
            # 代理挂了的话，后面每层都是同样超时，没意义
            if route != Route.DIRECT and connect_fails >= 2:
                raise ProxyDown(f"{host} 连续 {connect_fails} 层连接失败: {e.reason}")
            continue

        _remember_tier(host, tier)
        TIER_HITS[tier] = TIER_HITS.get(tier, 0) + 1
        return Fetched(res, tier, attempts)

    # 四层全灭；403 时换个节点再给 curl_cffi 一次机会（可选）
    if rotate_on_block and cffi_requests is not None and "HTTP 403" in last_reason:
        if rotate_node(proxy_group):
            try:
                res = _tier_curl_cffi(url, headers, route)
            except _TierFail as e:
                attempts.append(("curl_cffi(换节点)", e.reason))
            else:
                _remember_tier(host, "curl_cffi")
                TIER_HITS["curl_cffi"] = TIER_HITS.get("curl_cffi", 0) + 1
                return Fetched(res, "curl_cffi", attempts)

    raise FetchFailed(f"{host} 降级链全灭（起始 {start}）: {last_reason or '无可用层'}")


def close_all() -> None:
    global _pw, _pw_browser
    for client in _clients.values():
        try:
            client.close()
        except Exception:
            pass
    _clients.clear()
    for session in _cffi_sessions.values():
        try:
            session.close()
        except Exception:
            pass
    _cffi_sessions.clear()
    for session in _cloak_sessions.values():
        try:
            session.close()
        except Exception:
            pass
    _cloak_sessions.clear()
    if _pw_browser is not None:
        try:
            _pw_browser.close()
        finally:
            _pw_browser = None
    if _pw is not None:
        try:
            _pw.stop()
        finally:
            _pw = None