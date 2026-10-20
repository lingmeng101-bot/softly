import logging

import config
from log import setup_logger
from pipeline import CrawlStats, crawl_and_save
from storage import init_db

log = logging.getLogger(config.Log.LOG_NAME)

def main() -> None:
    setup_logger()
    conn = init_db()
    total = CrawlStats()
    try:
        for name, target in config.TARGETS.items():
            if not target.active:
                log.info("跳过（未启用）: %s", name)
                continue
            log.info("=== %s  %s", name, target.url)
            total += crawl_and_save(target, conn)
    finally:
        conn.close()
    log.info("全部跑完：新增 %d，跳过 %d，异常 %d，解析错 %d",
             total.added, total.skipped, total.bad, total.parse_error)

if __name__ == "__main__":
    main()
