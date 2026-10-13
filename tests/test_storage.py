import config
import storage


def test_save_then_read(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_NAME", tmp_path / "t.db")
    conn = storage.init_db()
    storage.save_article(conn, "src", "https://a.com/1", "2026-01-01", "标题", "摘要", "正文", "normal", "k1", "h1")
    storage.commit_db(conn)
    assert storage.saved_hash(conn, "k1") == "h1"
    assert storage.saved_hash(conn, "nope") is None
    conn.close()


def test_same_key_updates(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_NAME", tmp_path / "t.db")
    conn = storage.init_db()
    storage.save_article(conn, "src", "https://a.com/1", "2026-01-01", "旧", "s", "c", "normal", "k1", "h1")
    storage.save_article(conn, "src", "https://a.com/1", "2026-01-02", "新", "s", "c", "normal", "k1", "h2")
    storage.commit_db(conn)
    assert storage.saved_hash(conn, "k1") == "h2"
    assert conn.execute("select count(*) from articles").fetchone()[0] == 1
    conn.close()
