import sqlite3
from datetime import datetime

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
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
"""
#初始化
def init_db():
    conn = sqlite3.connect(config.DB_NAME)
    conn.execute(SCHEMA)
    conn.commit()
    return conn
#取已存的 content_hash，没有这条返回 None
def saved_hash(conn:sqlite3.Connection,dedup_key:str) -> str | None:
    c = conn.cursor()
    c.execute(
        "SELECT content_hash FROM articles WHERE dedup_key = ? LIMIT 1",(dedup_key,)
    )
    row=c.fetchone()
    return row[0] if row else None
#储存
def save_article(
        conn:sqlite3.Connection,
        source:str,
        url:str,
        day:str,
        title:str,
        summary:str,
        content:str,
        access_status:str,
        dedup_key:str,
        content_hash:str,
) -> None:
    c = conn.cursor()
    c.execute(
        """INSERT INTO articles (source, url, day, title, summary, content, access_status, fetched_at, dedup_key, content_hash)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(dedup_key) DO UPDATE SET
               source = excluded.source,
               day = excluded.day,
               title = excluded.title,
               summary = excluded.summary,
               content = excluded.content,
               access_status = excluded.access_status,
               fetched_at = excluded.fetched_at,
               content_hash = excluded.content_hash""",
        (source, url, day, title, summary, content, access_status,
         datetime.now().isoformat(timespec="seconds"), dedup_key, content_hash)
    )

def commit_db(conn: sqlite3.Connection) -> None:
    conn.commit()
