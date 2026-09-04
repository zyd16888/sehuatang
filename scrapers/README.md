# 多来源爬虫架构

## 模块边界

```text
run.py / scheduler
        |
scrapers.registry
        |
  +-----+------------------+
  |                        |
sources/sehuatang      sources/javbee
  |                        |
  +----- core + infrastructure -----+
```

`scrapers/core` 负责来源无关能力：

- `config.py`：默认值、来源覆盖、环境变量覆盖和配置校验
- `http.py`：代理、超时、可重试状态分类、指数退避、抖动和批量请求
- `contracts.py`：source、repository、failure store 的数据合同
- `engine.py`：发现、筛选、详情抓取、解析、保存和运行汇总
- `models.py`：请求结果及 `success/partial_success/failed` 状态

`scrapers/sources/<name>` 负责站点差异：

- 列表入口和分页发现
- HTML 解析及数据校验
- 唯一键、刷新策略和 collection 映射
- 站点专属挑战及特殊任务

`scrapers/infrastructure` 负责 MongoDB/JSON 失败台账等外部适配。

## HTTP 与重试

每个来源独立解析 `concurrency`、`http.timeout`、`http.proxy` 和
`http.retry`。默认只重试连接类异常、空响应以及
`408/425/429/500/502/503/504`，普通 `4xx` 不重试。退避时间使用指数增长和
随机抖动，并尊重数字形式的 `Retry-After`。

Sehuatang 的 R18 safeid 和 FlareSolverr 留在来源专属 `HttpClient` 中；普通
网络重试委托给公共 transport。JavBee 直接使用公共 transport。

环境变量使用明确的来源前缀：

```text
CRAWLER_JAVBEE_CONCURRENCY
CRAWLER_JAVBEE_TIMEOUT
CRAWLER_JAVBEE_PROXY_ENABLED
CRAWLER_JAVBEE_PROXY_URL
CRAWLER_JAVBEE_RETRY_ATTEMPTS
CRAWLER_SEHUATANG_FLARESOLVERR_URL
```

代理 URL 与 `PROXY_ENABLED=true` 应同时设置；把 `JAVBEE` 替换为
`SEHUATANG` 即可覆盖另一个来源。代理凭据不要写入仓库，
公共日志会隐藏 URL 用户信息和常见敏感查询参数。

## 运行入口

```powershell
# 运行一个或全部来源
python run.py crawl --source javbee
python run.py crawl --source sehuatang
python run.py crawl --source all

# 只抓取和解析，不写库、不通知、不推进 checkpoint
python run.py crawl --source javbee --dry-run

# 重试失败台账中已到期的目标，不重新扫描列表页
python run.py retry-failed --source javbee
python run.py retry-failed --source sehuatang
```

`--mode once|javbee|backfill|bot|health` 继续兼容。新配置使用
`crawler.sources.<source>.schedule.cron` 为每个来源创建独立任务；未配置来源级
cron 时，调度器回退到旧的统一 `schedule.schedule_cron`。

## 失败台账

终态详情失败保存以下信息：

```text
source / source_key / url / stage / attempts / error_type
error_message / metadata / failure_count / next_retry_at
```

MongoDB 启用时写入 `crawl_failures` collection，否则写入
`data/crawl_failures.json`。成功保存后会清除同来源、同 key 的失败记录。

## 新增来源

1. 在 `scrapers/sources/<name>` 实现来源发现和解析逻辑。
2. 使用 `CrawlerHttpClient`，不要在来源内复制代理和重试循环。
3. 实现 `RecordRepository`，保留该来源自己的 schema 和唯一键。
4. 在 `scrapers/registry.py` 显式注册来源及 runner。
5. 为解析 fixture、刷新策略、部分成功和失败恢复补充测试。

不使用目录扫描或动态插件加载；显式 registry 更容易审查，也足以支持当前规模。
