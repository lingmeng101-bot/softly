import logging
import sqlite3
from datetime import datetime

import config
from config import Log

log = logging.getLogger(Log.LOG_NAME)

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY,
    source TEXT,
    url TEXT NOT NULL,
    publish_time TEXT,
    title TEXT NOT NULL,
    summary TEXT,
    content TEXT,
    access_status TEXT DEFAULT 'normal',
    error_msg TEXT,
    fetched_at TEXT,
    dedup_key TEXT NOT NULL,
    content_hash TEXT,
    UNIQUE(dedup_key)
)
"""


# 旧库迁移：day → publish_time，并补充 error_msg 列
def _migrate(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(articles)")}
    if "day" in cols and "publish_time" not in cols:
        conn.execute("ALTER TABLE articles ADD COLUMN publish_time TEXT")
        conn.execute("UPDATE articles SET publish_time = day")
        log.info("迁移：day → publish_time")
    if "error_msg" not in cols:
        conn.execute("ALTER TABLE articles ADD COLUMN error_msg TEXT")
        log.info("迁移：新增 error_msg 列")


# 初始化：建目录、建表、迁移
def init_db():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


# 读取已存的 content_hash、access_status 与 fetched_at；无记录时返回 (None, None, None)
def saved_state(conn: sqlite3.Connection, dedup_key: str) -> tuple[str | None, str | None, str | None]:
    row = conn.execute(
        "SELECT content_hash, access_status, fetched_at FROM articles WHERE dedup_key = ? LIMIT 1",
        (dedup_key,),
    ).fetchone()
    return (row[0], row[1], row[2]) if row else (None, None, None)


# 写入：dedup_key 唯一，冲突时更新
def save_article(
    conn: sqlite3.Connection,
    *,
    source: str,
    url: str,
    publish_time: str | None,
    title: str,
    summary: str | None,
    content: str,
    access_status: str,
    dedup_key: str,
    content_hash: str,
    error_msg: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO articles (source, url, publish_time, title, summary, content, access_status, error_msg, fetched_at, dedup_key, content_hash)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(dedup_key) DO UPDATE SET
               source = excluded.source,
               publish_time = excluded.publish_time,
               title = excluded.title,
               summary = excluded.summary,
               content = excluded.content,
               access_status = excluded.access_status,
               error_msg = excluded.error_msg,
               fetched_at = excluded.fetched_at,
               content_hash = excluded.content_hash""",
        (
            source,
            url,
            publish_time,
            title,
            summary,
            content,
            access_status,
            error_msg,
            datetime.now().isoformat(timespec="seconds"),
            dedup_key,
            content_hash,
        ),
    )


def commit_db(conn: sqlite3.Connection) -> None:
    conn.commit()
