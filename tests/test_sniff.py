import httpx

from crawler.sniff import extract_mime, sniff


def make(content_type, body):
    return httpx.Response(200, headers={"content-type": content_type}, content=body)


def test_json_by_mime():
    res = make("application/json; charset=utf-8", b"{}")
    assert extract_mime(res) == "application/json"
    assert sniff(res) == "json"


def test_html_by_mime():
    assert sniff(make("text/html", b"<html></html>")) == "html"


def test_xml_by_mime():
    assert sniff(make("application/xml", b"<a/>")) == "xml"


def test_json_by_body():
    assert sniff(make("text/plain", b'  {"a": 1}')) == "json"


def test_text_fallback():
    assert sniff(make("text/plain", b"hello")) == "text"
