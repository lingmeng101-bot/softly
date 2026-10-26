import sqlite3

import config
import storage


def _save(conn, **kw):
    args = {
        "source": "src",
        "url": "https://a.com/1",
        "publish_time": "2026-01-01",
        "title": "标题",
        "summary": "摘要",
        "content": "正文",
        "access_status": "normal",
        "dedup_key": "k1",
        "content_hash": "h1",
    }
    args.update(kw)
    storage.save_article(conn, **args)


def test_save_then_read(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_NAME", tmp_path / "t.db")
    conn = storage.init_db()
    _save(conn)
    storage.commit_db(conn)
    state = storage.saved_state(conn, "k1")
    assert state[:2] == ("h1", "normal")
    assert state[2]  # fetched_at 非空
    assert storage.saved_state(conn, "nope") == (None, None, None)
    conn.close()


def test_same_key_updates(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_NAME", tmp_path / "t.db")
    conn = storage.init_db()
    _save(conn, title="旧", content_hash="h1")
    _save(conn, publish_time="2026-01-02", title="新", content_hash="h2")
    storage.commit_db(conn)
    assert storage.saved_state(conn, "k1")[:2] == ("h2", "normal")
    assert conn.execute("select count(*) from articles").fetchone()[0] == 1
    conn.close()


def test_error_state_stored(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_NAME", tmp_path / "t.db")
    conn = storage.init_db()
    _save(conn, access_status="blocked", content_hash="h1", error_msg="系统提示")
    storage.commit_db(conn)
    assert storage.saved_state(conn, "k1")[:2] == ("h1", "blocked")
    row = conn.execute("select error_msg from articles where dedup_key='k1'").fetchone()
    assert row[0] == "系统提示"
    conn.close()


def test_migrate_day_to_publish_time(tmp_path, monkeypatch):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE articles (
            id INTEGER PRIMARY KEY,
            source TEXT,
            url TEXT NOT NULL,
            day TEXT,
            title TEXT NOT NULL,
            summary TEXT,
            content TEXT,
            access_status TEXT DEFAULT 'normal',
            fetched_at TEXT,
            dedup_key TEXT NOT NULL,
            content_hash TEXT,
            UNIQUE(dedup_key)
        )
    """)
    conn.execute("insert into articles (source, url, day, title, dedup_key) values ('s','u','2026-01-02','t','k1')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(config, "DB_NAME", db)
    conn = storage.init_db()
    publish_time, error_msg = conn.execute("select publish_time, error_msg from articles where dedup_key='k1'").fetchone()
    assert publish_time == "2026-01-02"
    assert error_msg is None
    conn.close()
