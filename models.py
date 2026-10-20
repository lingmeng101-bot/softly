from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Article:
    url: str
    title: str
    content: str = ""
    source: str | None = None
    summary: str | None = None
    publish_time: str | None = None
    dedup_key: str = ""
    content_hash: str = ""
    fetched_at: str = field(
        # 默认按当前时间生成
        default_factory=lambda: datetime.now().isoformat(timespec="seconds"),
        # 不参与相等性比较
        compare=False,
    )
