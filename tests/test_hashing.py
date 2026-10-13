from hashing import content_hash, dedup_key, norm_url


def test_norm_url_strips_noise():
    assert norm_url("http://www.Example.com/a/?utm_source=x&b=2&a=1") == "https://example.com/a?a=1&b=2"


def test_norm_url_keeps_path():
    assert norm_url("https://a.com") == "https://a.com/"


def test_dedup_key_ignores_tracking():
    assert dedup_key("https://a.com/p?utm_source=x") == dedup_key("https://a.com/p")


def test_dedup_key_differs_on_path():
    assert dedup_key("https://a.com/p1") != dedup_key("https://a.com/p2")


def test_content_hash_ignores_whitespace():
    assert content_hash("标 题", "正文\n内容") == content_hash("标题", "正文内容")
