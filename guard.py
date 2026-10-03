import re

DATA_KEYS = {"data", "result", "results", "rows", "list", "items", "records", "content"}
MSG_KEYS  = {"message", "msg", "error", "errmsg", "error_message", "reason", "提示", "消息"}
CODE_KEYS = {"code", "errno", "errcode", "status", "retcode", "ret", "state"}
OK_CODES  = {None, 0, "0", 200, "200", True, "true", "ok", "success"}


def _norm(k: str) -> str:
    return re.sub(r"[_\-]", "", k.lower())


def _find(d, keys):
    for k, v in d.items():
        if _norm(k) in keys:
            return k, v
    return None, None


def check(raw:dict) -> str | None:

    if not isinstance(raw, dict):
        return None

    #有数据（且非空）→ 成功，不看 code
    for k, v in raw.items():
        if _norm(k) in DATA_KEYS and v not in (None, "", [], {}):
            return None

    # 有 code → 由它定（成功值放行，否则报）
    ck, code = _find(raw, CODE_KEYS)
    if ck is not None:
        if code in OK_CODES:
            return None
        mk, msg = _find(raw, MSG_KEYS)
        if isinstance(msg, str) and msg.strip():
            return f"{ck}={code} {msg.strip()}"
        return f"{ck}={code}"

    # 无 code，有 message → 当错误
    mk, msg = _find(raw, MSG_KEYS)
    if isinstance(msg, str) and msg.strip():
        return f"{mk}: {msg.strip()}"

    return "空响应"
