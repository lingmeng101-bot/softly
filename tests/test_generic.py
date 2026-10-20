from crawler.sites import generic


def test_auto_json_skips_items_without_url():
    data = {"data": [
        {"title": "有链接", "link": "/a"},
        {"title": "缺链接"},
        {"title": "空链接", "link": ""},
        {"title": "空白链接", "link": "   "},
    ]}
    items = generic.auto_json(data, "https://shop.example.com/list")
    assert [it.url for it in items] == ["https://shop.example.com/a"]


def test_auto_json_joins_relative_url():
    data = {"items": [
        {"name": "A", "href": "a/1"},
        {"name": "B", "href": "https://other.example.com/b"},
    ]}
    items = generic.auto_json(data, "https://shop.example.com/dir/page")
    assert items[0].url == "https://shop.example.com/dir/a/1"
    assert items[1].url == "https://other.example.com/b"


def test_auto_json_maps_time_and_content():
    # find_list 要求至少两条同构 dict
    data = {"results": [
        {"title": "T", "url": "/x", "publish_time": "2026-01-02", "content": "正文"},
        {"title": "T2", "url": "/y", "publish_time": "2026-01-03", "content": "正文2"},
    ]}
    items = generic.auto_json(data, "https://a.example.com/")
    assert items[0].publish_time == "2026-01-02"
    assert items[0].content == "正文"
    assert items[1].url == "https://a.example.com/y"