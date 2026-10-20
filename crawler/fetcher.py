import json
import logging
import os
import random
import re
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

# ---- 可选依赖：缺失时跳过对应层级 ----
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


# curl_cffi / httpcloak 自带与指纹匹配的请求头，此处仅透传业务字段
_LINK_RE = re.compile(r"<a[\s>]", re.I)
_BUSINESS_HEADERS = {"referer", "x-requested-with", "ali-ref"}


class FetchFailed(Exception):
    # 降级链终止：非指纹相关的 4xx，或全部层级失败
    pass


class ProxyDown(Exception):
    # 连续两层连接失败时判定代理不可用，终止后续降级
    pass


class _TierFail(Exception):
    # 单层失败：记录原因后进入下一层
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class Fetched(NamedTuple):
    res: object
    tier: str
    attempts: list[tuple[str, str]]


class BrowserResponse:
    # 将 playwright 页面适配为 sniff / pipeline 使用的响应接口

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
    # httpcloak 响应头值为列表（多值），统一压平为字符串
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


# 构造请求头（默认头 + 随机 UA）
def build_header(headers:dict|None =None) -> dict[str, str]:
    h=dict(DEFAULT_HEADERS)
    # 人工完成验证后可指定与浏览器一致的 UA 和 Cookie
    h['User-Agent'] = config.HTTP_USER_AGENT or random.choice(USER_AGENTS)
    if config.HTTP_COOKIE:
        h["Cookie"] = config.HTTP_COOKIE
    if headers:
        h.update(headers)
    return h


def proxy_alive() -> bool:
    # 代理可用性探测：不可用时跳过非直连站点，避免逐站超时
    t0 = time.perf_counter()
    try:
        httpx.get(
            config.PROXY_PROBE_URL,
            proxy=config.PROXY_HTTP,
            timeout=config.PROXY_PROBE_TIMEOUT,
            trust_env=False,
        ).raise_for_status()
    except Exception as e:
        log.error("[proxy] probe FAILED timeout=%.1fs (%s) → 跳过非 DIRECT 站点",
                  config.PROXY_PROBE_TIMEOUT, _short(e))
        return False
    log.info("[proxy] probe ok  latency=%dms", (time.perf_counter() - t0) * 1000)
    return True


def network_alive(url: str) -> bool:
    # 直连可达性探测：直接探测目标站，避免用公网探针误判国内外站点的可达性
    t0 = time.perf_counter()
    try:
        res = httpx.get(url, timeout=config.PROXY_PROBE_TIMEOUT,
                        follow_redirects=True, trust_env=False)
    except Exception as e:
        log.error("[net] 直连不可达 %s (%s)", url, _short(e))
        return False
    log.info("[net] 直连可达  status=%s  latency=%dms",
             res.status_code, (time.perf_counter() - t0) * 1000)
    return True


# 按路由方向懒创建客户端；DIRECT 不设置代理
_clients: dict[str, httpx.Client] = {}


def _client_for(route: Route) -> httpx.Client:
    key = "direct" if route == Route.DIRECT else "proxy"
    client = _clients.get(key)
    if client is None:
        kw = dict(
            default_encoding=autodetect,
            timeout=httpx.Timeout(connect=5, read=10, write=10, pool=5),
            follow_redirects=True,
            trust_env=False,   # 不读取 HTTP(S)_PROXY 环境变量，DIRECT 才是真直连
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


_CHALLENGE_TEXT = ("just a moment", "attention required", "cf-challenge", "cf_chl_",
                   "checking your browser")


def _page_challenge(html: str) -> bool:
    # 仅看首屏文本，用于判断浏览器是否还停在挑战页
    head = (html or "")[:4000].lower()
    return any(marker in head for marker in _CHALLENGE_TEXT)


def looks_like_shell(res) -> bool:
    # 判断 200 响应是否为仅含占位节点的空壳页面
    ct = _header_value(res, "content-type")
    if "html" not in ct:
        return False
    try:
        text = res.text or ""
    except Exception:
        return False
    return len(_LINK_RE.findall(text)) < 5 or len(text) < 3000


def _classify(res, tier: str) -> None:
    code = int(getattr(res, "status_code", 200) or 0)
    if code in config.BLOCKED_CODES:
        raise _TierFail(f"HTTP {code}")
    if code >= 500:
        raise _TierFail(f"HTTP {code}")
    if code >= 400:
        # 非指纹相关状态码：终止降级
        raise FetchFailed(f"{tier} HTTP {code}：非传输层问题，终止降级")
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
        kw = dict(timeout=30, allow_redirects=True, trust_env=False)
        if key == "proxy":
            kw["proxies"] = {"http": config.PROXY_HTTP, "https": config.PROXY_HTTP}
        else:
            # libcurl 默认读取 HTTP(S)_PROXY 环境变量；显式置空才保证真正直连
            kw["proxies"] = {"http": "", "https": ""}
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
            http_version=config.HTTPCLOAK_HTTP_VERSION,   # HTTP/3 需要 UDP 中继，保持 auto 自动回落 h2
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
_pw_browsers: dict[str, object] = {}
_pw_broken: dict[str, str] = {}


def _ensure_browser(route: Route):
    global _pw
    key = "direct" if route == Route.DIRECT else "proxy"
    broken = _pw_broken.get(key)
    if broken:
        raise _TierFail("playwright 不可用: " + broken)
    browser = _pw_browsers.get(key)
    if browser is not None:
        return browser
    try:
        if _pw is None:
            _pw = sync_playwright().start()
        kwargs = dict(
            headless=config.PLAYWRIGHT_HEADLESS,
            args=["--disable-blink-features=AutomationControlled"],
        )
        # 代理在 launch 时绑定，直连与代理需分别启动浏览器实例
        if key == "proxy":
            kwargs["proxy"] = {"server": config.PROXY_HTTP}
        else:
            # DIRECT 时禁用浏览器自身的系统代理，确保为真正直连
            kwargs["args"].append("--no-proxy-server")
        browser = _pw.chromium.launch(**kwargs)
        _pw_browsers[key] = browser
    except Exception as e:
        _pw_broken[key] = f"{type(e).__name__}: {_short(e)}"
        log.warning("[降级] playwright 启动失败（%s），跳过该路由的该层: %s", key, _short(e))
        raise _TierFail("playwright 不可用: " + _pw_broken[key])
    return browser


def _tier_playwright(url: str, headers, route: Route):
    browser = _ensure_browser(route)
    ctx = None
    try:
        state = config.PLAYWRIGHT_STORAGE_STATE
        if state and not os.path.isabs(state):
            state = str(config.BASE_DIR / state)
        state = state if state and os.path.exists(state) else ""
        ua = config.HTTP_USER_AGENT or None
        if state or ua:
            kw = {}
            if state:
                kw["storage_state"] = state
            if ua:
                kw["user_agent"] = ua
            ctx = browser.new_context(**kw)
            page = ctx.new_page()
        else:
            page = browser.new_page()
        try:
            resp = page.goto(url, wait_until=config.PLAYWRIGHT_WAIT,
                             timeout=config.PLAYWRIGHT_TIMEOUT_MS)
            status = resp.status if resp else 200
            html = page.content()
            # 挑战页返回 403 是常态，给浏览器留出执行 JS 并重载的时间
            if status in config.BLOCKED_CODES or _page_challenge(html):
                waited = 0
                while waited < config.PLAYWRIGHT_CHALLENGE_WAIT:
                    page.wait_for_timeout(1000)
                    waited += 1
                    html = page.content()
                    if not _page_challenge(html):
                        status = 200
                        break
                else:
                    log.warning("[降级] playwright 挑战未通过（等待 %ss）: %s",
                                waited, url)
            res = BrowserResponse(
                html,
                status,
                page.url,
                dict(resp.headers if resp else {}),
            )
        finally:
            page.close()
            if ctx is not None:
                ctx.close()
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
    # 粘滞策略：仅在层级提升时记录，不回退
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


_GROUP_TYPES = {"Selector", "URLTest", "Fallback", "LoadBalance"}
_NOT_A_NODE = {"DIRECT", "REJECT", "REJECT-DROP", "COMPATIBLE", "PASS"}


def rotate_node(group: str | None = None) -> bool:
    # 通过 Clash external-controller 切换节点；失败时静默返回
    group = group or config.CLASH_GROUP
    headers = {"Authorization": f"Bearer {config.CLASH_SECRET}"} if config.CLASH_SECRET else {}
    try:
        r = httpx.get(f"{config.CLASH_API}/proxies", headers=headers, timeout=5,
                      trust_env=False)
        r.raise_for_status()
        proxies = r.json().get("proxies") or {}
        data = proxies.get(group) or {}
        cur = data.get("now")
        # 仅选取真实节点，排除策略组与 DIRECT/REJECT
        candidates = [
            name for name in (data.get("all") or [])
            if isinstance(name, str) and name != cur and name not in _NOT_A_NODE
            and (proxies.get(name) or {}).get("type") not in _GROUP_TYPES
        ]
        if not candidates:
            log.info("换节点跳过：组 %s 没有可切换的节点", group)
            return False
        nxt = candidates[0]
        httpx.put(f"{config.CLASH_API}/proxies/{group}", headers=headers,
                  json={"name": nxt}, timeout=3, trust_env=False).raise_for_status()
        log.warning("换节点: %s  %s → %s", group, cur, nxt)
        return True
    except Exception as e:
        log.info("节点切换失败（忽略）: %s", _short(e))
        return False


def fetch(
    url: str,
    headers: dict[str, str] | None = None,
    *,
    min_tier: str | None = None,
    route: Route | str = Route.AUTO,
    rotate_on_block: bool = False,
    rotate_group: str | None = None,
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
            # 代理不可用时后续层级同样超时，提前终止
            if route != Route.DIRECT and connect_fails >= 2:
                raise ProxyDown(f"{host} 连续 {connect_fails} 层连接失败: {e.reason}")
            continue

        _remember_tier(host, tier)
        TIER_HITS[tier] = TIER_HITS.get(tier, 0) + 1
        return Fetched(res, tier, attempts)

    # 全部层级失败且原因为 403 时，切换节点后重试一次 curl_cffi
    if rotate_on_block and cffi_requests is not None and "HTTP 403" in last_reason:
        if rotate_node(rotate_group):
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
    global _pw
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
    for browser in _pw_browsers.values():
        try:
            browser.close()
        except Exception:
            pass
    _pw_browsers.clear()
    _pw_broken.clear()   # 允许下次重新尝试启动
    if _pw is not None:
        try:
            _pw.stop()
        finally:
            _pw = None