
from urllib.parse import urljoin

from bs4 import BeautifulSoup

BLOCKED = "blocked"          # 详情页被拦的标记

LIST_ITEM = "ul.ej_list li"
NEXT_LINK = "span.p_next.p_fun a"

CONTENT_SELECTORS = (
    "div.v_news_content, div#vsb_content, div.content, article, "
    "div.article-content, div.news_content, div.text-content, "
    "div#content, div.main-content, div.article, div.detail-content"
)


def _text(node) -> str:
    return node.get_text(strip=True) if node else ""


def parse_list(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    records = []

    for item in soup.select(LIST_ITEM):
        day = _text(item.select_one("p.date_list.fr"))

        link_tag = item.select_one("a[href*='/info/']")
        if not link_tag:
            continue
        href = link_tag.get("href", "")
        if not href:
            continue

        # 标题优先取 title 属性，没有就取链接文本
        title = (link_tag.get("title") or "").strip() or _text(link_tag)
        if not title:
            continue

        records.append({
            "day": day,
            "title": title,
            "url": urljoin(base_url, href),      # ← 铁律：每条必须有 url
        })

    return records


def parse_detail(html: str) -> str:

    soup = BeautifulSoup(html, "lxml")
    title = _text(soup.select_one("title"))

    if (len(html) < 2000 and ("无权访问" in html or "系统提示" in html)) or title == "系统提示":
        return BLOCKED

    content = soup.select_one(CONTENT_SELECTORS)
    if not content:
        return ""

    for tag in content.find_all(["script", "style", "img", "iframe"]):
        tag.decompose()

    return content.get_text(strip=True)


def next_page_url(html: str, base_url: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    next_tag = soup.select_one(NEXT_LINK)
    if not next_tag or not next_tag.get("href"):
        return None
    return urljoin(base_url, next_tag.get("href"))
