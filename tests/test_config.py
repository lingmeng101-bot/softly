from config import Target


def test_active():
    assert Target("https://a.com").active
    assert not Target("").active
    assert not Target("https://a.com", enable=False).active
