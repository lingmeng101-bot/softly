from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import logging

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_NAME = DATA_DIR / "softly.db"

# 翻页上限：target.max_pages 可以再小，但不能超过这个硬上限
MAX_PAGES_HARD = 50
DEFAULT_MAX_PAGES = 1
DEFAULT_FETCH_DETAIL = False

# ================== 代理（Clash 内核）==================
PROXY_HTTP   = "http://127.0.0.1:7897"    # Clash Verge 混合端口（设置→端口设置），给 httpx / curl_cffi / playwright
PROXY_SOCKS  = "socks5://127.0.0.1:7891"  # 只有需要 UDP/QUIC 时才用
CLASH_API    = "http://127.0.0.1:9090"    # external-controller，可选
CLASH_SECRET = ""                          # 没设就留空
CLASH_GROUP  = "PROXY"                     # 换节点用的策略组名
PROXY_PROBE_URL = "http://www.gstatic.com/generate_204"
PROXY_PROBE_TIMEOUT = 3.0
PROXY_FALLBACK_DIRECT = True              # 代理口不通但直连可出网（TUN/全局模式）时，非 DIRECT 站改走直连

# ================== 降级链 ==================
FETCH_TIERS = ("httpx", "curl_cffi", "httpcloak", "playwright")
BLOCKED_CODES = {403, 412, 429, 451, 503}
CHALLENGE_MARKERS = (
    "just a moment", "attention required", "cf-challenge", "cf_chl_",
    "checking your browser", "enable javascript and cookies",
    "请稍候", "安全验证", "人机验证", "访问异常",
)
CURL_CFFI_IMPERSONATE = "chrome120"       # 本机 0.16.3 的 "chrome" 在部分站会被断连，chrome120 实测正常
HTTPCLOAK_PRESET = "chrome-latest"
HTTPCLOAK_HTTP_VERSION = "auto"           # 不是 "h3"！机场多数不保证 UDP 中继

# ================== playwright ==================
PLAYWRIGHT_HEADLESS = True
PLAYWRIGHT_WAIT = "domcontentloaded"       # 需要等 JS 的站可改 "networkidle"
PLAYWRIGHT_TIMEOUT_MS = 20000
PLAYWRIGHT_ROTATE_ON_BLOCK = False


class Route(StrEnum):
    AUTO   = "auto"       # 走 Clash，由规则分流（默认）
    DIRECT = "direct"     # 根本不设 proxy，直连，绕开 Clash
    FORCE  = "force"      # 走 Clash，且必须在 PROXY group 里（不回落 DIRECT）


@dataclass
class Target:
    url: str = ''
    enable: bool = True
    max_pages: int = DEFAULT_MAX_PAGES
    fetch_detail: bool = DEFAULT_FETCH_DETAIL
    route: Route = Route.AUTO
    proxy_group: str | None = None  # 配合 FORCE / 换节点

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
    # 国内校园网走机场反而慢，出口 IP 还容易被校方风控设备拦，所以强制直连
    "ahszu":Target("https://www.ahszu.edu.cn/xxxw/tzgg.htm", route=Route.DIRECT),
    "xsyu":Target("https://www.xsyu.edu.cn/xwzx/tzgg.htm", route=Route.DIRECT),
    "30aitool.com":Target("https://www.30aitool.com/wp-json/wp/v2/tool"),
}
#随机UA和Headers
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