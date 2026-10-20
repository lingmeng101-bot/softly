import types

import config
import pipeline
import storage
from crawler.fetcher import TIERS, FetchFailed, Fetched
from models import Article


class FakeRes:
    def __init__(self, text="", ct="text/html; charset=utf-8", payload=None, url=None, status=200):
        self.text = text
        self.headers = {"content-type": ct}
        self.status_code = status
        self.url = url or "https://fake.example.com/list"
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def fetched(res, tier="httpx"):
    return Fetched(res=res, tier=tier, attempts=[])


def make_mod(parse_list, next_page=None):
    mod = types.SimpleNamespace(__name__="fake_mod", parse_list=parse_list)
    if next_page is not None:
        mod.next_page_url = next_page
    return mod


def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_NAME", tmp_path / "p.db")
    return storage.init_db()


def target(**kw):
    kw.setdefault("url", "https://fake.example.com/list")
    kw.setdefault("max_pages", 1)
    return config.Target(**kw)


ITEM = {"url": "https://a.com/1", "title": "标题", "summary": "摘要", "publish_time": "2026-01-01"}


def test_save_items_and_stats(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "looks_like_shell", lambda res: False)
    monkeypatch.setattr(pipeline, "fetch", lambda url, **kw: fetched(FakeRes(text="<html>ok</html>")))
    monkeypatch.setattr(pipeline, "get_parser", lambda url: make_mod(lambda payload, source: [ITEM]))
    conn = db(tmp_path, monkeypatch)
    stats = pipeline.crawl_and_save(target(), conn)
    assert (stats.added, stats.skipped, stats.bad, stats.parse_error) == (1, 0, 0, 0)
    row = conn.execute("select publish_time, title, access_status from articles").fetchone()
    assert tuple(row) == ("2026-01-01", "标题", "normal")
    # 账平
    assert stats.added + stats.skipped + stats.bad + stats.blocked + stats.parse_error == 1
    conn.close()


def test_article_dataclass_item(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "looks_like_shell", lambda res: False)
    monkeypatch.setattr(pipeline, "fetch", lambda url, **kw: fetched(FakeRes(text="<html>ok</html>")))
    item = Article(url="https://a.com/2", title="T2")
    monkeypatch.setattr(pipeline, "get_parser", lambda url: make_mod(lambda payload, source: [item]))
    conn = db(tmp_path, monkeypatch)
    stats = pipeline.crawl_and_save(target(), conn)
    assert stats.added == 1
    conn.close()


def _setup_second_run(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "looks_like_shell", lambda res: False)
    monkeypatch.setattr(pipeline, "fetch", lambda url, **kw: fetched(FakeRes(text="<html>ok</html>")))
    monkeypatch.setattr(pipeline, "get_parser", lambda url: make_mod(lambda payload, source: [ITEM]))
    return db(tmp_path, monkeypatch)


def test_second_run_skips_same_hash(tmp_path, monkeypatch):
    conn = _setup_second_run(tmp_path, monkeypatch)
    pipeline.crawl_and_save(target(), conn)
    second = pipeline.crawl_and_save(target(), conn)
    assert (second.added, second.skipped) == (0, 1)
    conn.close()


def test_retryable_status_allows_overwrite(tmp_path, monkeypatch):
    conn = _setup_second_run(tmp_path, monkeypatch)
    pipeline.crawl_and_save(target(), conn)
    conn.execute("update articles set access_status='blocked'")
    conn.commit()
    third = pipeline.crawl_and_save(target(), conn)
    assert (third.added, third.skipped) == (1, 0)
    assert conn.execute("select count(*) from articles").fetchone()[0] == 1
    conn.close()


def test_missing_url_or_title_counts_bad(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "looks_like_shell", lambda res: False)
    monkeypatch.setattr(pipeline, "fetch", lambda url, **kw: fetched(FakeRes(text="<html>ok</html>")))
    items = [{"title": "没有 url"}, {"url": "https://a.com/3"}, ITEM]
    monkeypatch.setattr(pipeline, "get_parser", lambda url: make_mod(lambda payload, source: items))
    conn = db(tmp_path, monkeypatch)
    stats = pipeline.crawl_and_save(target(), conn)
    assert (stats.added, stats.bad) == (1, 2)
    assert stats.added + stats.skipped + stats.bad == 3
    conn.close()


def test_text_kind_is_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "sniff", lambda res: "text")
    calls = []

    def boom(url, **kw):
        calls.append(url)
        return fetched(FakeRes(text="hello"))

    def no_parser(url):
        raise AssertionError("text 页面不该进解析器")

    monkeypatch.setattr(pipeline, "fetch", boom)
    monkeypatch.setattr(pipeline, "get_parser", no_parser)
    conn = db(tmp_path, monkeypatch)
    stats = pipeline.crawl_and_save(target(), conn)
    assert stats.bad == 1
    assert len(calls) == 1
    conn.close()


def test_parse_error_suppresses_content_downgrade(tmp_path, monkeypatch):
    calls = []

    def fake_fetch(url, **kw):
        calls.append(kw.get("min_tier"))
        return fetched(FakeRes(text="<html>" + "x" * 5000))

    def boom(payload, source):
        raise RuntimeError("selector 坏了")

    monkeypatch.setattr(pipeline, "fetch", fake_fetch)
    monkeypatch.setattr(pipeline, "looks_like_shell", lambda res: True)
    monkeypatch.setattr(pipeline, "get_parser", lambda url: make_mod(boom))
    conn = db(tmp_path, monkeypatch)
    stats = pipeline.crawl_and_save(target(), conn)
    assert stats.parse_error == 1
    assert calls == [None]          # 没有触发第二次（min_tier）请求
    conn.close()


def test_api_error_stops_before_parse(tmp_path, monkeypatch):
    got = []

    def fake_parse(payload, source):
        got.append(payload)
        return []

    payload = {"code": 403, "msg": "forbidden"}
    monkeypatch.setattr(pipeline, "sniff", lambda res: "json")
    monkeypatch.setattr(pipeline, "fetch", lambda url, **kw: fetched(
        FakeRes(text='{"code":403}', ct="application/json", payload=payload)))
    monkeypatch.setattr(pipeline, "get_parser", lambda url: make_mod(fake_parse))
    conn = db(tmp_path, monkeypatch)
    stats = pipeline.crawl_and_save(target(), conn)
    assert got == [[]]              # 解析器拿到空列表，不再猜
    assert stats.added == 0
    conn.close()


def test_next_page_same_url_stops(tmp_path, monkeypatch):
    calls = []

    def fake_fetch(url, **kw):
        calls.append(url)
        return fetched(FakeRes(text="<html>ok</html>"))

    monkeypatch.setattr(pipeline, "fetch", fake_fetch)
    monkeypatch.setattr(pipeline, "looks_like_shell", lambda res: False)
    mod = make_mod(lambda payload, source: [], next_page=lambda html, source: "https://fake.example.com/list")
    monkeypatch.setattr(pipeline, "get_parser", lambda url: mod)
    conn = db(tmp_path, monkeypatch)
    stats = pipeline.crawl_and_save(target(max_pages=5), conn)
    assert len(calls) == 1
    conn.close()


def test_pagination_respects_max_pages(tmp_path, monkeypatch):
    urls = []

    def fake_fetch(url, **kw):
        urls.append(url)
        return fetched(FakeRes(text="<html>ok</html>", url=url))

    monkeypatch.setattr(pipeline, "fetch", fake_fetch)
    monkeypatch.setattr(pipeline, "looks_like_shell", lambda res: False)
    monkeypatch.setattr(pipeline, "get_parser", lambda url: make_mod(lambda payload, source: []))
    conn = db(tmp_path, monkeypatch)
    pipeline.crawl_and_save(target(url="https://fake.example.com/list?page=1", max_pages=3), conn)
    assert urls == [
        "https://fake.example.com/list?page=1",
        "https://fake.example.com/list?page=2",
        "https://fake.example.com/list?page=3",
    ]
    conn.close()


def test_content_downgrade_once(tmp_path, monkeypatch):
    calls = []
    shell = FakeRes(text="<html><body><div id=app></div></body></html>")
    full = FakeRes(text="<html><body>" + "<a href='/x'>t</a>" * 10 + "y" * 4000 + "</body></html>")

    def fake_fetch(url, **kw):
        calls.append(kw.get("min_tier"))
        return fetched(full if kw.get("min_tier") else shell, tier=kw.get("min_tier") or "httpx")

    def parse_list(payload, source):
        return [] if "<a " not in payload else [ITEM]

    monkeypatch.setattr(pipeline, "fetch", fake_fetch)
    monkeypatch.setattr(pipeline, "get_parser", lambda url: make_mod(parse_list))
    conn = db(tmp_path, monkeypatch)
    stats = pipeline.crawl_and_save(target(), conn)
    assert stats.added == 1
    assert len(calls) == 2 and calls[0] is None and calls[1] == TIERS[1]
    conn.close()

def _detail_fetch(detail_calls, detail_html="<html>detail</html>"):
    def fake_fetch(url, **kw):
        if url.startswith("https://a.com/"):
            detail_calls.append(url)
            return fetched(FakeRes(text=detail_html, url=url))
        return fetched(FakeRes(text="<html>ok</html>"))
    return fake_fetch


def test_fetch_detail_content_saved(tmp_path, monkeypatch):
    mod = make_mod(lambda payload, source: [ITEM])
    mod.parse_detail = lambda html: "正文内容"
    monkeypatch.setattr(pipeline, "fetch", _detail_fetch([]))
    monkeypatch.setattr(pipeline, "looks_like_shell", lambda res: False)
    monkeypatch.setattr(pipeline, "get_parser", lambda url: mod)
    conn = db(tmp_path, monkeypatch)
    stats = pipeline.crawl_and_save(target(fetch_detail=True), conn)
    assert (stats.added, stats.blocked) == (1, 0)
    row = conn.execute("select content, access_status from articles").fetchone()
    assert row[0] == "正文内容" and row[1] == "normal"
    conn.close()


def test_fetch_detail_blocked_is_saved_and_retryable(tmp_path, monkeypatch):
    mod = make_mod(lambda payload, source: [ITEM])
    mod.parse_detail = lambda html: "blocked"
    monkeypatch.setattr(pipeline, "fetch", _detail_fetch([], "<html>系统提示</html>"))
    monkeypatch.setattr(pipeline, "looks_like_shell", lambda res: False)
    monkeypatch.setattr(pipeline, "get_parser", lambda url: mod)
    conn = db(tmp_path, monkeypatch)
    stats = pipeline.crawl_and_save(target(fetch_detail=True), conn)
    assert (stats.added, stats.blocked) == (0, 1)
    row = conn.execute("select content, access_status, error_msg from articles").fetchone()
    assert row[0] == "" and row[1] == "blocked" and row[2]
    assert stats.added + stats.skipped + stats.bad + stats.blocked + stats.parse_error == 1
    conn.close()


def test_fetch_detail_failure_keeps_list_content(tmp_path, monkeypatch):
    item = dict(ITEM, content="列表正文")

    def fake_fetch(url, **kw):
        if url.startswith("https://a.com/"):
            raise FetchFailed("detail down")
        return fetched(FakeRes(text="<html>ok</html>"))

    mod = make_mod(lambda payload, source: [item])
    mod.parse_detail = lambda html: "x"
    monkeypatch.setattr(pipeline, "fetch", fake_fetch)
    monkeypatch.setattr(pipeline, "looks_like_shell", lambda res: False)
    monkeypatch.setattr(pipeline, "get_parser", lambda url: mod)
    conn = db(tmp_path, monkeypatch)
    stats = pipeline.crawl_and_save(target(fetch_detail=True), conn)
    assert stats.added == 1
    assert conn.execute("select content from articles").fetchone()[0] == "列表正文"
    conn.close()


def test_fetch_detail_without_parse_detail_is_skipped(tmp_path, monkeypatch):
    detail_calls = []
    mod = make_mod(lambda payload, source: [ITEM])          # 没有 parse_detail
    monkeypatch.setattr(pipeline, "fetch", _detail_fetch(detail_calls))
    monkeypatch.setattr(pipeline, "looks_like_shell", lambda res: False)
    monkeypatch.setattr(pipeline, "get_parser", lambda url: mod)
    conn = db(tmp_path, monkeypatch)
    stats = pipeline.crawl_and_save(target(fetch_detail=True), conn)
    assert stats.added == 1
    assert detail_calls == []
    conn.close()


def test_fetch_detail_second_run_does_not_refetch(tmp_path, monkeypatch):
    detail_calls = []
    mod = make_mod(lambda payload, source: [ITEM])
    mod.parse_detail = lambda html: "正文内容"
    monkeypatch.setattr(pipeline, "fetch", _detail_fetch(detail_calls))
    monkeypatch.setattr(pipeline, "looks_like_shell", lambda res: False)
    monkeypatch.setattr(pipeline, "get_parser", lambda url: mod)
    conn = db(tmp_path, monkeypatch)
    pipeline.crawl_and_save(target(fetch_detail=True), conn)
    second = pipeline.crawl_and_save(target(fetch_detail=True), conn)
    assert (second.added, second.skipped) == (0, 1)
    assert len(detail_calls) == 1
    conn.close()
