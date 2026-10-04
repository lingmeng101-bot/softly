import httpx
from charset_normalizer import detect
import random
import logging
from tenacity import (
    retry,
    stop_after_attempt,
    wait_random_exponential,
    retry_if_exception,
    before_sleep_log
)
from config import USER_AGENTS,DEFAULT_HEADERS,Log

log = logging.getLogger(Log.LOG_NAME)

def autodetect(content:bytes)->str :
    result=detect(content)
    if result.get("confidence",0) < 0.5 :
        return "utf-8"
    return result.get('encoding') or 'utf-8'


#headers参数配置
def build_header(headers:dict|None =None) -> dict[str, str]:
    h=dict(DEFAULT_HEADERS)
    h['User-Agent'] = random.choice(USER_AGENTS)
    if headers:
        h.update(headers)
    return h

#follow处理3xx响应
_client = httpx.Client(default_encoding=autodetect, timeout=httpx.Timeout(connect=5, read=10, write=10, pool=5), follow_redirects=True)

def _should_retry(exc: BaseException) -> bool:
    if isinstance (exc,httpx.RequestError):
        return True
    if isinstance (exc,httpx.HTTPStatusError):
        code=exc.response.status_code
        return code == 429 or 500<=code<600
    return False

@retry(
    stop=stop_after_attempt(4),
    wait=wait_random_exponential(multiplier=1, max=10),
    before_sleep=before_sleep_log(log, logging.WARNING),
    retry=retry_if_exception(_should_retry),
    reraise=True,
)
def fetch(url: str, headers: dict[str, str] | None = None) -> httpx.Response:
    log.info("开始抓取 url=%s", url)
    res=_client.get(url,headers=build_header(headers))
    res.raise_for_status()
    return res


