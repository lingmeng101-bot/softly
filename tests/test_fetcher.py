import asyncio

import pytest

import config
from crawler import fetcher
from crawler.fetcher import (
    _TIER_HINT,
    TIERS,
    BrowserResponse,
    _challenge_hit,
    _is_challenge,
    _remember_tier,
    _slice_business,
    _start_tier,
    looks_like_shell,
)


def run(coro):
    return asyncio.run(coro)


def async_call(fn):
    async def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)

    return wrapper


def fetch(*args, **kwargs):
    return run(fetcher.fetch(*args, **kwargs))


def rotate_node(*args, **kwargs):
    return run(fetcher.rotate_node(*args, **kwargs))


def close_all():
    return run(fetcher.close_all())


def ensure_browser(route):
    return run(fetcher._ensure_browser(route))


def tier_playwright(*args, **kwargs):
    return run(fetcher._tier_playwright(*args, **kwargs))


def tier_httpx(*args, **kwargs):
    return run(fetcher._tier_httpx(*args, **kwargs))


@pytest.fixture(autouse=True)
def _reset_manual_session(monkeypatch):
    # 测试不依赖本机 .env 中的人工验证会话，也不继承代理熔断状态
    monkeypatch.setattr(config, "HTTP_USER_AGENT", "")
    monkeypatch.setattr(config, "HTTP_COOKIE", "")
    monkeypatch.setattr(config, "PLAYWRIGHT_STORAGE_STATE", "")


class FakeRes:
    def __init__(self, status=200, ct="text/html; charset=utf-8", text="", headers=None):
        self.status_code = status
        self.headers = headers if headers is not None else {"content-type": ct}
        self.text = text
        self.url = "http://a.com/"
        self.content = text.encode("utf-8")


def test_tiers_subset_of_config():
    assert isinstance(TIERS, tuple) and TIERS
    assert all(t in config.FETCH_TIERS for t in TIERS)


def test_strong_marker_challenge():
    res = FakeRes(text="<html><title>Just a moment...</title>" + "x" * 50)
    assert _is_challenge(res) is True
    assert "just a moment" in (_challenge_hit(res) or "")


def test_attention_required_challenge():
    assert _challenge_hit(FakeRes(text="<html>Attention Required! | Cloudflare" + "x" * 50)) == "attention required"


def test_normal_html_not_challenge():
    res = FakeRes(text="<html><body><h1>正常列表</h1>" + "a" * 200 + "</body></html>")
    assert _is_challenge(res) is False


def test_json_never_challenge():
    res = FakeRes(ct="application/json", text='{"msg": "验证码"}')
    assert _is_challenge(res) is False


def test_chinese_markers_need_two():
    one = FakeRes(text="<html>" + "请稍候" + "x" * 100)
    assert _is_challenge(one) is False
    two = FakeRes(text="<html>请稍候 安全验证" + "x" * 100)
    assert _is_challenge(two) is True


def test_looks_like_shell():
    shell = FakeRes(text="<html><body><div id=app></div></body></html>")
    assert looks_like_shell(shell) is True
    full = FakeRes(text="<html><body>" + "<a href='/x'>t</a>" * 6 + "y" * 4000 + "</body></html>")
    assert looks_like_shell(full) is False
    assert looks_like_shell(FakeRes(ct="application/json", text="{}")) is False


def test_slice_business_only_business_headers():
    headers = {"User-Agent": "x", "Referer": "r", "sec-ch-ua": "y", "X-Requested-With": "z"}
    assert _slice_business(headers) == {"Referer": "r", "X-Requested-With": "z"}
    assert _slice_business(None) == {}


def test_sticky_hint_only_goes_up():
    host = "hint.example.com"
    _TIER_HINT.pop(host, None)
    _remember_tier(host, TIERS[-1])
    _remember_tier(host, TIERS[0])  # 粘滞策略不回退
    assert _start_tier("https://" + host + "/x", None) == TIERS[-1]


def test_min_tier_can_raise_start():
    host = "mintier.example.com"
    _TIER_HINT.pop(host, None)
    if len(TIERS) >= 2:
        assert _start_tier("https://" + host + "/x", TIERS[1]) == TIERS[1]


def test_browser_response_interface():
    res = BrowserResponse('{"a": 1}', 200, "http://a.com/", {"content-type": "application/json"})
    assert res.status_code == 200
    assert str(res.url) == "http://a.com/"
    assert res.text == '{"a": 1}'
    assert res.content == b'{"a": 1}'
    assert res.headers.get("content-type") == "application/json"
    assert res.json() == {"a": 1}
    res.raise_for_status()
    bad = BrowserResponse("", 404, "http://a.com/", {})
    try:
        bad.raise_for_status()
    except fetcher.FetchFailed:
        pass
    else:
        raise AssertionError("404 应该抛 FetchFailed")


class _Resp:
    def __init__(self, data=None, status=200):
        self._data = data or {}
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http error")

    def json(self):
        return self._data


def test_rotate_node_skips_current_group_and_direct(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            calls.append(("get", url))
            return _Resp(
                {
                    "proxies": {
                        "PROXY": {"type": "Selector", "all": ["A", "DIRECT", "Sub", "B"], "now": "A"},
                        "A": {"type": "Vless"},
                        "B": {"type": "Vless"},
                        "DIRECT": {"type": "Direct"},
                        "Sub": {"type": "Selector"},
                    }
                }
            )

        async def put(self, url, **kwargs):
            calls.append(("put", kwargs.get("json")))
            return _Resp()

    monkeypatch.setattr(fetcher.httpx, "AsyncClient", FakeClient)
    assert rotate_node("PROXY") is True
    # 跳过当前节点、DIRECT 与策略组，选择 B
    assert calls[1] == ("put", {"name": "B"})


def test_rotate_node_no_candidates(monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            return _Resp(
                {
                    "proxies": {
                        "PROXY": {"type": "Selector", "all": ["A", "DIRECT"], "now": "A"},
                        "A": {"type": "Vless"},
                        "DIRECT": {"type": "Direct"},
                    }
                }
            )

        async def put(self, url, **kwargs):
            raise AssertionError("没有可换节点时不该 PUT")

    monkeypatch.setattr(fetcher.httpx, "AsyncClient", FakeClient)
    assert rotate_node("PROXY") is False


def test_rotate_node_failure_is_silent(monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            raise RuntimeError("clash 没开")

    monkeypatch.setattr(fetcher.httpx, "AsyncClient", FakeClient)
    assert rotate_node("PROXY") is False


def test_fetch_rotate_uses_given_group(monkeypatch):
    seen = []

    def fail(url, headers, route):
        raise fetcher._TierFail("HTTP 403")

    for tier in fetcher.TIERS:
        monkeypatch.setitem(fetcher._TIER_FUNCS, tier, async_call(fail))
    monkeypatch.setattr(fetcher, "rotate_node", async_call(lambda group=None: seen.append(group) or False))

    with pytest.raises(fetcher.FetchFailed):
        fetch("https://rotate.example.com/", route=config.Route.FORCE, rotate_on_block=True, rotate_group="测试策略组")
    assert seen == ["测试策略组"]


def test_fetch_all_tiers_fail(monkeypatch):
    def fail(url, headers, route):
        raise fetcher._TierFail("HTTP 403")

    for tier in fetcher.TIERS:
        monkeypatch.setitem(fetcher._TIER_FUNCS, tier, async_call(fail))
    with pytest.raises(fetcher.FetchFailed):
        fetch("https://down.example.com/")


def test_force_raises_proxy_down_when_proxy_unreachable(monkeypatch):
    def fail(url, headers, route):
        raise fetcher._TierFail("connect: refused")

    for tier in fetcher.TIERS:
        monkeypatch.setitem(fetcher._TIER_FUNCS, tier, async_call(fail))
    # FORCE 只有代理一条 leg，连不上直接 ProxyDown，不回落直连
    with pytest.raises(fetcher.ProxyDown):
        fetch("https://force.example.com/", route=config.Route.FORCE)


def test_auto_falls_back_to_direct_leg(monkeypatch):
    seen = []

    def fake(url, headers, route):
        leg = fetcher._leg_of(route)
        seen.append(leg)
        if leg == fetcher.LEG_PROXY:
            raise fetcher._TierFail("connect: refused")
        return FakeRes(text="<html>ok</html>")

    for tier in fetcher.TIERS:
        monkeypatch.setitem(fetcher._TIER_FUNCS, tier, async_call(fake))

    fetched = fetch("https://auto.example.com/", route=config.Route.AUTO)
    assert fetched.tier in TIERS
    assert fetcher.LEG_PROXY in seen and fetcher.LEG_DIRECT in seen
    assert seen.index(fetcher.LEG_PROXY) < seen.index(fetcher.LEG_DIRECT)


def test_auto_does_not_fall_back_on_block(monkeypatch):
    seen = []

    def fake(url, headers, route):
        seen.append(fetcher._leg_of(route))
        raise fetcher._TierFail("HTTP 403")

    for tier in fetcher.TIERS:
        monkeypatch.setitem(fetcher._TIER_FUNCS, tier, async_call(fake))

    # 403 说明代理本身是通的，回落直连没有意义
    with pytest.raises(fetcher.FetchFailed):
        fetch("https://blocked.example.com/", route=config.Route.AUTO)
    assert seen == [fetcher.LEG_PROXY] * len(TIERS)


def test_direct_route_never_touches_proxy(monkeypatch):
    seen = []

    def fake(url, headers, route):
        seen.append(fetcher._leg_of(route))
        raise fetcher._TierFail("connect: refused")

    for tier in fetcher.TIERS:
        monkeypatch.setitem(fetcher._TIER_FUNCS, tier, async_call(fake))

    with pytest.raises(fetcher.FetchFailed):
        fetch("https://direct.example.com/", route=config.Route.DIRECT)
    assert set(seen) == {fetcher.LEG_DIRECT}


def test_proxy_down_short_circuits_following_sites(monkeypatch):
    seen = []

    def fake(url, headers, route):
        leg = fetcher._leg_of(route)
        seen.append(leg)
        raise fetcher._TierFail("connect: refused")

    for tier in fetcher.TIERS:
        monkeypatch.setitem(fetcher._TIER_FUNCS, tier, async_call(fake))

    async def scenario():
        with pytest.raises(fetcher.FetchFailed):
            await fetcher.fetch("https://first.example.com/", route=config.Route.AUTO)
        assert fetcher._runtime().proxy_down is True
        seen.clear()
        with pytest.raises(fetcher.FetchFailed):
            await fetcher.fetch("https://second.example.com/", route=config.Route.AUTO)

    run(scenario())
    # 第一站已确认代理不通，第二站不再等代理连接超时
    assert fetcher.LEG_PROXY not in seen


def test_force_skipped_immediately_when_proxy_known_down():
    async def scenario():
        fetcher._runtime().proxy_down = True
        return await fetcher.fetch("https://force.example.com/", route=config.Route.FORCE)

    with pytest.raises(fetcher.ProxyDown):
        run(scenario())


def test_http_clients_ignore_env_proxy(monkeypatch):
    captured = []

    class FakeClient:
        def __init__(self, **kwargs):
            captured.append(kwargs)

        async def aclose(self):
            pass

    monkeypatch.setattr(fetcher.httpx, "AsyncClient", FakeClient)

    async def scenario():
        fetcher._client_for(config.Route.DIRECT)
        fetcher._client_for(config.Route.AUTO)
        await fetcher.close_all()

    run(scenario())
    assert captured[0]["trust_env"] is False
    assert captured[0]["proxy"] is None
    assert captured[1]["trust_env"] is False
    assert captured[1]["proxy"] == config.PROXY_HTTP


def test_browser_failure_isolated_per_route(monkeypatch):
    import types

    async def launch(**kwargs):
        if "proxy" in kwargs:
            raise RuntimeError("proxy browser failed")
        return object()

    class FakePW:
        chromium = types.SimpleNamespace(launch=launch)

        def stop(self):
            pass

    class Starter:
        async def start(self):
            return FakePW()

    monkeypatch.setattr(fetcher, "async_playwright", lambda: Starter())

    async def scenario():
        with pytest.raises(fetcher._TierFail):
            await fetcher._ensure_browser(config.Route.AUTO)
        assert "proxy" in fetcher._runtime().pw_broken
        # 代理浏览器失败不应影响直连浏览器
        return await fetcher._ensure_browser(config.Route.DIRECT)

    assert run(scenario()) is not None


def test_connect_timeout_counts_as_connect(monkeypatch):
    def boom(url, headers, route):
        raise fetcher.httpx.ConnectTimeout("connect timeout")

    monkeypatch.setattr(fetcher, "_http_get", boom)
    with pytest.raises(fetcher._TierFail) as exc:
        tier_httpx("https://x.example/", None, fetcher.LEG_DIRECT)
    # 连不上代理：应计入 connect，才能触发 ProxyDown / 熔断
    assert exc.value.reason.startswith("connect:")


def test_read_timeout_not_counted_as_connect(monkeypatch):
    def boom(url, headers, route):
        raise fetcher.httpx.ReadTimeout("read timeout")

    monkeypatch.setattr(fetcher, "_http_get", boom)
    with pytest.raises(fetcher._TierFail) as exc:
        tier_httpx("https://x.example/", None, fetcher.LEG_DIRECT)
    # 连接已建立只是站慢：不能算出口不通，否则会误触发全局熔断
    assert exc.value.reason.startswith("timeout:")


def test_direct_leg_does_not_rotate_node(monkeypatch):
    rotated = []

    def fail(url, headers, leg):
        raise fetcher._TierFail("HTTP 403")

    for tier in fetcher.TIERS:
        monkeypatch.setitem(fetcher._TIER_FUNCS, tier, async_call(fail))
    monkeypatch.setattr(fetcher, "rotate_node", async_call(lambda group=None: rotated.append(group) or True))

    with pytest.raises(fetcher.FetchFailed):
        fetch("https://direct403.example.com/", route=config.Route.DIRECT, rotate_on_block=True)
    # 直连失败与 Clash 节点无关，不该动用户的节点
    assert rotated == []


def test_close_all_resets_broken_state():
    async def scenario():
        rt = fetcher._runtime()
        rt.pw_broken = {"proxy": "boom"}
        rt.pw_browsers = {}
        rt.pw = None
        await fetcher.close_all()
        return rt.pw_broken

    assert run(scenario()) == {}


def test_close_all_resets_proxy_down():
    async def scenario():
        rt = fetcher._runtime()
        rt.proxy_down = True
        await fetcher.close_all()
        return rt.proxy_down

    assert run(scenario()) is False


def test_browser_cached_per_route(monkeypatch):

    launches = []

    class FakeBrowser:
        def close(self):
            pass

    class FakeChromium:
        async def launch(self, **kwargs):
            launches.append(kwargs)
            return FakeBrowser()

    class FakePW:
        chromium = FakeChromium()

        def stop(self):
            pass

    class Starter:
        async def start(self):
            return FakePW()

    monkeypatch.setattr(fetcher, "async_playwright", lambda: Starter())

    async def scenario():
        direct = await fetcher._ensure_browser(config.Route.DIRECT)
        proxy = await fetcher._ensure_browser(config.Route.AUTO)
        again = await fetcher._ensure_browser(config.Route.DIRECT)
        return direct, proxy, again

    direct, proxy, again = run(scenario())
    assert direct is again and direct is not proxy
    assert len(launches) == 2
    assert "proxy" not in launches[0]
    assert "--no-proxy-server" in launches[0]["args"]  # 直连禁用浏览器系统代理
    assert launches[1]["proxy"] == {"server": config.PROXY_HTTP}
    assert "--no-proxy-server" not in launches[1]["args"]


def test_cffi_session_ignores_env_proxy(monkeypatch):
    import types

    captured = []

    class FakeSession:
        def __init__(self, **kwargs):
            captured.append(kwargs)

        def close(self):
            pass

    monkeypatch.setattr(fetcher, "cffi_requests", types.SimpleNamespace(AsyncSession=FakeSession))

    async def scenario():
        fetcher._cffi_session(config.Route.DIRECT)
        fetcher._cffi_session(config.Route.AUTO)
        await fetcher.close_all()

    run(scenario())

    assert captured[0]["trust_env"] is False  # 直连不读 HTTP(S)_PROXY
    assert captured[0]["proxies"] == {"http": "", "https": ""}  # 显式置空，覆盖 libcurl 的环境代理
    assert captured[1]["trust_env"] is False
    assert captured[1]["proxies"] == {"http": config.PROXY_HTTP, "https": config.PROXY_HTTP}


def test_looks_like_shell_link_variants():
    # <a 后接换行或直接 > 的写法也要能识别为链接
    newline_links = "<html><body>" + "<a\nhref='/x'>t</a>" * 6 + "y" * 4000 + "</body></html>"
    bare_links = "<html><body>" + "<a>t</a>" * 6 + "y" * 4000 + "</body></html>"
    assert looks_like_shell(FakeRes(text=newline_links)) is False
    assert looks_like_shell(FakeRes(text=bare_links)) is False


class _FakePage:
    def __init__(self, contents, status=403):
        self._contents = list(contents)
        self._idx = 0
        self.url = "https://cf.example.com/"
        self.status = status
        self.waits = 0

    async def goto(self, url, **kwargs):
        import types

        return types.SimpleNamespace(status=self.status, headers={"content-type": "text/html; charset=utf-8"})

    async def content(self):
        value = self._contents[min(self._idx, len(self._contents) - 1)]
        self._idx += 1
        return value

    async def wait_for_timeout(self, ms):
        self.waits += 1

    async def close(self):
        pass


class _FakeBrowser:
    def __init__(self, page):
        self.page = page

    async def new_page(self):
        return self.page


def _challenge_html():
    return "<!doctype html><html><title>Just a moment...</title></html>"


def _normal_html():
    return "<!doctype html><html><title>Dashboard</title><body>ok</body></html>"


def test_playwright_waits_for_challenge_to_clear(monkeypatch):
    page = _FakePage([_challenge_html(), _challenge_html(), _normal_html()])
    monkeypatch.setattr(fetcher, "_ensure_browser", async_call(lambda route: _FakeBrowser(page)))
    monkeypatch.setattr(config, "PLAYWRIGHT_CHALLENGE_WAIT", 5)
    res = tier_playwright("https://cf.example.com/", None, config.Route.AUTO)
    assert res.status_code == 200
    assert "Dashboard" in res.text
    assert page.waits == 2


def test_playwright_gives_up_when_challenge_stuck(monkeypatch):
    page = _FakePage([_challenge_html()] * 10)
    monkeypatch.setattr(fetcher, "_ensure_browser", async_call(lambda route: _FakeBrowser(page)))
    monkeypatch.setattr(config, "PLAYWRIGHT_CHALLENGE_WAIT", 3)
    with pytest.raises(fetcher._TierFail):
        tier_playwright("https://cf.example.com/", None, config.Route.AUTO)
    assert page.waits == 3


def test_playwright_no_wait_on_normal_page(monkeypatch):
    page = _FakePage([_normal_html()], status=200)
    monkeypatch.setattr(fetcher, "_ensure_browser", async_call(lambda route: _FakeBrowser(page)))
    res = tier_playwright("https://cf.example.com/", None, config.Route.AUTO)
    assert res.status_code == 200
    assert page.waits == 0


def test_build_header_uses_manual_session(monkeypatch):
    monkeypatch.setattr(config, "HTTP_USER_AGENT", "MyTestUA/1.0")
    monkeypatch.setattr(config, "HTTP_COOKIE", "cf_clearance=abc; other=1")
    headers = fetcher.build_header()
    assert headers["User-Agent"] == "MyTestUA/1.0"
    assert headers["Cookie"] == "cf_clearance=abc; other=1"
    assert fetcher.build_header({"Referer": "https://x/"})["Referer"] == "https://x/"


def test_playwright_uses_storage_state(monkeypatch, tmp_path):
    state = tmp_path / "session.json"
    state.write_text("{}", encoding="utf-8")
    page = _FakePage([_normal_html()], status=200)
    closed = {"ctx": False}

    class _Ctx:
        async def new_page(self):
            return page

        async def close(self):
            closed["ctx"] = True

    class _Browser:
        def __init__(self):
            self.kwargs = None

        async def new_context(self, **kwargs):
            self.kwargs = kwargs
            return _Ctx()

        async def new_page(self):
            raise AssertionError("配置了 storage_state 时应走 new_context")

    browser = _Browser()
    monkeypatch.setattr(fetcher, "_ensure_browser", async_call(lambda route: browser))
    monkeypatch.setattr(config, "PLAYWRIGHT_STORAGE_STATE", str(state))
    res = tier_playwright("https://cf.example.com/", None, config.Route.AUTO)
    assert res.status_code == 200
    assert browser.kwargs == {"storage_state": str(state)}
    assert closed["ctx"] is True


def test_playwright_context_uses_configured_ua(monkeypatch):
    page = _FakePage([_normal_html()], status=200)
    closed = {"ctx": False}

    class _Ctx:
        async def new_page(self):
            return page

        async def close(self):
            closed["ctx"] = True

    class _Browser:
        def __init__(self):
            self.kwargs = None

        async def new_context(self, **kwargs):
            self.kwargs = kwargs
            return _Ctx()

        async def new_page(self):
            raise AssertionError("配置 UA 时应走 new_context")

    browser = _Browser()
    monkeypatch.setattr(fetcher, "_ensure_browser", async_call(lambda route: browser))
    monkeypatch.setattr(config, "HTTP_USER_AGENT", "MyUA/1.0")
    monkeypatch.setattr(config, "PLAYWRIGHT_STORAGE_STATE", "")
    res = tier_playwright("https://cf.example.com/", None, config.Route.AUTO)
    assert res.status_code == 200
    assert browser.kwargs == {"user_agent": "MyUA/1.0"}
    assert closed["ctx"] is True


def test_httpcloak_disables_conditional_cache(monkeypatch):
    captured = []

    class FakeSession:
        def __init__(self, **kwargs):
            captured.append(kwargs)

        def close(self):
            pass

    monkeypatch.setattr(fetcher, "httpcloak", type("FakeHTTPCloak", (), {"Session": FakeSession}))

    async def scenario():
        fetcher._cloak_session(config.Route.DIRECT)

    run(scenario())
    assert captured and captured[0]["without_conditional_cache"] is True


def test_runtime_state_isolated_between_event_loops(monkeypatch):
    def fake(url, headers, route):
        raise fetcher._TierFail("connect: refused")

    for tier in fetcher.TIERS:
        monkeypatch.setitem(fetcher._TIER_FUNCS, tier, async_call(fake))

    async def first():
        with pytest.raises(fetcher.FetchFailed):
            await fetcher.fetch("https://a.example/", route=config.Route.AUTO)
        return fetcher._runtime().proxy_down

    assert run(first()) is True

    async def second():
        return fetcher._runtime().proxy_down

    # 新的 asyncio.run 是新的 loop：熔断状态不能带过去
    assert run(second()) is False


def test_browser_launch_is_locked_under_concurrency(monkeypatch):

    launches = []

    class FakeBrowser:
        async def close(self):
            pass

    class FakeChromium:
        async def launch(self, **kwargs):
            launches.append(kwargs)
            await asyncio.sleep(0.01)  # 放大竞态窗口
            return FakeBrowser()

    class FakePW:
        chromium = FakeChromium()

        def stop(self):
            pass

    class Starter:
        async def start(self):
            return FakePW()

    monkeypatch.setattr(fetcher, "async_playwright", lambda: Starter())

    async def scenario():
        return await asyncio.gather(
            fetcher._ensure_browser(config.Route.DIRECT),
            fetcher._ensure_browser(config.Route.DIRECT),
            fetcher._ensure_browser(config.Route.DIRECT),
        )

    browsers = run(scenario())
    assert browsers[0] is browsers[1] is browsers[2]
    assert len(launches) == 1


def test_httpcloak_calls_are_serialized(monkeypatch):
    import time
    import types as _types

    active = 0
    peak = 0

    class FakeSession:
        def __init__(self, **kwargs):
            pass

        def get(self, url, headers=None):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            time.sleep(0.02)
            active -= 1
            return FakeRes(text="<html><body>" + "<a href='/x'>t</a>" * 6 + "y" * 4000 + "</body></html>")

        def close(self):
            pass

    monkeypatch.setattr(fetcher, "httpcloak", _types.SimpleNamespace(Session=FakeSession))

    async def scenario():
        await asyncio.gather(*(fetcher._tier_httpcloak("https://x.example/", None, config.Route.DIRECT) for _ in range(3)))

    run(scenario())
    assert peak == 1  # httpcloak 的线程调用必须串行
