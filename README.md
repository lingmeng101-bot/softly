# softly

多站点爬虫

```
main.py
  ├─ setup_logger / init_db（自动建 data/）
  ├─ proxy_alive（Clash 通不通；挂了只跑 route=DIRECT 的站）
  └─ 读 config.TARGETS            要爬哪些站（url / max_pages / fetch_detail / route）
       └─ pipeline.crawl_and_save(target, conn)
            ① 取数       crawler/fetcher.py   四层降级：httpx → curl_cffi → httpcloak → playwright
                         缺依赖或不可用的层自动跳过；某站命中高层后本轮粘滞
            ② 格式判断   crawler/sniff.py     json / html / xml / text（text 直接丢）
            ③ 接口报错   crawler/guard.py     200 但 code 报错 → api_error
            ④ 选解析器   crawler/sites/       命中专属用专属，没命中用 generic
            ⑤ 解析       mod.parse_list(payload, source)；空壳 HTML 再降一层重取一次
            ⑥ 最后判断   缺 url 或标题 → 丢
            ⑦ 详情页     target.fetch_detail=True 且有 parse_detail 时抓正文；被拦记 blocked
            ⑧ 入库       storage.py           articles 表，dedup_key 唯一，重复则更新
            ⑨ 翻页       mod.next_page_url / paginator，带环路保护
```

`access_status`：`normal` / `blocked` / `api_error` / `parse_error` / `empty` / `text_unsupported`。
非 `normal` 的记录下次重跑会重抓覆盖，不会永远 skip。

依赖：`pip install -r requirements.txt`。`curl_cffi` / `httpcloak` / `playwright` 任意一个缺失都能跑，
只是降级链少一层；playwright 还要额外 `playwright install chromium`。

代理：`config.PROXY_HTTP`（本机值写在 `.env`，缺省 `127.0.0.1:7897`）。走不走 Clash 由 `Target.route` 决定，
具体哪个域名走哪个节点交给 Clash 的规则分流，爬虫里不写分流规则。
## 本机配置（`.env`）

代理端口、外部控制地址、密钥、策略组名都在项目根目录的 `.env` 里（已在 `.gitignore`，不会提交）：

```
copy .env.example .env
```

```
PROXY_HTTP=http://127.0.0.1:7897
CLASH_API=http://127.0.0.1:9097
CLASH_SECRET=
CLASH_GROUP=🚀 节点选择
```

真实环境变量优先于 `.env`；缺省值见 [config.py](config.py)。