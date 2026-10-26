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
        records.append(
            {
                "url": urljoin(source, href),
                "title": title,
                "summary": summary or None,
            }
        )
    return records


def _is_challenge(html: str) -> bool:
    head = (html or "")[:4000].lower()
    return any(marker in head for marker in ("just a moment", "cf-chl", "请稍候", "正在进行安全验证"))


def parse_detail(html: str) -> str:
    # 仅解析服务端已渲染的内容；挑战页直接返回约定值，不做任何绕过
    if _is_challenge(html):
        return "blocked"
    soup = BeautifulSoup(html, "lxml")
    parts = []

    title = _text(soup.select_one('[class*="HeroTitle"]')) or _text(soup.select_one("h1"))
    subtitle = _text(soup.select_one('[class*="HeroSubTitle"]'))
    if title:
        parts.append(title)
    if subtitle and subtitle != title:
        parts.append(subtitle)

    # 套餐 / 时长：每个 sku 块前有标题，item 内含 name、price、折扣
    for box in soup.select('[class*="SkuBox"]'):
        heading = _text(box.select_one('[class*="StyledTitle"]'))
        if heading:
            parts.append(heading + "：")
        for item in box.select(".sku-item"):
            name = _text(item.select_one(".name"))
            price = _text(item.select_one(".price"))
            promo = _text(item.select_one(".promotion-discount"))
            line = " ".join(part for part in (name, price, promo) if part)
            if line:
                parts.append(line)

    # 商品详情 / 常见问题（SSR 内容）
    for section in soup.select("section#product-details, section#faq"):
        heading = _text(section.select_one("h2"))
        if heading:
            parts.append(heading + "：")
        body = section.select_one('[class*="DetailContent"]') or section
        text = body.get_text("\n", strip=True)
        if text:
            parts.append(text)

    seen = set()
    lines = []
    for part in parts:
        for line in part.splitlines():
            line = line.strip()
            if line and line not in seen:
                seen.add(line)
                lines.append(line)
    return "\n".join(lines)
