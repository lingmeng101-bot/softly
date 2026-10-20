import logging

import config
from config import Log
from crawler.fetcher import TIER_HITS, close_all, log_tiers, network_alive, proxy_alive
from log import setup_logger
from pipeline import CrawlStats, crawl_and_save
from storage import init_db

log = logging.getLogger(Log.LOG_NAME)


def _stats_line(counter: dict[str, int]) -> str:
    return " ".join(f"{tier}={counter.get(tier, 0)}" for tier in config.FETCH_TIERS)


def main() -> None:
    setup_logger()
    log_tiers()
    conn = init_db()
    total = CrawlStats()
    alive = proxy_alive()
    try:
        for name, target in config.TARGETS.items():
            if not target.active:
                log.info("跳过（未启用）: %s", name)
                continue
            route = target.route
            if not alive and route != config.Route.DIRECT:
                if route == config.Route.FORCE:
                    log.error("代理不可用，FORCE 站点不回落直连，跳过: %s", name)
                    continue
                if not config.PROXY_FALLBACK_DIRECT or not network_alive(target.url):
                    log.error("代理不可用且直连不可达，跳过: %s", name)
                    continue
                #逐个目标站探测后回落直连，避免用公网探针误判
                route = config.Route.DIRECT
                log.warning("代理不可用，直连可达，改走直连: %s", name)
            log.info("=== %s  %s route=%s fetch_detail=%s",
                     name, target.url, route, target.fetch_detail)
            total += crawl_and_save(target, conn, route=route)
    finally:
        conn.close()
        close_all()
    log.info("全部跑完：新增 %d，拦截 %d（已入库），跳过 %d，异常 %d，解析错 %d",
             total.added, total.blocked, total.skipped, total.bad, total.parse_error)
    log.info("[stats] %s", _stats_line(TIER_HITS))

if __name__ == "__main__":
    main()