import logging
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from config import Log

log = logging.getLogger(Log.LOG_NAME)

CARD = 'a[class*="ProductCard"]'
TITLE = '[class*="ProductTitle"]'
PRICE = '[class*="PriceMain"]'
OLD_PRICE = '[class*="PriceOld"]'
DISCOUNT = '[class*="DiscountTag"]'


def _text(node) -> str:
    return node.get_text(" ", strip=True) if node else ""


def parse_list(html: str, source: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    records = []
    for card in soup.select(CARD):
        href = card.get("href", "")
        title = _text(card.select_one(TITLE))
        if not href or not title:
            continue
        price = _text(card.select_one(PRICE))
        old_price = _text(card.select_one(OLD_PRICE))
        discount = _text(card.select_one(DISCOUNT))
        summary = " | ".join(part for part in (price, old_price, discount) if part)
        records.append({
            "url": urljoin(source, href),
            "title": title,
            "summary": summary or None,
        })
    return records
