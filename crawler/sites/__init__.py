from types import ModuleType
from urllib.parse import urlparse

from . import ahszu, aitool30, books, ezsub, generic, qiaomu, xsyu

PARSERS: dict[str, ModuleType] = {
    "ahszu.edu.cn": ahszu,
    "xsyu.edu.cn": xsyu,
    "books.toscrape.com": books,
    "30aitool.com": aitool30,
    "ezsubxiaoali.ezsub.com": ezsub,
    "skills.qiaomu.ai": qiaomu,
}


def host_of(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    # 兼容 user:pass@ 形式
    netloc = netloc.split("@")[-1]
    # 去除端口
    host = netloc.split(":")[0]
    return host[4:] if host.startswith("www.") else host


def get_parser(url: str) -> ModuleType:

    return PARSERS.get(host_of(url)) or generic
