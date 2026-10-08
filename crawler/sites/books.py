

import logging
from urllib.parse import urljoin

from bs4 import BeautifulSoup

import config

log = logging.getLogger(config.Log.LOG_NAME)     # 新项目里 Log 是类，要 config.Log.LOG_NAME

RATING_MAP = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}


def parse_list(html: str, source: str) -> list[dict]:

    try:
        soup = BeautifulSoup(html, "lxml")
        items = soup.select("article.product_pod")
        if not items:
            log.warning("没匹配到 article.product_pod，页面结构可能变了：%s", source)
            return []

        records = []
        for item in items:
            link_tag = item.select_one("div.image_container a")
            if not link_tag:
                link_tag = item.select_one("a[href]")

            img_tag = item.select_one("div.image_container a img")
            if not img_tag:
                img_tag = item.select_one("img[src*='.jpg']")

            name_tag = item.select_one("h3 a")
            name = name_tag.get("title") if name_tag else ""
            if not name:
                name = img_tag.get("alt") if img_tag else ""

            price_tag = item.select_one("p.price_color")
            price = price_tag.get_text(strip=True) if price_tag else ""

            stock_tag = item.select_one("p.instock.availability")
            stock = stock_tag.get_text(strip=True) if stock_tag else ""

            rating_tag = item.select_one("p.star-rating")
            classes = rating_tag.get("class", []) if rating_tag else []
            star = RATING_MAP.get(classes[1], 0) if len(classes) > 1 else 0

            href = link_tag.get("href", "") if link_tag else ""
            if not name or not href:
                continue

            records.append({
                "title": name,
                "price": price,
                "stock": stock,
                "star": star,
                "img": urljoin(source, img_tag.get("src", "")) if img_tag else "",
                "url": urljoin(source, href),      # ← 铁律：每条必须有 url
            })
        return records
    except Exception:
        log.exception("解析异常 source=%s", source)
        raise


def next_page_url(html: str, source: str) -> str | None:

    soup = BeautifulSoup(html, "lxml")
    next_tag = soup.select_one("li.next a")
    if not next_tag or not next_tag.get("href"):
        return None
    return urljoin(source, next_tag.get("href"))
