# 数据抓取模块

## 架构

```
scrapers/
├── http_client.py          # HTTP 抓取客户端（curl_cffi + chrome110 指纹）
├── web_scraper.py          # 主协调器，并发抓取列表 / 详情
├── page_parser.py          # HTML 解析（BeautifulSoup）
├── data_processor.py       # 数据合并 / 验证 / 清理
├── data_manager.py         # MongoDB / MySQL 写入与去重
└── notification_manager.py # 推送（Telegram / 企业微信）
```

抓取层走 `curl_cffi.requests`，伪装 Chrome 110 的 TLS / JA3 指纹直接拿 HTML，
无需真实浏览器。自动处理两种拦截：

- **R18 拦截页**（小体积 + 含 `var safeid='xxx'` 内嵌脚本）：自动提取 `safeid`
  写入 `_safe` cookie 并重试一次。
- **Cloudflare 挑战**（403/429/503 或挑战标题）：可选调用 FlareSolverr 服务
  自动过盾，未配置 `flaresolverr_url` 时放弃本次抓取并告警。

## 使用

```python
from scrapers.web_scraper import WebScraper

with WebScraper() as scraper:
    result = await scraper.crawl_forum_section(fid)
```

## 关键配置（config.yaml）

```yaml
http_client:
  concurrent_workers: 6       # 并发线程数
  request_timeout: 15
  flaresolverr_url: ""        # 留空则禁用 CF 自动过盾

browser:
  user_agent: "Mozilla/5.0 ..."  # 仅 user_agent 字段被 http_client 复用，其他字段保留兼容
```

## 扩展

- 新增解析器：继承 `PageParser`
- 新增数据库：扩展 `DataManager`
- 新增通知方式：扩展 `NotificationManager`
