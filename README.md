# 多来源数据抓取系统

这是一个基于 Python 3.11、`curl_cffi` 和 BeautifulSoup 的多来源爬虫系统。
当前包含三个来源：

- `sehuatang`：按论坛板块抓取主题，支持年度历史补抓、R18 safeid 和可选
  FlareSolverr。
- `javbee`：抓取最新列表及详情，支持按新数据、全量或过期时间刷新。
- `x1080x`：通过 Discuz archiver 模式抓取（游客可访问，无需论坛账号），
  站点有 Cloudflare JS 挑战，必须配置 FlareSolverr；过盾 Cookie 会被缓存，
  后续请求直连复用。

两个来源共用请求重试、日志、运行状态、失败恢复、CLI 和调度器，但分别保留
自己的代理、并发、超时、解析器、数据 schema 和业务策略。

## 快速开始

```powershell
cd D:\project\python\sehuatang
mamba activate ame
pip install -r requirements.txt

Copy-Item config\config.example.yaml config\config.yaml
# 编辑 config/config.yaml，填写数据库和通知配置

python run.py --mode health
python run.py --help
```

不再需要 Chrome 或 Selenium。HTTP 抓取统一使用 `curl_cffi` 模拟浏览器指纹。

## 配置

唯一配置模板是 [config/config.example.yaml](config/config.example.yaml)。运行配置
固定为 `config/config.yaml`，已被 Git 忽略，不应提交真实密码、Token 或代理凭据。

核心结构：

```yaml
mongodb:
  enable: true
  connection_string: "mongodb+srv://user:password@cluster/database"
  use_conn_str: true

sehuatang:
  domain_name: "sehuatang.org"
  fid:
    103: "高清中文字幕"
    104: "素人有码系列"
  page_num: 5
  date: null

javbee:
  base_url: "https://javbee.co"
  start_path: "/new"
  page_limit: 30

crawler:
  defaults:
    concurrency: 4
    http:
      timeout: 30
      retry:
        attempts: 3
        base_delay: 2
        max_delay: 15
        jitter: 0.3
  sources:
    sehuatang:
      enabled: true
      http:
        proxy:
          enabled: true
          url: "http://127.0.0.1:7890"
      schedule:
        cron: "0 2 * * *"
    javbee:
      enabled: true
      concurrency: 3
      http:
        timeout: 60
        proxy:
          enabled: false
          url: ""
      refresh:
        mode: "stale_after"
        days: 7
      schedule:
        cron: "30 2 * * *"
```

来源级环境变量：

```text
CRAWLER_JAVBEE_CONCURRENCY
CRAWLER_JAVBEE_TIMEOUT
CRAWLER_JAVBEE_PROXY_ENABLED
CRAWLER_JAVBEE_PROXY_URL
CRAWLER_JAVBEE_RETRY_ATTEMPTS
CRAWLER_SEHUATANG_FLARESOLVERR_URL
```

代理 URL 和 `PROXY_ENABLED=true` 应同时设置。把变量中的 `JAVBEE` 替换为
`SEHUATANG` 可覆盖另一个来源。数据库和通知配置仍支持 `SHT_` 加完整配置路径
的环境变量形式，例如 `SHT_MONGODB_CONNECTION_STRING`。

旧版顶层 `javbee.concurrent_workers/request_timeout/proxy_*`、`http_client` 和
`proxy` 配置仍可读取，但新配置应使用 `crawler.sources.<source>`，避免同名配置
串值。

## Web 管理页

```powershell
python run.py --mode web
```

调度器 + 管理页一起运行（docker 默认入口）。页面地址 `http://127.0.0.1:8181`，
提供：来源状态与手动触发（抓取 / dry-run / 重试失败）、分页补抓触发与进度、
失败台账、运行历史（`crawl_runs` collection，保留 90 天）、配置文件在线编辑
（保存前校验 YAML，原文件备份为 `config.yaml.bak`，重启后生效）和一键重启。

安全约定：未配置 token 时仅允许本机访问；监听非本机地址（含 docker）必须设置
`web.token` 或环境变量 `SHT_WEB_TOKEN`，页面右上角 Token 按钮填入。同一来源
的手动触发与调度任务在进程内互斥，重复触发会得到 `already_running`。

## 运行命令

```powershell
# 启动调度器
python run.py

# 运行一个或全部来源
python run.py crawl --source sehuatang
python run.py crawl --source javbee
python run.py crawl --source x1080x
python run.py crawl --source all

# 真实访问并解析，但不写库、不通知、不推进 checkpoint
python run.py crawl --source javbee --dry-run

# 重试失败台账中已到期的目标，不重新扫描列表页
python run.py retry-failed --source javbee
python run.py retry-failed --source sehuatang

# 按页区间补抓历史数据（推荐）
python run.py backfill-pages --source x1080x --end-page 200
python run.py backfill-pages --source x1080x --typeid 5479 --end-page 500 --resume
python run.py backfill-pages --source sehuatang --fid 103 --end-page 300
python run.py backfill-pages --source sehuatang --fid 103 --end-page 300 --dry-run

# Sehuatang 年度补抓（旧方案，按年份二分定位）
python run.py --mode backfill --year 2025
python run.py --mode backfill --year 2025 --fid 103 --fid 104
python run.py --mode backfill --year 2025 --resume
python run.py --mode backfill --year 2025 --resume --dry-run

# 兼容入口
python run.py --mode once
python run.py --mode javbee
python run.py --mode health
```

`backfill-pages` 按页区间补抓：跳过已入库数据、不做日期过滤，检查点按完整
处理完的页推进（保存在 `data/page_backfill_progress.json`，键为
`source:partition`）。详情失败写入失败台账、由 `retry-failed` 恢复，不阻塞页
进度；列表页失败则该分区暂停且检查点不推进，可用 `--resume` 继续。

年度补抓按发帖时间二分定位页码范围。进度保存在
`data/backfill_progress.json`；任何详情获取、解析或必要字段校验失败都会暂停推进
checkpoint，成功数据仍会保存，修复后可使用 `--resume` 继续。

## 请求与失败恢复

公共 HTTP 层仅重试连接错误、超时、空响应和
`408/425/429/500/502/503/504`，普通 `4xx` 不重试。重试采用指数退避、随机
抖动，并尊重数字形式的 `Retry-After`。

运行状态分为：

- `success`：所有计划目标成功。
- `partial_success`：成功保存部分记录，同时存在终态失败。
- `failed`：来源或全部目标失败。

MongoDB 启用时，终态详情失败写入 `crawl_failures` collection；否则写入
`data/crawl_failures.json`。成功保存后会清除对应失败记录。

## 数据存储

Sehuatang 保留现有按板块分 collection 的结构，使用 MongoDB 存储。
JavBee 固定写入 MongoDB `javbee_items`，以 `source_key` 唯一索引幂等 upsert。
本次多来源重构不合并或迁移现有业务 collection schema。

x1080x 写入单一 collection `x1080x_items`，分区（typeid/section）作为文档
字段而不是分表：

- `source_key`（=tid）唯一索引，幂等 upsert；
- `(typeid, date)` 复合索引支持分区内按发布日期查询；
- `(date, tid)`、`(code_normalized, date)` 支持按日期、按番号查询；
- `collected_at` / `resource_updated_at` 时钟契约与 `javbee_items` 一致，
  typeid/section 归类变化也会推进有效变更时间；
- `magnet` 存主磁链（字符串），`magnets` 存全部磁链，`img` 存预览图列表。

## Docker

```powershell
docker compose up -d
docker compose logs -f sehuatang-crawler
docker compose down
```

容器默认运行 `python run.py --mode web`（调度器 + 管理页，端口 8181，请配置
`SHT_WEB_TOKEN`）。配置目录挂载到 `/app/config`（管理页需要写入），日志和
运行状态分别挂载到 `/app/logs`、`/app/data`。管理页的重启按钮通过退出进程
配合 `restart: unless-stopped` 实现容器级重启。

CF 过盾使用 compose 内置的 `byparr` 服务（FlareSolverr 兼容 API），在
config 的 `crawler.sources.<source>.challenge.flaresolverr_url` 填
`http://byparr:8191/v1`；x1080x 必须配置，sehuatang 在触发 CF 时使用。

脚本用途见 [scripts/README.md](scripts/README.md)。

## 项目结构

```text
scrapers/
  core/                 公共合同、配置、HTTP、引擎和运行模型
  infrastructure/       MongoDB/JSON 失败台账
  sources/
    javbee/             JavBee source、parser、repository
    sehuatang/          Sehuatang 来源入口
  registry.py           显式来源注册表
scripts/
  diagnostics/          手工只读诊断脚本
tests/                  解析、配置、重试、存储和恢复测试
main.py                 应用编排
run.py                  唯一 CLI 与 scheduler 入口
```

公共层和新增来源约束见 [scrapers/README.md](scrapers/README.md)，当前重构检查项见
[docs/CRAWLER_REFACTOR_TODO.md](docs/CRAWLER_REFACTOR_TODO.md)。

## 测试

```powershell
mamba run -n ame python -m unittest `
  tests.crawler_core_tests `
  tests.javbee_tests `
  tests.backfill_tests `
  tests.sehuatang_source_tests `
  tests.extract_and_query_tests `
  tests.x1080x_tests `
  tests.page_backfill_tests `
  tests.web_app_tests

mamba run -n ame python -m compileall -q main.py run.py scrapers util tests
python run.py --mode health
```

手工诊断脚本会访问真实站点，但不会写数据库：

```powershell
python scripts\diagnostics\http_smoke.py
python scripts\diagnostics\source_smoke.py
```

## 安全说明

- 不要在 README、测试脚本、命令行参数或 Git 中保存 Cookie、数据库密码、Bot
  Token 和代理认证信息。
- `config/config.yaml`、`.env*`、运行时数据和生成查询已加入 `.gitignore`。
- 如果历史提交曾包含仍有效的凭据，应立即轮换；仅从当前文件删除不能清理 Git
  历史。

项目采用 [MIT License](LICENSE)。
