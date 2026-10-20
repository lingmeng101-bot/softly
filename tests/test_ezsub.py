from crawler.sites import ezsub, get_parser, host_of

HTML = """
<html><body>
<div class="list__CardsGrid">
  <a class="list__ProductCard-sc-lmk624-12 x" href="/zh-cn/subscription/chatgpt-recharge">
    <h2 class="list__ProductTitle-sc-lmk624-15 y">ChatGPT</h2>
    <div class="list__PriceRow"><div class="list__PriceMain-sc-lmk624-19 z">￥19</div>
      <div class="list__DiscountTag-sc-lmk624-21 d">72%优惠</div></div>
  </a>
  <a class="list__ProductCard-sc-lmk624-12 x" href="/zh-cn/subscription/claude">
    <h2 class="list__ProductTitle-sc-lmk624-15 y">Claude</h2>
    <div class="list__PriceRow"><div class="list__PriceMain-sc-lmk624-19 z">￥36</div>
      <div class="list__PriceOld-sc-lmk624-20 o">￥67</div>
      <div class="list__DiscountTag-sc-lmk624-21 d">46%优惠</div></div>
  </a>
  <a href="/zh-cn/blog">非商品链接</a>
</div>
</body></html>
"""


def test_parse_list_cards():
    items = ezsub.parse_list(HTML, "https://ezsubxiaoali.ezsub.com/zh-cn/subscriptions")
    assert len(items) == 2
    assert items[0]["url"] == "https://ezsubxiaoali.ezsub.com/zh-cn/subscription/chatgpt-recharge"
    assert items[0]["title"] == "ChatGPT"
    assert "￥19" in items[0]["summary"] and "72%优惠" in items[0]["summary"]
    assert items[1]["title"] == "Claude"
    assert "￥67" in items[1]["summary"]


def test_get_parser_routes_to_ezsub():
    assert get_parser("https://ezsubxiaoali.ezsub.com/zh-cn/subscriptions") is ezsub


def test_host_of_ezsub():
    assert host_of("https://ezsubxiaoali.ezsub.com/zh-cn/subscriptions") == "ezsubxiaoali.ezsub.com"
