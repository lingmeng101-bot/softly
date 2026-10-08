import logging
from dataclasses import asdict, is_dataclass

from config import Log
from crawler.fetcher import fetch
from crawler.sniff import sniff
from crawler.sites import get_parser, host_of
from hashing import content_hash, dedup_key
from storage import commit_db, link_exists, save_article

log = logging.getLogger(Log.LOG_NAME)

def as_dict(item) -> dict:
    return asdict(item) if is_dataclass(item) else item

def crawl_and_save(target, conn, max_pages: int = 1) -> tuple[int, int]:
    url = target.url
    page = 0
    added = 0
    skipped = 0
    bad = 0
    seen = set()

    while url and page < max_pages:
        log.info("[list %d/%d] %s", page + 1, max_pages, url)
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
                bad += 1
                continue
            key = dedup_key(link)
            if key in seen or link_exists(conn, key):
                skipped += 1
                continue
            seen.add(key)

            save_article(
                conn,
                source=host_of(link),
                url=link,
                day=row.get("day"),
                title=title,
                summary=row.get("summary"),
                content=row.get("content") or "",
                access_status="normal",
                dedup_key=key,
                content_hash=content_hash(title, row.get("content") or ""),
            )
            added += 1
            log.info("  [+] %s 【%s】%s", host_of(link), row.get("day") or "-", title[:30])

        commit_db(conn)

        #翻页，没有一夜结束
        if not hasattr(mod, "next_page_url"):
            break
        url = mod.next_page_url(res.text, source=str(res.url))
        page += 1

    log.info("本目标：新增 %d，跳过 %d，异常 %d", added, skipped, bad)
    return added, bad
