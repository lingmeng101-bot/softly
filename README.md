# softly

多站点爬虫

```
main.py
  └─ 读 config.TARGETS               要爬哪些站
       └─ pipeline.crawl_and_save(target, conn)
            ① 取数       crawler/fetcher.py      httpx + 重试 + 编码识别
            ② 格式判断   crawler/sniff.py        json / html
            ③ 选解析器   crawler/sites/          命中专属用专属，没命中用 generic
            ④ 解析       mod.parse_list(payload, source)
            ⑤ 最后判断   缺 url 或标题 → 丢
            ⑥ 入库       storage.py             articles 表，url 唯一，重复则更新
            ⑦ 翻页       mod.next_page_url(...)  没这个函数就一页结束
```
