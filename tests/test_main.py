import config
import main as main_mod


class _FakeConn:
    def close(self):
        pass


def _setup(monkeypatch):
    calls = []
    monkeypatch.setattr(main_mod, "setup_logger", lambda: None)
    monkeypatch.setattr(main_mod, "log_tiers", lambda: None)
    monkeypatch.setattr(main_mod, "init_db", lambda: _FakeConn())
    monkeypatch.setattr(main_mod, "close_all", lambda: None)

    def fake_crawl(target, conn):
        calls.append(target.url)
        return main_mod.CrawlStats(added=1)

    monkeypatch.setattr(main_mod, "crawl_and_save", fake_crawl)
    return calls


def test_main_passes_target_through_without_route_override(monkeypatch):
    calls = _setup(monkeypatch)
    monkeypatch.setattr(config, "TARGETS", {
        "force": config.Target("https://force.example.com/", route=config.Route.FORCE),
        "auto": config.Target("https://auto.example.com/", route=config.Route.AUTO),
        "direct": config.Target("https://direct.example.com/", route=config.Route.DIRECT),
    })
    main_mod.main()
    # main 不预判网络环境，路由完全交给 target
    assert calls == [
        "https://force.example.com/",
        "https://auto.example.com/",
        "https://direct.example.com/",
    ]


def test_inactive_target_skipped(monkeypatch):
    calls = _setup(monkeypatch)
    monkeypatch.setattr(config, "TARGETS", {
        "off": config.Target("https://off.example.com/", enable=False),
        "empty": config.Target(""),
        "on": config.Target("https://on.example.com/"),
    })
    main_mod.main()
    assert calls == ["https://on.example.com/"]


def test_stats_accumulated_across_targets(monkeypatch):
    monkeypatch.setattr(main_mod, "setup_logger", lambda: None)
    monkeypatch.setattr(main_mod, "log_tiers", lambda: None)
    monkeypatch.setattr(main_mod, "init_db", lambda: _FakeConn())
    monkeypatch.setattr(main_mod, "close_all", lambda: None)

    seen = []
    monkeypatch.setattr(main_mod, "crawl_and_save",
                        lambda target, conn: main_mod.CrawlStats(added=2, skipped=1))

    real_info = main_mod.log.info
    main_mod.log.info = lambda fmt, *a: seen.append((fmt, a))
    try:
        monkeypatch.setattr(config, "TARGETS", {
            "a": config.Target("https://a.example.com/"),
            "b": config.Target("https://b.example.com/"),
        })
        main_mod.main()
    finally:
        main_mod.log.info = real_info

    summary = [args for fmt, args in seen if "全部跑完" in fmt]
    assert summary and summary[0][0] == 4      # added 2 + 2
    assert summary[0][2] == 2                  # skipped 1 + 1
