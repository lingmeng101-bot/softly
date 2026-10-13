from crawler.sites import generic, get_parser, host_of


def test_host_of():
    assert host_of("https://www.xsyu.edu.cn/a/b?c=1") == "xsyu.edu.cn"


def test_host_of_with_port():
    assert host_of("http://a.com:8080/x") == "a.com"


def test_get_parser_hit():
    assert get_parser("https://www.xsyu.edu.cn/xwzx/tzgg.htm") is not generic


def test_get_parser_fallback():
    assert get_parser("https://unknown.example.com/") is generic
