# softly

多站点爬虫

## 结构

```
main.py
  ├─ setup_logger / init_db（自动创建 data/）
  ├─ proxy_alive（检测 Clash 可用性；不可用时逐站探测目标站直连可达性，AUTO 可达则回落直连，FORCE 跳过）
  └─ config.TARGETS（站点列表：url / max_pages / fetch_detail / route）
       └─ pipeline.crawl_and_save(target, conn)
            ① 取数       crawler/fetcher.py   四层降级：httpx → curl_cffi → httpcloak → playwright
                         缺失或不可用的层级自动跳过；站点命中高层后本轮保持该层级；T3 遇到 Cloudflare 挑战页会等待其完成（PLAYWRIGHT_CHALLENGE_WAIT）
            ② 格式判断   crawler/sniff.py     json / html / xml / text（text 类型丢弃）
            ③ 接口报错   crawler/guard.py     200 但业务 code 报错 → api_error
            ④ 选择解析器 crawler/sites/       命中专属解析器则使用，否则使用 generic
            ⑤ 解析       mod.parse_list(payload, source)；空壳 HTML 时降一层重取并重新判定格式
            ⑥ 最终校验   缺少 url 或 title 的条目丢弃
            ⑦ 详情页     target.fetch_detail=True 且实现 parse_detail 时抓取正文；被拦截记 blocked，超过 DETAIL_REFRESH_DAYS 天后重抓
            ⑧ 入库       storage.py           articles 表，dedup_key 唯一，冲突时更新
            ⑨ 翻页       mod.next_page_url / paginator，带环路保护
```

`access_status` 取值：`normal` / `blocked` / `api_error` / `parse_error` / `empty` / `text_unsupported`。
非 `normal` 记录在下次运行时会重新抓取并覆盖。

## 流程图

![主流程](docs/flow.png)

Mermaid 源文件：[docs/flow.mmd](docs/flow.mmd)。

## 依赖

```
pip install -r requirements.txt
```

`curl_cffi` / `httpcloak` / `playwright` 为可选层，任一缺失均可运行，仅跳过对应层级；
playwright 需额外执行 `playwright install chromium`。

## 代理

是否经过代理由 `Target.route` 控制：`AUTO` 经 Clash 由规则分流；`DIRECT` 不设置代理；
`FORCE` 经 Clash 且不回落直连。具体域名分流由 Clash 规则负责，代码中不维护分流规则。
缺省代理地址见 `config.PROXY_HTTP`。

可选：`Target.rotate_group` 指定全部层级失败且原因为 403 时用于切换节点的策略组
（缺省 `config.CLASH_GROUP`）；该字段不绑定正常请求的路由。

## 需要人工验证的站点

部分站点（如 Cloudflare 保护的面板）会先返回人机验证页。本项目**不做 JS 绕过**：
先在浏览器中手动完成验证，再导出会话给爬虫复用，相关配置写在 `.env`：

- `HTTP_COOKIE` / `HTTP_USER_AGENT`：供 T0 使用，需与过验证时的浏览器一致
- `PLAYWRIGHT_STORAGE_STATE`：浏览器导出的 cookies JSON（如 Cookie-Editor 导出），T3 会用它创建浏览器上下文

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


## 说明

本项目由用户与 AI 协同完成：用户提供原始项目、重构任务书与验收标准，
AI 负责重构实现、测试与文档；最终取舍与验收由用户确认。