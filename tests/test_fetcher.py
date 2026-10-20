import pytest

import config
from crawler import fetcher
from crawler.fetcher import (
    BrowserResponse, TIERS, TIER_INDEX, _TIER_HINT, _challenge_hit, _is_challenge,
    _remember_tier, _slice_business, _start_tier, looks_like_shell,
)


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
    _remember_tier(host, TIERS[0])          # 粘滞策略不回退
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

    def fake_get(url, **kwargs):
        calls.append(("get", url))
        return _Resp({"proxies": {
            "PROXY": {"type": "Selector", "all": ["A", "DIRECT", "Sub", "B"], "now": "A"},
            "A": {"type": "Vless"},
            "B": {"type": "Vless"},
            "DIRECT": {"type": "Direct"},
            "Sub": {"type": "Selector"},
        }})

    def fake_put(url, headers=None, json=None, timeout=None, **kwargs):
        calls.append(("put", json))
        return _Resp()

    monkeypatch.setattr(fetcher.httpx, "get", fake_get)
    monkeypatch.setattr(fetcher.httpx, "put", fake_put)
    assert fetcher.rotate_node("PROXY") is True
    # 跳过当前节点、DIRECT 与策略组，选择 B
    assert calls[1] == ("put", {"name": "B"})


def test_rotate_node_no_candidates(monkeypatch):
    def fake_get(url, **kwargs):
        return _Resp({"proxies": {
            "PROXY": {"type": "Selector", "all": ["A", "DIRECT"], "now": "A"},
            "A": {"type": "Vless"},
            "DIRECT": {"type": "Direct"},
        }})

    def fake_put(url, **kwargs):
        raise AssertionError("没有可换节点时不该 PUT")

    monkeypatch.setattr(fetcher.httpx, "get", fake_get)
    monkeypatch.setattr(fetcher.httpx, "put", fake_put)
    assert fetcher.rotate_node("PROXY") is False


def test_rotate_node_failure_is_silent(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("clash 没开")

    monkeypatch.setattr(fetcher.httpx, "get", boom)
    assert fetcher.rotate_node("PROXY") is False


def test_fetch_rotate_uses_given_group(monkeypatch):
    seen = []

    def fail(url, headers, route):
        raise fetcher._TierFail("HTTP 403")

    for tier in fetcher.TIERS:
        monkeypatch.setitem(fetcher._TIER_FUNCS, tier, fail)
    monkeypatch.setattr(fetcher, "rotate_node", lambda group=None: seen.append(group) or False)

    with pytest.raises(fetcher.FetchFailed):
        fetcher.fetch("https://rotate.example.com/", rotate_on_block=True, rotate_group="测试策略组")
    assert seen == ["测试策略组"]


def test_fetch_all_tiers_fail(monkeypatch):
    def fail(url, headers, route):
        raise fetcher._TierFail("HTTP 403")

    for tier in fetcher.TIERS:
        monkeypatch.setitem(fetcher._TIER_FUNCS, tier, fail)
    with pytest.raises(fetcher.FetchFailed):
        fetcher.fetch("https://down.example.com/")


def test_consecutive_connect_fails_raise_proxy_down(monkeypatch):
    def fail(url, headers, route):
        raise fetcher._TierFail("connect: refused")

    for tier in fetcher.TIERS:
        monkeypatch.setitem(fetcher._TIER_FUNCS, tier, fail)
    with pytest.raises(fetcher.ProxyDown):
        fetcher.fetch("https://down2.example.com/", route=config.Route.AUTO)

def test_network_alive(monkeypatch):
    seen = {}

    class _R:
        def raise_for_status(self):
            pass

    def fake_get(url, **kwargs):
        seen.update(kwargs)
        return _R()

    monkeypatch.setattr(fetcher.httpx, "get", fake_get)
    assert fetcher.network_alive() is True
    assert seen.get("trust_env") is False      # 不读环境代理，才是真直连探测

    def boom(*args, **kwargs):
        raise RuntimeError("no net")

    monkeypatch.setattr(fetcher.httpx, "get", boom)
    assert fetcher.network_alive() is False


def test_proxy_alive_ignores_env_proxy(monkeypatch):
    seen = {}

    class _R:
        def raise_for_status(self):
            pass

    def fake_get(url, **kwargs):
        seen.update(kwargs)
        return _R()

    monkeypatch.setattr(fetcher.httpx, "get", fake_get)
    assert fetcher.proxy_alive() is True
    assert seen.get("trust_env") is False
    assert seen.get("proxy") == config.PROXY_HTTP


def test_http_clients_ignore_env_proxy(monkeypatch):
    captured = []

    class FakeClient:
        def __init__(self, **kwargs):
            captured.append(kwargs)

        def close(self):
            pass

    monkeypatch.setattr(fetcher.httpx, "Client", FakeClient)
    monkeypatch.setattr(fetcher, "_clients", {})
    fetcher._client_for(config.Route.DIRECT)
    fetcher._client_for(config.Route.AUTO)
    fetcher.close_all()
    assert captured[0]["trust_env"] is False
    assert "proxy" not in captured[0]
    assert captured[1]["trust_env"] is False
    assert captured[1]["proxy"] == config.PROXY_HTTP


def test_browser_failure_isolated_per_route(monkeypatch):
    import types

    def launch(**kwargs):
        if "proxy" in kwargs:
            raise RuntimeError("proxy browser failed")
        return object()

    class FakePW:
        chromium = types.SimpleNamespace(launch=launch)

        def stop(self):
            pass

    monkeypatch.setattr(fetcher, "sync_playwright", lambda: types.SimpleNamespace(start=lambda: FakePW()))
    monkeypatch.setattr(fetcher, "_pw", None)
    monkeypatch.setattr(fetcher, "_pw_browsers", {})
    monkeypatch.setattr(fetcher, "_pw_broken", {})

    with pytest.raises(fetcher._TierFail):
        fetcher._ensure_browser(config.Route.AUTO)
    assert "proxy" in fetcher._pw_broken
    # 代理浏览器失败不应影响直连浏览器
    assert fetcher._ensure_browser(config.Route.DIRECT) is not None


def test_close_all_resets_broken_state(monkeypatch):
    monkeypatch.setattr(fetcher, "_pw_broken", {"proxy": "boom"})
    monkeypatch.setattr(fetcher, "_pw_browsers", {})
    monkeypatch.setattr(fetcher, "_pw", None)
    fetcher.close_all()
    assert fetcher._pw_broken == {}


def test_browser_cached_per_route(monkeypatch):
    import types

    launches = []

    class FakeBrowser:
        def close(self):
            pass

    class FakeChromium:
        def launch(self, **kwargs):
            launches.append(kwargs)
            return FakeBrowser()

    class FakePW:
        chromium = FakeChromium()

        def stop(self):
            pass

    monkeypatch.setattr(fetcher, "sync_playwright", lambda: types.SimpleNamespace(start=lambda: FakePW()))
    monkeypatch.setattr(fetcher, "_pw", None)
    monkeypatch.setattr(fetcher, "_pw_browsers", {})
    monkeypatch.setattr(fetcher, "_pw_broken", {})

    direct = fetcher._ensure_browser(config.Route.DIRECT)
    proxy = fetcher._ensure_browser(config.Route.AUTO)
    again = fetcher._ensure_browser(config.Route.DIRECT)

    assert direct is again and direct is not proxy
    assert len(launches) == 2
    assert "proxy" not in launches[0]
    assert "--no-proxy-server" in launches[0]["args"]      # DIRECT 禁用浏览器系统代理
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

    monkeypatch.setattr(fetcher, "cffi_requests", types.SimpleNamespace(Session=FakeSession))
    monkeypatch.setattr(fetcher, "_cffi_sessions", {})
    fetcher._cffi_session(config.Route.DIRECT)
    fetcher._cffi_session(config.Route.AUTO)
    fetcher.close_all()

    assert captured[0]["trust_env"] is False               # DIRECT 不读 HTTP(S)_PROXY
    assert captured[0]["proxies"] == {"http": "", "https": ""}   # 显式置空，覆盖 libcurl 的环境代理
    assert captured[1]["trust_env"] is False
    assert captured[1]["proxies"] == {"http": config.PROXY_HTTP, "https": config.PROXY_HTTP}
