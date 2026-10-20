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

# 重试白名单：这些状态视为未完整抓取，下次允许覆盖
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
        # 环路保护：防止翻页参数错误导致死循环
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
            # xml 交由 lxml 解析，按 html 处理
            pass
        else:
            if kind == "text":
                log.warning("不支持的内容类型 text，跳过: %s", url)
                stats.bad += 1
                break

        # 接口级错误在解析前统一判断，各解析器无需重复实现
        if isinstance(payload, (dict, list)):
            err = check(payload)
            if err:
                log.warning("接口报错：%s", err)
                access_status, error_msg = "api_error", err
                payload = []
                allow_downgrade = False

        # 选择站点解析器：命中专属解析器，否则使用通用解析器
        mod = get_parser(str(res.url))
        log.info("用 %s 解析", mod.__name__)
        try:
            items = mod.parse_list(payload, source=str(res.url))
        except Exception as e:
            log.exception("解析异常: %s (%s)", url, e)
            items = []
            # 解析异常与传输层无关，跳过内容降级
            stats.parse_error += 1
            access_status = "parse_error"
            allow_downgrade = False

        # 内容层降级：200 但为空壳页面（列表由 JS 渲染）时，换下一层重取一次
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

        # 最终校验：缺少 url 或 title 的条目不入库
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
            # fetch_detail 模式下 content_hash 来自详情页正文，与列表页计算结果不一致；
            # 因此改为按 URL 是否存在且状态正常判重，避免重复抓取详情页
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
            # 详情页仅对新条目抓取；站点未实现 parse_detail 时跳过
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
                            # 抓取成功但无正文：标记 empty，下次重试
                            item_status = "empty"
                            item_err = item_err or "详情页没解析出正文"
                    except Exception as e:
                        # 详情页失败不得标记 normal，否则会被判重跳过
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
                # 单条入库失败不中断当前目标
                stats.bad += 1
                log.exception("入库失败 url=%s (%s)", link, e)

        commit_db(conn)

        # 翻页：优先使用站点自定义实现，否则尝试参数递增
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