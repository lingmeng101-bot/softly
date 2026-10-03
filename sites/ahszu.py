

from urllib.parse import urljoin

from bs4 import BeautifulSoup

BLOCKED = "blocked"          # 详情页被拦的标记（先用字符串，等第三个站再统一成状态字段）

LIST_ITEM = 'li[id^="line_u12_"]'
NEXT_LINK = "span.p_next.p_fun a"

# 正文容器：不同学校的 CMS 模板不一样，按可能性从高到低排
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
        # 日期在这个站是拆成两块的：p 是年月，span 是日
        month = _text(item.select_one("div.text-ldata p"))
        day = _text(item.select_one("div.text-ldata span"))
        full_day = f"{month}-{day}" if month and day else ""

        title = _text(item.select_one("div.text-linfo h3"))

        summary = _text(item.select_one("div.text-linfo p"))
        if len(summary) > 30:
            summary = summary[:30] + "..."

        link_tag = item.select_one("a[href]")
        href = link_tag.get("href", "") if link_tag else ""

        if not title or not href:
            continue

        records.append({
            "day": full_day,
            "title": title,
            "summary": summary,
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
