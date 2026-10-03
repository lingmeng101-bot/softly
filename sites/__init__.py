from types import ModuleType
from urllib.parse import urlparse
from . import ahszu, books, xsyu

PARSERS: dict[str, ModuleType] = {
    "ahszu.edu.cn": ahszu,
    "xsyu.edu.cn": xsyu,
    "books.toscrape.com": books,
}


def host_of(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    # 假设URL 里带 user:pass@
    netloc = netloc.split("@")[-1]
    # 去掉端口
    host = netloc.split(":")[0]
    return host[4:] if host.startswith("www.") else host


def get_parser(url: str) -> ModuleType | None:
    return PARSERS.get(host_of(url))
