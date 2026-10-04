import logging

import config
from log import setup_logger
from pipeline import crawl_and_save
from storage import init_db

log = logging.getLogger(config.Log.LOG_NAME)

def main() -> None:
    setup_logger()
    conn = init_db()
    total_added = 0
    total_bad = 0
    try:
        for name, target in config.TARGETS.items():
            if not target.active:
                log.info("跳过（未启用）: %s", name)
                continue
            log.info("=== %s  %s", name, target.url)
            added, bad = crawl_and_save(target, conn)
            total_added += added
            total_bad += bad
    finally:
        conn.close()
    log.info("全部跑完：新增 %d 条，异常 %d 条", total_added, total_bad)

if __name__ == "__main__":
    main()
