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


DETAIL_HTML = """
<html><body>
<h1 class="detail__HeroTitle-sc-19dihxg-17 x">Discord</h1>
<p class="detail__HeroSubTitle-sc-19dihxg-18 y">DiscordNitro BASIC一个月会员充值</p>
<div class="detail__StyledSkuBox-sc-19dihxg-30 a">
  <h2 class="detail__StyledTitle-sc-19dihxg-27 b">套餐</h2>
  <div class="sku-list">
    <div class="sku-item"><div class="content"><span class="name">DiscordNitro</span></div></div>
    <div class="sku-item"><div class="content"><span class="name">DiscordNitro BASIC</span></div></div>
  </div>
</div>
<div class="detail__StyledSkuBox-sc-19dihxg-30 c leafCardBox">
  <h2 class="detail__StyledTitle-sc-19dihxg-27 b">时长</h2>
  <div class="sku-list">
    <div class="sku-item isLeaf"><div class="content"><span class="name">12个月</span>
      <span class="price-box"><span class="price">￥ 199.00</span>
      <span class="promotion-discount">1%优惠</span></span></div></div>
  </div>
</div>
<section id="product-details"><h2>商品详情</h2>
  <div class="detail__DetailContent-sc-19dihxg-36 d"><p>购买前须知</p><p>本商品交付兑换链接</p></div>
</section>
</body></html>
"""


def test_parse_detail_extracts_sku_and_content():
    text = ezsub.parse_detail(DETAIL_HTML)
    assert "Discord" in text
    assert "DiscordNitro BASIC一个月会员充值" in text
    assert "12个月" in text and "￥ 199.00" in text and "1%优惠" in text
    assert "购买前须知" in text and "本商品交付兑换链接" in text


def test_parse_detail_returns_blocked_on_challenge():
    assert ezsub.parse_detail("<html><title>Just a moment...</title></html>") == "blocked"
