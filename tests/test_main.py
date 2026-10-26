import asyncio

import config
import main as main_mod


async def _noop():
    pass


async def _fake_close_all():
    pass


class _FakeConn:
    def __init__(self):
        self.rollbacks = 0

    def close(self):
        pass

    def rollback(self):
        self.rollbacks += 1


def _setup(monkeypatch):
    calls = []
    monkeypatch.setattr(main_mod, "setup_logger", lambda: None)
    monkeypatch.setattr(main_mod, "log_tiers", lambda: None)
    monkeypatch.setattr(main_mod, "init_db", lambda: _FakeConn())
    monkeypatch.setattr(main_mod, "close_all", _fake_close_all)

    async def fake_crawl(target, conn):
        calls.append(target.url)
        return main_mod.CrawlStats(added=1)

    monkeypatch.setattr(main_mod, "crawl_and_save", fake_crawl)
    return calls


def test_main_passes_target_through_without_route_override(monkeypatch):
    calls = _setup(monkeypatch)
    monkeypatch.setattr(
        config,
        "TARGETS",
        {
            "force": config.Target("https://force.example.com/", route=config.Route.FORCE),
            "auto": config.Target("https://auto.example.com/", route=config.Route.AUTO),
            "direct": config.Target("https://direct.example.com/", route=config.Route.DIRECT),
        },
    )
    asyncio.run(main_mod.main())
    # main 不预判网络环境，路由完全交给 target
    assert calls == [
        "https://force.example.com/",
        "https://auto.example.com/",
        "https://direct.example.com/",
    ]


def test_inactive_target_skipped(monkeypatch):
    calls = _setup(monkeypatch)
    monkeypatch.setattr(
        config,
        "TARGETS",
        {
            "off": config.Target("https://off.example.com/", enable=False),
            "empty": config.Target(""),
            "on": config.Target("https://on.example.com/"),
        },
    )
    asyncio.run(main_mod.main())
    assert calls == ["https://on.example.com/"]


def test_one_failed_target_does_not_stop_the_batch(monkeypatch):
    # 框架级隔离：任何单个目标抛异常，后续目标必须照跑
    conn = _FakeConn()
    calls = []
    monkeypatch.setattr(main_mod, "setup_logger", lambda: None)
    monkeypatch.setattr(main_mod, "log_tiers", lambda: None)
    monkeypatch.setattr(main_mod, "init_db", lambda: conn)
    monkeypatch.setattr(main_mod, "close_all", _fake_close_all)

    async def crawl(target, conn):
        calls.append(target.url)
        if "b" in target.url:
            raise RuntimeError("模拟解析器/翻页/入库抛异常")
        return main_mod.CrawlStats(added=1)

    monkeypatch.setattr(main_mod, "crawl_and_save", crawl)
    monkeypatch.setattr(config, "TARGETS", {k: config.Target(f"https://{k}.example/") for k in "abc"})

    asyncio.run(main_mod.main())

    assert calls == ["https://a.example/", "https://b.example/", "https://c.example/"]
    assert conn.rollbacks == 1  # 失败目标的事务已回滚


def test_stats_accumulated_across_targets(monkeypatch):
    monkeypatch.setattr(main_mod, "setup_logger", lambda: None)
    monkeypatch.setattr(main_mod, "log_tiers", lambda: None)
    monkeypatch.setattr(main_mod, "init_db", lambda: _FakeConn())
    monkeypatch.setattr(main_mod, "close_all", _fake_close_all)

    seen = []

    async def fake_crawl(target, conn):
        return main_mod.CrawlStats(added=2, skipped=1)

    monkeypatch.setattr(main_mod, "crawl_and_save", fake_crawl)

    real_info = main_mod.log.info
    main_mod.log.info = lambda fmt, *a: seen.append((fmt, a))
    try:
        monkeypatch.setattr(
            config,
            "TARGETS",
            {
                "a": config.Target("https://a.example.com/"),
                "b": config.Target("https://b.example.com/"),
            },
        )
        asyncio.run(main_mod.main())
    finally:
        main_mod.log.info = real_info

    summary = [args for fmt, args in seen if "全部跑完" in fmt]
    assert summary and summary[0][0] == 4  # added 2 + 2
    assert summary[0][2] == 2  # skipped 1 + 1


def test_concurrent_targets_use_isolated_connections(monkeypatch):
    monkeypatch.setattr(config, "MAX_CONCURRENT_TARGETS", 3)
    conns = []

    class _Conn:
        def __init__(self):
            self.closed = False
            self.rollbacks = 0

        def close(self):
            self.closed = True

        def rollback(self):
            self.rollbacks += 1

    def fake_init_db():
        conn = _Conn()
        conns.append(conn)
        return conn

    calls = []

    async def fake_crawl(target, conn):
        calls.append(target.url)
        if "b" in target.url:
            raise RuntimeError("模拟单站失败")
        return main_mod.CrawlStats(added=1)

    monkeypatch.setattr(main_mod, "setup_logger", lambda: None)
    monkeypatch.setattr(main_mod, "log_tiers", lambda: None)
    monkeypatch.setattr(main_mod, "init_db", fake_init_db)
    monkeypatch.setattr(main_mod, "close_all", _fake_close_all)
    monkeypatch.setattr(main_mod, "crawl_and_save", fake_crawl)
    monkeypatch.setattr(config, "TARGETS", {k: config.Target(f"https://{k}.example/") for k in "abc"})

    asyncio.run(main_mod.main())

    assert sorted(calls) == ["https://a.example/", "https://b.example/", "https://c.example/"]
    assert len(conns) == 3  # 每个目标独立连接
    assert all(c.closed for c in conns)  # 全部释放
    assert sum(c.rollbacks for c in conns) == 1  # 失败目标各自回滚
