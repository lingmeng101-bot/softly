from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Article:
    url: str
    title: str
    content: str = ""
    source: str | None=None
    author: str | None = None
    publish_time: str | None = None
    dedup_key: str = ""
    content_hash: str = ""
    fetched_at: str = field(
        #自动生成
        default_factory=lambda: datetime.now().isoformat(timespec="seconds"),
        #跳过抓取时间对比
        compare=False,
    )