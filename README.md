# softly

多站点爬虫

## 结构

```
main.py
  ├─ setup_logger / init_db（自动创建 data/）
  ├─ proxy_alive（检测 Clash 可用性；不可用时仅运行 route=DIRECT 的站点）
  └─ config.TARGETS（站点列表：url / max_pages / fetch_detail / route）
       └─ pipeline.crawl_and_save(target, conn)
            ① 取数       crawler/fetcher.py   四层降级：httpx → curl_cffi → httpcloak → playwright
                         缺失或不可用的层级自动跳过；站点命中高层后本轮保持该层级
            ② 格式判断   crawler/sniff.py     json / html / xml / text（text 类型丢弃）
            ③ 接口报错   crawler/guard.py     200 但业务 code 报错 → api_error
            ④ 选择解析器 crawler/sites/       命中专属解析器则使用，否则使用 generic
            ⑤ 解析       mod.parse_list(payload, source)；空壳 HTML 时降一层重取一次
            ⑥ 最终校验   缺少 url 或 title 的条目丢弃
            ⑦ 详情页     target.fetch_detail=True 且实现 parse_detail 时抓取正文；被拦截记 blocked
            ⑧ 入库       storage.py           articles 表，dedup_key 唯一，冲突时更新
            ⑨ 翻页       mod.next_page_url / paginator，带环路保护
```

`access_status` 取值：`normal` / `blocked` / `api_error` / `parse_error` / `empty` / `text_unsupported`。
非 `normal` 记录在下次运行时会重新抓取并覆盖。

## 依赖

```
pip install -r requirements.txt
```

`curl_cffi` / `httpcloak` / `playwright` 为可选层，任一缺失均可运行，仅跳过对应层级；
playwright 需额外执行 `playwright install chromium`。

## 代理

是否经过代理由 `Target.route` 控制（AUTO / DIRECT / FORCE）；具体域名分流由 Clash 规则负责，
代码中不维护分流规则。缺省代理地址见 `config.PROXY_HTTP`。

## 本机配置（`.env`）

代理端口、外部控制地址、密钥、策略组名均从项目根目录的 `.env` 读取（已被 `.gitignore` 忽略）：

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

## 测试

```
pytest -q
```
