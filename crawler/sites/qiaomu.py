import logging
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from config import Log

log = logging.getLogger(Log.LOG_NAME)

SKILL_LINK = '.seo-fallback a[href^="/skills/"]'


def _text(node) -> str:
    return node.get_text(" ", strip=True) if node else ""


def _is_challenge(html: str) -> bool:
    head = (html or "")[:4000].lower()
    return any(marker in head for marker in ("just a moment", "cf-chl", "请稍候", "正在进行安全验证"))


def parse_list(html: str, source: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    records = []
    seen = set()
    for link in soup.select(SKILL_LINK):
        href = link.get("href", "")
        title = link.get_text(strip=True)
        if not href or not title:
            continue
        url = urljoin(source, href)
        if url in seen:
            continue
        seen.add(url)
        # 静态回退列表的 li 文本是「名称：描述」
        summary = ""
        li = link.find_parent("li")
        if li:
            text = li.get_text(" ", strip=True)
            if text.startswith(title):
                summary = text[len(title):].lstrip("：: ").strip()
        records.append({"url": url, "title": title, "summary": summary or None})
    return records


def parse_detail(html: str) -> str:
    if _is_challenge(html):
        return "blocked"
    soup = BeautifulSoup(html, "lxml")
    parts = []
    for selector in ("h1.detail-title", "p.detail-value", "p.detail-fit"):
        text = _text(soup.select_one(selector))
        if text:
            parts.append(text)
    command = _text(soup.select_one(".detail-command code"))
    if command:
        parts.append("安装命令：" + command)
    for section in soup.select(".detail-main section"):
        heading = _text(section.select_one("h2.detail-section-title"))
        if heading:
            parts.append(heading + "：")
        for node in section.select("p, ul.detail-list li"):
            text = _text(node)
            if text:
                parts.append(("- " + text) if node.name == "li" else text)
    for panel in soup.select(".detail-side-panel"):
        text = panel.get_text(" ", strip=True)
        if text:
            parts.append(text)
    seen = set()
    lines = []
    for part in parts:
        line = part.strip()
        if line and line not in seen:
            seen.add(line)
            lines.append(line)
    return "\n".join(lines)
