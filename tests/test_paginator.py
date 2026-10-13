from paginator import find_param, next_page_url


def test_find_param():
    assert find_param("https://a.com/list?page=2") == ("page", "page", "2")


def test_next_page():
    assert next_page_url("https://a.com/list?page=2") == "https://a.com/list?page=3"


def test_next_page_keeps_other_params():
    assert next_page_url("https://a.com/list?p=1&x=y") == "https://a.com/list?p=2&x=y"


def test_offset_not_guessed():
    assert next_page_url("https://a.com/list?offset=20") is None


def test_no_param():
    assert next_page_url("https://a.com/list") is None


def test_non_digit():
    assert next_page_url("https://a.com/list?page=x") is None
