import asyncio
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


async def main() -> None:
    setup_logger()
    log_tiers()
    conn = init_db()
    total = CrawlStats()
    failed: list[str] = []
    try:
        for name, target in config.TARGETS.items():
            if not target.active:
                log.info("跳过（未启用）: %s", name)
                continue
            log.info("=== %s  %s route=%s fetch_detail=%s",
                     name, target.url, target.route, target.fetch_detail)
            try:
                total += await crawl_and_save(target, conn)
            except Exception:
                # 单站失败不终止整批；已提交的页数据保留，未提交的回滚
                failed.append(name)
                log.exception("目标失败，继续下一个: %s", name)
                try:
                    conn.rollback()
                except Exception:
                    pass
    finally:
        conn.close()
        await close_all()
    log.info("全部跑完：新增 %d，拦截 %d（已入库），跳过 %d，异常 %d，解析错 %d",
             total.added, total.blocked, total.skipped, total.bad, total.parse_error)
    log.info("[stats] %s", _stats_line(TIER_HITS))
    if failed:
        log.warning("失败目标 %d/%d: %s", len(failed), len(config.TARGETS), ", ".join(failed))

if __name__ == "__main__":
    asyncio.run(main())
