from config import Target


def test_active():
    assert Target("https://a.com").active
    assert not Target("").active
    assert not Target("https://a.com", enable=False).active


def test_default_max_pages():
    assert Target("https://a.com").max_pages == 1

def test_route_defaults():
    from config import Route, TARGETS
    assert Target("https://a.com").route == Route.AUTO
    assert TARGETS["books"].route == Route.AUTO
    assert TARGETS["ahszu"].route == Route.DIRECT
    assert TARGETS["xsyu"].route == Route.DIRECT

def test_default_fetch_detail():
    assert Target("https://a.com").fetch_detail is False

def test_proxy_fallback_flag():
    import config
    assert config.PROXY_FALLBACK_DIRECT is True
