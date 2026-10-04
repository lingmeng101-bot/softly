from types import ModuleType
from urllib.parse import urlparse
from . import ahszu, aitool30, books, generic, xsyu

PARSERS: dict[str, ModuleType] = {
    "ahszu.edu.cn": ahszu,
    "xsyu.edu.cn": xsyu,
    "books.toscrape.com": books,
    "30aitool.com": aitool30,
}


def host_of(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    # 假设URL 里带 user:pass@
    netloc = netloc.split("@")[-1]
    # 去掉端口
    host = netloc.split(":")[0]
    return host[4:] if host.startswith("www.") else host


def get_parser(url: str) -> ModuleType:

    return PARSERS.get(host_of(url)) or generic
