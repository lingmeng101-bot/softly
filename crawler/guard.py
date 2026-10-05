import re

DATA_KEYS = {"data", "result", "results", "rows", "list", "items", "records", "content"}
MSG_KEYS  = {"message", "msg", "error", "errmsg", "error_message", "reason", "提示", "消息"}
CODE_KEYS = {"code", "errno", "errcode", "status", "retcode", "ret", "state"}

OK_STRINGS = {"0", "200", "ok", "success", "true"}
OK_NUMBERS = {0, 200}


MIN_STR = 20



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

    if not isinstance(raw, dict):
        return None

    
    for k, v in raw.items():
        if _norm(k) in DATA_KEYS and _has_data(v):
            return None

    
    ck, code = _find(raw, CODE_KEYS)
    if ck is not None:
        if _code_ok(code):
            return None
        mk, msg = _find(raw, MSG_KEYS)
        if isinstance(msg, str) and msg.strip():
            return f"{ck}={code!r} {msg.strip()}"
        return f"{ck}={code!r}"

    
    mk, msg = _find(raw, MSG_KEYS)
    if isinstance(msg, str) and msg.strip():
        return f"{mk}: {msg.strip()}"

    return "空响应"
