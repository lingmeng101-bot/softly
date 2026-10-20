import logging
import sqlite3
from dataclasses import asdict, dataclass, is_dataclass

import config
import paginator
from config import Log
from crawler.fetcher import fetch
from crawler.sniff import sniff
from crawler.sites import get_parser, host_of
from hashing import content_hash, dedup_key
from storage import commit_db, save_article, saved_state

log = logging.getLogger(Log.LOG_NAME)

#这些状态说明上一条没抓干净，重跑时要允许覆盖
RETRYABLE = {"blocked", "api_error", "empty", "text_unsupported", "parse_error"}


@dataclass
class CrawlStats:
    added: int = 0
    skipped: int = 0
    bad: int = 0
    blocked: int = 0
    parse_error: int = 0

    def __iadd__(self, other):
        self.added += other.added
        self.skipped += other.skipped
        self.bad += other.bad
        self.blocked += other.blocked
        self.parse_error += other.parse_error
        return self


def as_dict(item) -> dict:
    return asdict(item) if is_dataclass(item) else item


def crawl_and_save(target, conn) -> CrawlStats:
    stats = CrawlStats()
    url = target.url
    page = 0
    cap = min(target.max_pages, config.MAX_PAGES_HARD)
    visited: set[str] = set()
    seen: set[str] = set()

    while url and page < cap:
        #环路保护：参数猜错时别死在 while 里
        if url in visited:
            log.info("翻页结束（检测到环路）")
            break
        visited.add(url)

        log.info("[list %d/%d] %s", page + 1, cap, url)
        try:
            res = fetch(url)
        except Exception as e:
            log.error("列表页请求失败，本轮结束: %s (%s)", url, e)
            break

        kind = sniff(res)
        payload = res.text
        if kind == "json":
            try:
                payload = res.json()
            except Exception as e:
                log.warning("自称 json 但解析失败，按 html 处理: %s", e)

        #判断专属还是通用
        mod = get_parser(str(res.url))
        log.info("用 %s 解析", mod.__name__)
        try:
            items = mod.parse_list(payload, source=str(res.url))
        except Exception as e:
            log.exception("解析异常: %s (%s)", url, e)
            items = []

        if not items:
            log.warning("列表页没解析出任何条目，页面结构可能变了: %s", url)

        #最后判断，缺 url 或标题的直接丢
        for item in items:
            row = as_dict(item)
            link = str(row.get("url") or "").strip()
            title = str(row.get("title") or "").strip()
            if not link or not title:
                stats.bad += 1
                continue
            key = dedup_key(link)
            chash = content_hash(title, row.get("content") or "")
            old_hash, old_status = saved_state(conn, key)
            if key in seen:
                stats.skipped += 1
                continue
            #hash 没变且上次是正常状态，跳过；上次是异常状态，允许重抓覆盖
            if old_hash == chash and old_status not in RETRYABLE:
                stats.skipped += 1
                continue
            seen.add(key)

            try:
                save_article(
                    conn,
                    source=host_of(link),
                    url=link,
                    publish_time=row.get("publish_time"),
                    title=title,
                    summary=row.get("summary"),
                    content=row.get("content") or "",
                    access_status="normal",
                    dedup_key=key,
                    content_hash=chash,
                )
                stats.added += 1
                log.info("  [+] %s 【%s】%s", host_of(link), row.get("publish_time") or "-", title[:30])
            except sqlite3.Error as e:
                #单条入库失败不能中断整个 target
                stats.bad += 1
                log.exception("入库失败 url=%s (%s)", link, e)

        commit_db(conn)

        #翻页，站点自己有就用它的，没有就猜参数
        if hasattr(mod, "next_page_url"):
            nxt = mod.next_page_url(res.text, source=str(res.url))
        else:
            nxt = paginator.next_page_url(url)
        if not nxt or nxt == url or nxt in visited:
            log.info("翻页结束（%s）", "无下一页" if not nxt else "检测到环路")
            break
        url = nxt
        page += 1

    log.info("本目标：新增 %d，跳过 %d，异常 %d", stats.added, stats.skipped, stats.bad)
    return stats
