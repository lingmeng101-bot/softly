from config import Target


def test_active():
    assert Target("https://a.com").active
    assert not Target("").active
    assert not Target("https://a.com", enable=False).active


def test_default_max_pages():
    assert Target("https://a.com").max_pages == 1


def test_route_defaults():
    from config import TARGETS, Route

    assert Target("https://a.com").route == Route.AUTO
    assert TARGETS["books"].route == Route.AUTO
    assert TARGETS["ahszu"].route == Route.DIRECT
    assert TARGETS["xsyu"].route == Route.DIRECT


def test_default_fetch_detail():
    assert Target("https://a.com").fetch_detail is False


def test_load_env_file(tmp_path, monkeypatch):
    import os

    import config

    p = tmp_path / ".env"
    p.write_text("# 注释\n\nFOO_X=bar\nQUOTED_X='a b'\n", encoding="utf-8")
    config._load_env(p)
    try:
        assert os.environ["FOO_X"] == "bar"
        assert os.environ["QUOTED_X"] == "a b"
    finally:
        os.environ.pop("FOO_X", None)
        os.environ.pop("QUOTED_X", None)


def test_load_env_does_not_override_real_env(tmp_path, monkeypatch):
    import os

    import config

    monkeypatch.setenv("KEEP_X", "real")
    p = tmp_path / ".env"
    p.write_text("KEEP_X=from-file\n", encoding="utf-8")
    config._load_env(p)
    assert os.environ["KEEP_X"] == "real"
