import asyncio
import contextlib
import logging

import config
from config import Log
from crawler.fetcher import TIER_HITS, close_all, log_tiers
from log import setup_logger
from pipeline import CrawlStats, crawl_and_save
from storage import init_db

log = logging.getLogger(Log.LOG_NAME)


def _stats_line(counter: dict[str, int]) -> str:
    return " ".join(f"{tier}={counter.get(tier, 0)}" for tier in config.FETCH_TIERS)


def _log_target(name, target) -> None:
    log.info("=== %s  %s route=%s fetch_detail=%s", name, target.url, target.route, target.fetch_detail)


async def _crawl_sequential(targets, conn):
    total = CrawlStats()
    failed: list[str] = []
    for name, target in targets:
        _log_target(name, target)
        try:
            total += await crawl_and_save(target, conn)
        except Exception:
            # 单站失败不终止整批；已提交的页数据保留，未提交的回滚
            failed.append(name)
            log.exception("目标失败，继续下一个: %s", name)
            with contextlib.suppress(Exception):
                conn.rollback()
    return total, failed


async def _crawl_isolated(name, target):
    # 并发模式下每个目标使用独立连接，避免不同目标的事务交错
    conn = init_db()
    stats = CrawlStats()
    ok = True
    _log_target(name, target)
    try:
        stats = await crawl_and_save(target, conn)
    except Exception:
        ok = False
        log.exception("目标失败，继续下一个: %s", name)
        with contextlib.suppress(Exception):
            conn.rollback()
    finally:
        conn.close()
    return name, stats, ok


async def _crawl_concurrent(targets):
    sem = asyncio.Semaphore(config.MAX_CONCURRENT_TARGETS)

    async def worker(name, target):
        async with sem:
            return await _crawl_isolated(name, target)

    total = CrawlStats()
    failed: list[str] = []
    for name, stats, ok in await asyncio.gather(*(worker(n, t) for n, t in targets)):
        total += stats
        if not ok:
            failed.append(name)
    return total, failed


async def main() -> None:
    setup_logger()
    log_tiers()

    targets = []
    for name, target in config.TARGETS.items():
        if not target.active:
            log.info("跳过（未启用）: %s", name)
            continue
        targets.append((name, target))

    total = CrawlStats()
    failed: list[str] = []
    try:
        if config.MAX_CONCURRENT_TARGETS <= 1:
            conn = init_db()
            try:
                total, failed = await _crawl_sequential(targets, conn)
            finally:
                conn.close()
        else:
            total, failed = await _crawl_concurrent(targets)
    finally:
        await close_all()

    log.info(
        "全部跑完：新增 %d，拦截 %d（已入库），跳过 %d，异常 %d，解析错 %d",
        total.added,
        total.blocked,
        total.skipped,
        total.bad,
        total.parse_error,
    )
    log.info("[stats] %s", _stats_line(TIER_HITS))
    if failed:
        log.warning("失败目标 %d/%d: %s", len(failed), len(targets), ", ".join(failed))


if __name__ == "__main__":
    asyncio.run(main())
