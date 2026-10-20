from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import logging
import os

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_NAME = DATA_DIR / "softly.db"


# 本机配置从 .env 读取（.env 已被 .gitignore 忽略）
def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        # 真实环境变量优先于 .env
        os.environ.setdefault(key.strip(), value)


_load_env(BASE_DIR / ".env")

# 翻页上限：target.max_pages 可更小，但不能超过该硬上限
MAX_PAGES_HARD = 50
DEFAULT_MAX_PAGES = 1
DEFAULT_FETCH_DETAIL = False
DETAIL_REFRESH_DAYS = 7   # fetch_detail 模式下超过该天数重抓详情；0 表示不按时间重抓

# ---- 代理（Clash） ----
# 本机相关配置从 .env 读取，以下为缺省值
PROXY_HTTP   = os.environ.get("PROXY_HTTP", "http://127.0.0.1:7897")    # Clash Verge 混合端口
PROXY_SOCKS  = os.environ.get("PROXY_SOCKS", "socks5://127.0.0.1:7891") # 仅在需要 UDP/QUIC 时使用
CLASH_API    = os.environ.get("CLASH_API", "http://127.0.0.1:9097")     # external-controller 地址
CLASH_SECRET = os.environ.get("CLASH_SECRET", "")                       # 与「API 访问密钥」一致；为空则不携带认证头
CLASH_GROUP  = os.environ.get("CLASH_GROUP", "🚀 节点选择")              # 节点切换使用的策略组名
PROXY_PROBE_URL = "http://www.gstatic.com/generate_204"
PROXY_PROBE_TIMEOUT = 3.0
PROXY_FALLBACK_DIRECT = True              # 代理端口不可用但直连可出网时，非 DIRECT 站点改走直连

# ---- 降级链 ----
FETCH_TIERS = ("httpx", "curl_cffi", "httpcloak", "playwright")
BLOCKED_CODES = {403, 412, 429, 451, 503}
CHALLENGE_MARKERS = (
    "just a moment", "attention required", "cf-challenge", "cf_chl_",
    "checking your browser", "enable javascript and cookies",
    "请稍候", "安全验证", "人机验证", "访问异常",
)
CURL_CFFI_IMPERSONATE = "chrome120"       # 部分站点会拒绝最新指纹，使用 chrome120 兼容
HTTPCLOAK_PRESET = "chrome-latest"
HTTPCLOAK_HTTP_VERSION = "auto"           # HTTP/3 需要 UDP 中继，机场通常不保证，保持 auto 自动回落 h2

# ---- playwright ----
PLAYWRIGHT_HEADLESS = True
PLAYWRIGHT_WAIT = "domcontentloaded"       # 需要等 JS 的站可改 "networkidle"
PLAYWRIGHT_TIMEOUT_MS = 20000
PLAYWRIGHT_CHALLENGE_WAIT = 15   # 浏览器遇到 Cloudflare 挑战页时最多等待的秒数

# 需要人工完成验证的站点：复用浏览器会话，不做任何 JS 绕过
# HTTP_COOKIE 供 T0 使用；PLAYWRIGHT_STORAGE_STATE 供 T3 使用（浏览器导出的 cookies JSON）
HTTP_COOKIE = os.environ.get("HTTP_COOKIE", "")
HTTP_USER_AGENT = os.environ.get("HTTP_USER_AGENT", "")
PLAYWRIGHT_STORAGE_STATE = os.environ.get("PLAYWRIGHT_STORAGE_STATE", "")
PLAYWRIGHT_ROTATE_ON_BLOCK = False


class Route(StrEnum):
    AUTO   = "auto"       # 经 Clash 由规则分流（默认）
    DIRECT = "direct"     # 不设置代理，直连，绕过 Clash
    FORCE  = "force"      # 经 Clash，禁止回落 DIRECT


@dataclass
class Target:
    url: str = ''
    enable: bool = True
    max_pages: int = DEFAULT_MAX_PAGES
    fetch_detail: bool = DEFAULT_FETCH_DETAIL
    route: Route = Route.AUTO
    rotate_group: str | None = None  # 失败换节点时使用的策略组（缺省 CLASH_GROUP）；不绑定正常请求路由

    @property
    def active(self):
        return self.enable and self.url

@dataclass
class Log:
    LOG_NAME: str = 'xun'
    LOG_LEVEL: str = logging.INFO
    LOG_FORMAT:str = "%(asctime)s - [%(filename)s:%(lineno)d] - %(name)s - %(levelname)s - %(message)s"
    LOG_DATEFMT:str = "%Y-%m-%d %H:%M:%S"
    LOG_FILE :str = "app.log"


TARGETS:dict[str, Target] ={
    "books":Target("https://books.toscrape.com/"),
    # 校园网直连更稳定；经代理出口可能触发校园风控
    "ahszu":Target("https://www.ahszu.edu.cn/xxxw/tzgg.htm", route=Route.DIRECT),
    "xsyu":Target("https://www.xsyu.edu.cn/xwzx/tzgg.htm", route=Route.DIRECT),
    "30aitool.com":Target("https://www.30aitool.com/wp-json/wp/v2/tool"),
}
# skills.qiaomu.ai：按分类抓技能列表（详情页不抓）
QIAOMU_CATEGORIES = (
    "install-react",
    "install-nextjs",
    "install-design",
    "install-mobile",
    "install-agent-workflows",
    "install-databases",
    "install-testing",
    "install-marketing",
    "community-tools",
    "community-business",
    "community-development",
    "community-testing-security",
    "community-data-ai",
    "community-devops",
    "community-debugging",
    "community-sales-marketing",
    "community-system-admin",
    "community-code-quality",
    "community-llm-ai",
    "community-git-workflows",
    "community-productivity-tools",
    "community-documents",
    "community-architecture-patterns",
    "community-project-management",
    "community-testing",
    "community-finance-investment",
    "qiaomu-agent",
    "qiaomu-dev",
    "qiaomu-content",
    "qiaomu-visual",
    "qiaomu-media",
    "qiaomu-research",
    "qiaomu-productivity",
)

for _slug in QIAOMU_CATEGORIES:
    TARGETS[f"qiaomu-{_slug}"] = Target(f"https://skills.qiaomu.ai/categories/{_slug}/")
del _slug
# 随机 UA 与默认请求头
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36 Edg/153.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:156.0) Gecko/20100101 Firefox/156.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:156.0) Gecko/20100101 Firefox/156.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:156.0) Gecko/20100101 Firefox/156.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.6 Safari/605.1.15",
    "Mozilla/5.0 (Linux; Android 16; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 26_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.6 Mobile/15E148 Safari/604.1",
]

DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}