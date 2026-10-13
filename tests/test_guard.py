from crawler.guard import check


def test_empty():
    assert check({}) == "空响应"


def test_data_ok():
    assert check({"data": [1, 2, 3]}) is None


def test_code_ok():
    assert check({"code": 0, "msg": "ok"}) is None


def test_code_error():
    assert check({"code": 500, "msg": "服务器错误"}) == "code=500 服务器错误"


def test_message_only():
    assert check({"message": "无权访问"}) == "message: 无权访问"


def test_not_dict():
    assert check("x") is None
