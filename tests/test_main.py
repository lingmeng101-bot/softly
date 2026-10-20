import config
import main as main_mod


class _FakeConn:
    def close(self):
        pass


def _setup(monkeypatch, alive, direct_ok):
    calls = []
    monkeypatch.setattr(main_mod, "setup_logger", lambda: None)
    monkeypatch.setattr(main_mod, "log_tiers", lambda: None)
    monkeypatch.setattr(main_mod, "proxy_alive", lambda: alive)
    monkeypatch.setattr(main_mod, "network_alive", lambda url: direct_ok)
    monkeypatch.setattr(main_mod, "init_db", lambda: _FakeConn())
    monkeypatch.setattr(main_mod, "close_all", lambda: None)

    def fake_crawl(target, conn, route=None):
        calls.append((target.url, route))
        return main_mod.CrawlStats()

    monkeypatch.setattr(main_mod, "crawl_and_save", fake_crawl)
    return calls


def test_force_does_not_fall_back_to_direct(monkeypatch):
    calls = _setup(monkeypatch, alive=False, direct_ok=True)
    monkeypatch.setattr(config, "TARGETS", {
        "force": config.Target("https://force.example.com/", route=config.Route.FORCE),
        "auto": config.Target("https://auto.example.com/", route=config.Route.AUTO),
        "direct": config.Target("https://direct.example.com/", route=config.Route.DIRECT),
    })
    main_mod.main()
    # FORCE 跳过；AUTO 回落直连；DIRECT 保持
    assert calls == [
        ("https://auto.example.com/", config.Route.DIRECT),
        ("https://direct.example.com/", config.Route.DIRECT),
    ]


def test_routes_kept_when_proxy_alive(monkeypatch):
    calls = _setup(monkeypatch, alive=True, direct_ok=False)
    monkeypatch.setattr(config, "TARGETS", {
        "force": config.Target("https://force.example.com/", route=config.Route.FORCE),
        "auto": config.Target("https://auto.example.com/", route=config.Route.AUTO),
    })
    main_mod.main()
    assert calls == [
        ("https://force.example.com/", config.Route.FORCE),
        ("https://auto.example.com/", config.Route.AUTO),
    ]


def test_foreign_skipped_when_no_proxy_and_no_direct(monkeypatch):
    calls = _setup(monkeypatch, alive=False, direct_ok=False)
    monkeypatch.setattr(config, "TARGETS", {
        "auto": config.Target("https://auto.example.com/", route=config.Route.AUTO),
        "direct": config.Target("https://direct.example.com/", route=config.Route.DIRECT),
    })
    main_mod.main()
    assert calls == [("https://direct.example.com/", config.Route.DIRECT)]
