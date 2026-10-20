import logging
import sqlite3
from dataclasses import asdict, dataclass, is_dataclass

import config
import paginator
from config import Log
from crawler.fetcher import FetchFailed, ProxyDown, TIERS as FETCH_TIERS
from crawler.fetcher import fetch, looks_like_shell
from crawler.guard import check
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


def crawl_and_save(target, conn, route=None) -> CrawlStats:
    stats = CrawlStats()
    url = target.url
    page = 0
    cap = min(target.max_pages, config.MAX_PAGES_HARD)
    fetch_route = route if route is not None else target.route
    tiers = FETCH_TIERS
    visited: set[str] = set()
    seen: set[str] = set()

    while url and page < cap:
        #环路保护：参数猜错时别死在 while 里
        if url in visited:
            log.info("翻页结束（检测到环路）")
            break
        visited.add(url)

        try:
            f = fetch(url, route=fetch_route, proxy_group=target.proxy_group,
                      rotate_on_block=config.PLAYWRIGHT_ROTATE_ON_BLOCK)
        except ProxyDown as e:
            log.error("代理不可用，本轮结束: %s (%s)", url, e)
            break
        except FetchFailed as e:
            log.error("列表页请求失败，本轮结束: %s (%s)", url, e)
            break

        log.info("[list %d/%d] tier=%s %s", page + 1, cap, f.tier, url)
        res = f.res
        kind = sniff(res)
        payload = res.text
        access_status, error_msg = "normal", None
        allow_downgrade = True

        if kind == "json":
            try:
                payload = res.json()
            except Exception as e:
                log.warning("自称 json 但解析失败，按 html 处理: %s", e)
                kind = "html"
        elif kind == "xml":
            #lxml 能解析 xml，按 html 走
            pass
        else:
            if kind == "text":
                log.warning("不支持的内容类型 text，跳过: %s", url)
                stats.bad += 1
                break

        #接口报错（200 但 code 报错）在解析前统一判，专属解析器不用各写一遍
        if isinstance(payload, (dict, list)):
            err = check(payload)
            if err:
                log.warning("接口报错：%s", err)
                access_status, error_msg = "api_error", err
                payload = []
                allow_downgrade = False

        #判断专属还是通用
        mod = get_parser(str(res.url))
        log.info("用 %s 解析", mod.__name__)
        try:
            items = mod.parse_list(payload, source=str(res.url))
        except Exception as e:
            log.exception("解析异常: %s (%s)", url, e)
            items = []
            #解析器坏了跟 TLS 指纹无关，别让内容降级白跑 playwright
            stats.parse_error += 1
            access_status = "parse_error"
            allow_downgrade = False

        #内容层降级：200 但只有空壳（列表靠 JS 渲染），换下一层重取一次
        if (not items and allow_downgrade and kind == "html" and f.tier in tiers
                and tiers.index(f.tier) + 1 < len(tiers)
                and looks_like_shell(res)):
            nxt = tiers[tiers.index(f.tier) + 1]
            log.warning("内容为空，改用 %s 重取: %s", nxt, url)
            try:
                f = fetch(url, min_tier=nxt, route=fetch_route, proxy_group=target.proxy_group)
                res = f.res
                items = mod.parse_list(res.text, source=str(res.url))
            except Exception as e:
                log.warning("内容降级失败，保持空结果: %s (%s)", url, e)

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
            #开了 fetch_detail 时 content_hash 存的是详情页正文，跟列表页算出来的对不上，
            #所以改用「URL 已存在且状态正常」判重，避免每次重跑都重抓一遍详情页
            if target.fetch_detail:
                skip = old_hash is not None and old_status not in RETRYABLE
            else:
                skip = old_hash == chash and old_status not in RETRYABLE
            if skip:
                stats.skipped += 1
                continue
            seen.add(key)

            content = row.get("content") or ""
            item_status, item_err = access_status, error_msg
            #详情页：只对新条目抓，天然限流；站点没 parse_detail 就跳过
            if target.fetch_detail:
                detail_fn = getattr(mod, "parse_detail", None)
                if detail_fn:
                    try:
                        d = fetch(link, route=fetch_route)
                        detail_text = detail_fn(d.res.text)
                        if detail_text == "blocked":
                            item_status = "blocked"
                            content = ""
                            item_err = item_err or "详情页被拦（返回系统提示页）"
                        elif detail_text:
                            content = detail_text
                        else:
                            #抓到了但没正文：标 empty，下轮还会重试
                            item_status = "empty"
                            item_err = item_err or "详情页没解析出正文"
                    except Exception as e:
                        #详情页失败不能标 normal，否则下次会被"状态正常"永久跳过
                        item_status = "empty"
                        item_err = f"详情页抓取失败: {type(e).__name__}"
                        log.warning("详情页失败 url=%s (%s)", link, e)
                chash = content_hash(title, content)

            try:
                save_article(
                    conn,
                    source=host_of(link),
                    url=link,
                    publish_time=row.get("publish_time"),
                    title=title,
                    summary=row.get("summary"),
                    content=content,
                    access_status=item_status,
                    dedup_key=key,
                    content_hash=chash,
                    error_msg=item_err,
                )
                if item_status == "blocked":
                    stats.blocked += 1
                    log.warning("  [!] %s 【%s】%s", host_of(link), row.get("publish_time") or "-", title[:30])
                else:
                    stats.added += 1
                    log.info("  [+] %s 【%s】%s", host_of(link), row.get("publish_time") or "-", title[:30])
            except sqlite3.Error as e:
                #单条入库失败不能中断整个 target
                stats.bad += 1
                log.exception("入库失败 url=%s (%s)", link, e)

        commit_db(conn)

        #翻页，站点自己有就用它的，没有就猜参数
        if hasattr(mod, "next_page_url"):
            nxt_url = mod.next_page_url(res.text, source=str(res.url))
        else:
            nxt_url = paginator.next_page_url(url)
        if not nxt_url or nxt_url == url or nxt_url in visited:
            log.info("翻页结束（%s）", "无下一页" if not nxt_url else "检测到环路")
            break
        url = nxt_url
        page += 1

    log.info("本目标：新增 %d，拦截 %d，跳过 %d，异常 %d，解析错 %d",
             stats.added, stats.blocked, stats.skipped, stats.bad, stats.parse_error)
    return stats