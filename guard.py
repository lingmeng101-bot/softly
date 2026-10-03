# -*- coding: utf-8 -*-
"""响应体检：HTTP 200 不代表成功。

200 有三种骗法：
    1. 200 + HTML 错误页/验证页          → 归 sniff 管（看 content-type 和首字符）
    2. 200 + JSON，但 body 里 code 是错的 → 归这里管
    3. 200 + 说是 json，body 其实是 HTML  → 归调用方管（res.json() 要包 try）

用法：
    err = check(解析出来的 JSON 对象)
        err is None  → 看起来有真数据，继续走
        err 是字符串 → 失败原因，直接进日志
"""

import re

DATA_KEYS = {"data", "result", "results", "rows", "list", "items", "records", "content"}
MSG_KEYS  = {"message", "msg", "error", "errmsg", "error_message", "reason", "提示", "消息"}
CODE_KEYS = {"code", "errno", "errcode", "status", "retcode", "ret", "state"}

OK_STRINGS = {"0", "200", "ok", "success", "true"}
OK_NUMBERS = {0, 200}

# 裸字符串要够长才算"数据" —— 防止 {"code":404,"content":"页面不存在"} 这种顶替真数据
MIN_STR = 20


# 键统一归一后再入集合，否则写 error_message 这种永远匹配不上（会被归一成 errormessage）
def _norm(k: str) -> str:
    return re.sub(r"[_\-]", "", str(k).lower())


DATA_KEYS = {_norm(k) for k in DATA_KEYS}
MSG_KEYS  = {_norm(k) for k in MSG_KEYS}
CODE_KEYS = {_norm(k) for k in CODE_KEYS}


def _find(d, keys):
    for k, v in d.items():
        if _norm(k) in keys:
            return k, v
    return None, None


def _has_data(v) -> bool:
    """递归判"这里头有没有真东西"。

    空壳全部不算：None / "" / [] / {} / False
    {"list": []} 也不算 —— 这正是 code=500 + data={"list":[]} 那种假成功。
    """
    if v is None:
        return False
    if isinstance(v, bool):
        return v                      # False 不当数据
    if isinstance(v, str):
        return len(v.strip()) >= MIN_STR
    if isinstance(v, dict):
        return any(_has_data(x) for x in v.values())
    if isinstance(v, (list, tuple, set)):
        return any(_has_data(x) for x in v)
    return True                       # 非空数字之类


def _code_ok(code) -> bool:
    """判成功值。

    bool 必须单独处理：Python 里 True == 1、False == 0，
    一起塞进集合判成员会串味（code=1 被 True 放行、status=false 被 0 放行）。
    """
    if code is None:
        return True
    if isinstance(code, bool):
        return code is True
    if isinstance(code, (int, float)):
        return code in OK_NUMBERS
    if isinstance(code, str):
        return code.strip().lower() in OK_STRINGS
    return False


def check(raw) -> str | None:
    """None = 看起来有真数据；字符串 = 失败原因。"""

    if not isinstance(raw, dict):
        return None

    # 1) 有真数据就放行（有些接口 code 乱写，但数据是好的）
    for k, v in raw.items():
        if _norm(k) in DATA_KEYS and _has_data(v):
            return None

    # 2) 有 code → 由 code 定
    ck, code = _find(raw, CODE_KEYS)
    if ck is not None:
        if _code_ok(code):
            return None
        mk, msg = _find(raw, MSG_KEYS)
        if isinstance(msg, str) and msg.strip():
            return f"{ck}={code!r} {msg.strip()}"
        return f"{ck}={code!r}"

    # 3) 没 code，只有 message → 当错误
    mk, msg = _find(raw, MSG_KEYS)
    if isinstance(msg, str) and msg.strip():
        return f"{mk}: {msg.strip()}"

    return "空响应"
