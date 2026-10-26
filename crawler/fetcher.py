import asyncio
import inspect
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
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None


# curl_cffi / httpcloak 自带与指纹匹配的请求头，此处仅透传业务字段
_LINK_RE = re.compile(r"<a[\s>]", re.I)
_BUSINESS_HEADERS = {"referer", "x-requested-with", "ali-ref"}


class FetchFailed(Exception):
    # 降级链终止：非指纹相关的 4xx，或全部层级失败
    pass


class ProxyDown(Exception):
    # 代理出口连不上；AUTO 据此回落直连，FORCE 直接失败
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


# ---------------- 路由：尝试序列 ----------------
# 每个 Route 展开成一串 leg，按顺序尝试；连不上才轮到下一个 leg。
LEG_PROXY = "proxy"
LEG_DIRECT = "direct"

_LEGS: dict[Route, tuple[str, ...]] = {
    Route.DIRECT: (LEG_DIRECT,),
    Route.AUTO:   (LEG_PROXY, LEG_DIRECT),
    Route.FORCE:  (LEG_PROXY,),
}

# 本轮内已确认代理连不上：后续站点不再重复等待连接超时
_PROXY_DOWN = False


def _leg_of(route) -> str:
    # 接受 Route 或 leg 字符串，统一成 leg
    if route == LEG_DIRECT or route == LEG_PROXY:
        return route
    return LEG_DIRECT if Route(route) == Route.DIRECT else LEG_PROXY


def _legs_for(route: Route) -> tuple[str, ...]:
    if not _PROXY_DOWN:
        return _LEGS[route]
    if route == Route.FORCE:
        return ()
    return (LEG_DIRECT,)


# 按 leg 懒创建客户端；direct 不设置代理
_clients: dict[str, httpx.AsyncClient] = {}


def _client_for(route) -> httpx.AsyncClient:
    key = _leg_of(route)
    client = _clients.get(key)
    if client is None:
        kw = dict(
            default_encoding=autodetect,
            timeout=httpx.Timeout(connect=5, read=10, write=10, pool=5),
            follow_redirects=True,
            trust_env=False,   # 不读取 HTTP(S)_PROXY 环境变量，直连才是真直连
        )
        if key == LEG_PROXY:
            kw["proxy"] = config.PROXY_HTTP
        client = httpx.AsyncClient(**kw)
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
async def _http_get(url: str, headers: dict[str, str] | None, route) -> httpx.Response:
    log.info("开始抓取 url=%s", url)
    res = await _client_for(route).get(url, headers=build_header(headers))
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

async def _tier_httpx(url: str, headers, route):
    try:
        res = await _http_get(url, headers, route)
    except httpx.HTTPStatusError as e:
        _classify(e.response, "httpx")
        raise _TierFail(f"HTTP {e.response.status_code}")
    except httpx.ConnectError as e:
        raise _TierFail("connect: " + _short(e))
    except httpx.ConnectTimeout as e:
        raise _TierFail("connect: " + _short(e))
    except httpx.TimeoutException as e:
        # 读取/写入超时说明连接已建立，不能算作出口不通
        raise _TierFail("timeout: " + _short(e))
    except httpx.RequestError as e:
        raise _TierFail("error: " + type(e).__name__)
    _classify(res, "httpx")
    return res


# ---------------- T1：curl_cffi ----------------

_cffi_sessions: dict[str, object] = {}


def _cffi_session(route):
    key = _leg_of(route)
    session = _cffi_sessions.get(key)
    if session is None:
        kw = dict(timeout=30, allow_redirects=True, trust_env=False)
        if key == LEG_PROXY:
            kw["proxies"] = {"http": config.PROXY_HTTP, "https": config.PROXY_HTTP}
        else:
            # libcurl 默认读取 HTTP(S)_PROXY 环境变量；显式置空才保证真正直连
            kw["proxies"] = {"http": "", "https": ""}
        session = cffi_requests.AsyncSession(**kw)
        _cffi_sessions[key] = session
    return session


async def _tier_curl_cffi(url: str, headers, route):
    session = _cffi_session(route)
    try:
        res = await session.get(
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


def _cloak_session(route):
    key = _leg_of(route)
    session = _cloak_sessions.get(key)
    if session is None:
        session = httpcloak.Session(
            preset=config.HTTPCLOAK_PRESET,
            proxy=None if key == LEG_DIRECT else config.PROXY_HTTP,
            timeout=30,
            http_version=config.HTTPCLOAK_HTTP_VERSION,   # HTTP/3 需要 UDP 中继，保持 auto 自动回落 h2
        )
        _cloak_sessions[key] = session
    return session


async def _tier_httpcloak(url: str, headers, route):
    session = _cloak_session(route)
    try:
        # httpcloak 没有异步接口，放到线程里避免阻塞事件循环
        res = await asyncio.to_thread(
            session.get, url, headers=_slice_business(headers) or None)
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


async def _ensure_browser(route):
    global _pw
    key = _leg_of(route)
    broken = _pw_broken.get(key)
    if broken:
        raise _TierFail("playwright 不可用: " + broken)
    browser = _pw_browsers.get(key)
    if browser is not None:
        return browser
    try:
        if _pw is None:
            _pw = await async_playwright().start()
        kwargs = dict(
            headless=config.PLAYWRIGHT_HEADLESS,
            args=["--disable-blink-features=AutomationControlled"],
        )
        # 代理在 launch 时绑定，直连与代理需分别启动浏览器实例
        if key == LEG_PROXY:
            kwargs["proxy"] = {"server": config.PROXY_HTTP}
        else:
            # 直连时禁用浏览器自身的系统代理
            kwargs["args"].append("--no-proxy-server")
        browser = await _pw.chromium.launch(**kwargs)
        _pw_browsers[key] = browser
    except Exception as e:
        _pw_broken[key] = f"{type(e).__name__}: {_short(e)}"
        log.warning("[降级] playwright 启动失败（%s），跳过该路由的该层: %s", key, _short(e))
        raise _TierFail("playwright 不可用: " + _pw_broken[key])
    return browser


async def _tier_playwright(url: str, headers, route):
    browser = await _ensure_browser(route)
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
            ctx = await browser.new_context(**kw)
            page = await ctx.new_page()
        else:
            page = await browser.new_page()
        try:
            resp = await page.goto(url, wait_until=config.PLAYWRIGHT_WAIT,
                                   timeout=config.PLAYWRIGHT_TIMEOUT_MS)
            status = resp.status if resp else 200
            html = await page.content()
            # 挑战页返回 403 是常态，给浏览器留出执行 JS 并重载的时间
            if status in config.BLOCKED_CODES or _page_challenge(html):
                waited = 0
                while waited < config.PLAYWRIGHT_CHALLENGE_WAIT:
                    await page.wait_for_timeout(1000)
                    waited += 1
                    html = await page.content()
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
            await page.close()
            if ctx is not None:
                await ctx.close()
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
    "playwright": async_playwright is not None,
}
TIERS = tuple(t for t in config.FETCH_TIERS if _READY.get(t))
TIER_INDEX = {t: i for i, t in enumerate(TIERS)}

if cffi_requests is None:
    log.warning("curl_cffi 未安装，降级链跳过该层")
if httpcloak is None:
    log.warning("httpcloak 未安装，降级链跳过该层")
if async_playwright is None:
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


async def rotate_node(group: str | None = None) -> bool:
    # 通过 Clash external-controller 切换节点；失败时静默返回
    group = group or config.CLASH_GROUP
    headers = {"Authorization": f"Bearer {config.CLASH_SECRET}"} if config.CLASH_SECRET else {}
    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            r = await client.get(f"{config.CLASH_API}/proxies", headers=headers)
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
            await client.put(f"{config.CLASH_API}/proxies/{group}", headers=headers,
                             json={"name": nxt}, timeout=3)
        log.warning("换节点: %s  %s → %s", group, cur, nxt)
        return True
    except Exception as e:
        log.info("节点切换失败（忽略）: %s", _short(e))
        return False


async def _fetch_leg(url: str, headers, min_tier, leg: str, rotate_on_block: bool,
                     rotate_group: str | None, host: str,
                     attempts: list[tuple[str, str]]) -> Fetched:
    # 单条 leg 内按降级链顺序尝试；连接类失败累计成 ProxyDown 交给上层换 leg
    start = _start_tier(url, min_tier)
    connect_fails = 0
    last_reason = ""
    leg_attempts: list[tuple[str, str]] = []

    for i in range(TIER_INDEX[start], len(TIERS)):
        tier = TIERS[i]
        nxt = TIERS[i + 1] if i + 1 < len(TIERS) else "全灭"
        try:
            res = await _TIER_FUNCS[tier](url, headers, leg)
        except _TierFail as e:
            leg_attempts.append((tier, e.reason))
            attempts.append((f"{leg}:{tier}", e.reason))
            last_reason = e.reason
            connect_fails = connect_fails + 1 if e.reason.startswith("connect") else 0
            log.warning("[降级] %s  %s/%s → %s  原因=%s", host, leg, tier, nxt, e.reason)
            # 代理不可用时后续层级同样超时，提前终止
            if leg == LEG_PROXY and connect_fails >= 2:
                raise ProxyDown(f"{host} 连续 {connect_fails} 层连接失败: {e.reason}")
            continue

        _remember_tier(host, tier)
        TIER_HITS[tier] = TIER_HITS.get(tier, 0) + 1
        return Fetched(res, tier, attempts)

    # 全部层级失败且原因为 403 时换节点，再按原顺序重试一遍
    # 只对代理 leg 换节点：直连失败与 Clash 节点无关
    if leg == LEG_PROXY and rotate_on_block and "HTTP 403" in last_reason:
        if await rotate_node(rotate_group):
            for tier in TIERS[TIER_INDEX[start]:]:
                try:
                    res = await _TIER_FUNCS[tier](url, headers, leg)
                except _TierFail as e:
                    attempts.append((f"{tier}(换节点)", e.reason))
                    continue
                _remember_tier(host, tier)
                TIER_HITS[tier] = TIER_HITS.get(tier, 0) + 1
                return Fetched(res, tier, attempts)

    # 整个 leg 全是连接类失败，说明这条出口本身不通（而非站点拦你）
    if leg == LEG_PROXY and leg_attempts and all(
            r.startswith("connect") for _, r in leg_attempts):
        raise ProxyDown(f"{host} 代理出口全部连接失败: {last_reason}")

    raise FetchFailed(f"{host} 降级链全灭（leg={leg} 起始 {start}）: {last_reason or '无可用层'}")


async def fetch(
    url: str,
    headers: dict[str, str] | None = None,
    *,
    min_tier: str | None = None,
    route: Route | str = Route.AUTO,
    rotate_on_block: bool = False,
    rotate_group: str | None = None,
) -> Fetched:
    global _PROXY_DOWN
    route = Route(route)
    host = host_of(url)
    legs = _legs_for(route)
    if not legs:
        raise ProxyDown(f"{host} 代理已判定不可用，FORCE 站点不回落直连")

    attempts: list[tuple[str, str]] = []
    for leg in legs:
        try:
            return await _fetch_leg(url, headers, min_tier, leg, rotate_on_block,
                                    rotate_group, host, attempts)
        except ProxyDown as e:
            _PROXY_DOWN = True
            if leg == legs[-1]:
                raise
            log.warning("[路由] %s 代理连不上，改走直连: %s", host, e)

    raise FetchFailed(f"{host} 无可用路由")


async def close_all() -> None:
    global _pw, _PROXY_DOWN
    for client in _clients.values():
        try:
            await client.aclose()
        except Exception:
            pass
    _clients.clear()
    for session in _cffi_sessions.values():
        try:
            res = session.close()
            if inspect.isawaitable(res):
                await res
        except Exception:
            pass
    _cffi_sessions.clear()
    for session in _cloak_sessions.values():
        try:
            # httpcloak 的 close 是同步的（只释放本地资源）
            session.close()
        except Exception:
            pass
    _cloak_sessions.clear()
    for browser in _pw_browsers.values():
        try:
            await browser.close()
        except Exception:
            pass
    _pw_browsers.clear()
    _pw_broken.clear()   # 允许下次重新尝试启动
    _PROXY_DOWN = False  # 下一轮重新判定
    if _pw is not None:
        try:
            await _pw.stop()
        finally:
            _pw = None
