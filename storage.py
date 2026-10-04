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
    UNIQUE(url)
)
"""
#初始化
def init_db():
    conn = sqlite3.connect(config.DB_NAME)
    conn.execute(SCHEMA)
    conn.commit()
    return conn
#二层去重
def link_exists(conn:sqlite3.Connection,url:str) -> bool:
    c = conn.cursor()
    c.execute(
        "SELECT 1 FROM articles WHERE url = ? LIMIT 1",(url,)
    )
    exists=c.fetchone() is not None
    return exists
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
) -> None:
    c = conn.cursor()
    c.execute(
        """INSERT INTO articles (source, url, day, title, summary, content, access_status, fetched_at)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(url) DO UPDATE SET
               source = excluded.source,
               day = excluded.day,
               title = excluded.title,
               summary = excluded.summary,
               content = excluded.content,
               access_status = excluded.access_status,
               fetched_at = excluded.fetched_at""",
        (source, url, day, title, summary, content, access_status,
         datetime.now().isoformat(timespec="seconds"))   #抓取时间就地生成
    )
#提交，一页写完再提交
def commit_db(conn: sqlite3.Connection) -> None:
    conn.commit()
