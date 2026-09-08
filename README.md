# 多来源数据抓取系统

这是一个基于 Python 3.11、`curl_cffi` 和 BeautifulSoup 的多来源爬虫系统。
当前包含三个来源：

- `sehuatang`：按论坛板块抓取主题，支持年度历史补抓、R18 safeid 和可选
  FlareSolverr。
- `javbee`：抓取最新列表及详情，支持按新数据、全量或过期时间刷新。
- `x1080x`：通过 Discuz archiver 模式抓取（游客可访问，无需论坛账号），
  站点有 Cloudflare JS 挑战，必须配置 FlareSolverr；过盾 Cookie 会被缓存，
  后续请求直连复用。支持按页补抓、限流冷却后自动恢复和历史空番号补录。

三个来源复用请求重试、日志、运行管理、失败恢复、CLI 和调度器，但分别保留
自己的代理、并发、超时、解析器、数据 schema 和业务策略。

## 快速开始

```powershell
# 在项目根目录执行；推荐 Python 3.11 及以上版本
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
# 离线测试的 FastAPI TestClient 需要 HTTP 客户端
python -m pip install httpx

# 仅首次配置时复制，已有配置直接编辑
Copy-Item config/config.example.yaml config/config.yaml
# 填写 MongoDB，开启需要的来源；不需要通知时关闭 send_telegram_enable

python run.py --mode health
python run.py --help
python run.py --mode web
```

管理页默认地址为 `http://127.0.0.1:8181`。HTTP 抓取使用 `curl_cffi` 模拟浏览器指纹，
不需要本地安装 Selenium 或浏览器驱动；CF 验证仍由独立的 byparr / FlareSolverr
浏览器服务处理。`--mode health` 检查配置与日志初始化，不验证站点、数据库或通知连通性。

## 配置

完整配置模板是 [config/config.example.yaml](config/config.example.yaml)。读取顺序为
`config/config.yaml` → 根目录 `config.yaml` → 示例配置，推荐日常运行固定使用
`config/config.yaml`。运行配置已被 Git 忽略，不应提交真实密码、Token 或代理凭据。
模板默认开启 sehuatang，关闭 javbee 和 x1080x；启用后两者需要 MongoDB。

核心结构（以下为配置摘录，首次部署请复制完整模板）：

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

x1080x:
  base_url: "https://agaghhh.cc"
  fid: 244
  page_limit: 3
  # typeids 分类映射见完整模板，可按需覆盖

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
      enabled: false
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
    x1080x:
      enabled: false
      concurrency: 1
      rate_limit:
        min_interval_seconds: 2
        cooldown_seconds: 60
        max_cooldown_seconds: 900
      challenge:
        flaresolverr_url: "http://byparr:8191/v1"
      # HTTP 指纹和 User-Agent 使用完整模板的 x1080x 配置
      schedule:
        cron: "0 3 * * *"
```

来源级环境变量：

```text
CRAWLER_JAVBEE_CONCURRENCY
CRAWLER_JAVBEE_TIMEOUT
CRAWLER_JAVBEE_PROXY_ENABLED
CRAWLER_JAVBEE_PROXY_URL
CRAWLER_JAVBEE_RETRY_ATTEMPTS
CRAWLER_SEHUATANG_FLARESOLVERR_URL
CRAWLER_X1080X_BASE_URL
CRAWLER_X1080X_FLARESOLVERR_URL
SHT_MONGODB_CONNECTION_STRING
SHT_WEB_HOST
SHT_WEB_TOKEN
```

代理 URL 和 `PROXY_ENABLED=true` 应同时设置。把变量中的 `JAVBEE` 替换为
`SEHUATANG` 或 `X1080X` 可覆盖其他来源。数据库和通知配置仍支持 `SHT_` 加完整配置路径
的环境变量形式，例如 `SHT_MONGODB_CONNECTION_STRING`。

旧版顶层 `javbee.concurrent_workers/request_timeout/proxy_*`、`http_client` 和
`proxy` 配置仍可读取，但新配置应使用 `crawler.sources.<source>`，避免同名配置
串值。

JavBee 和 x1080x 的运行入口向 HTTP 客户端传递合并后的来源配置，调节其并发、超时、
代理和重试时应在 `crawler.sources.<source>` 明确填写，不要只修改 `crawler.defaults`。
环境变量需传入容器才会生效，可在 Compose 服务的 `environment` 中配置。

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
python run.py retry-failed --source x1080x

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

定时调度和 `--mode once` 遵守来源 `enabled`；显式 `crawl`、`retry-failed` 命令会
强制运行所选来源，`crawl --source all` 也包括配置中关闭的来源。`retry-failed`
省略来源时默认 javbee，建议始终指定 `--source`。Cron 使用五段表达式和运行环境时区，
Compose 默认 `Asia/Shanghai`。

`backfill-pages` 按页区间补抓：跳过已入库数据、不做日期过滤，检查点按完整
处理完的页推进（保存在 `data/page_backfill_progress.json`，键为
`source:partition`）。详情失败写入失败台账、由 `retry-failed` 恢复，不阻塞页
进度；普通列表请求失败则该分区暂停且检查点不推进，可用 `--resume` 继续。
`--start-page` 默认 1，`--end-page` 包含在范围内，`--typeid` 和 `--fid` 可重复指定。
**`--resume` 不会回扫已完成页中的失败详情**，这些目标需要通过失败台账恢复。
x1080x 的站点限流采用下述自动冷却恢复，不按普通详情失败处理。

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

### x1080x 番号识别与空 code 补录

识别顺序为「标题括号 → 磁链 `dn` → 标题开头规则」，结果保存为 `code`、
`code_normalized`、`code_source`、`code_confidence`。

| 标题编号 | code | code_normalized |
| --- | --- | --- |
| `(xb-1774)` | `XB-1774` | `XB1774` |
| `(m-331)` | `M-331` | `M331` |
| `(jv-78)` | `JV-78` | `JV78` |
| `(xjx-2)` | `XJX-2` | `XJX2` |
| `(thxp20230329_003)` | `THXP20230329-003` | `THXP20230329003` |
| `(zb20230329_028)` | `ZB20230329-028` | `ZB20230329028` |
| `(mdsr-0010-1)` | `MDSR-0010-1` | `MDSR00101` |
| `(an-9-046)` | `AN-9-046` | `AN9046` |
| `(mnsc-mb-112)` | `MNSC-MB-112` | `MNSCMB112` |

标题与磁链 `dn` 使用同一套完整编号规则，支持前缀加日期/序号、分集后缀和多段字母前缀。
统一大写、下划线转连字符，保留前导零和全部分段，不把 `MDSR-0010-1` 截成 `MDSR-0010`。
`THXP20230329-003` 这类是站点资源编号，并不要求是发行商的标准番号。

单字母前缀要求 `-` 或 `_` 分隔，序号支持一位数字；纯日期括号、厂牌和括号里的 `H264`
等标记不按新增规则识别。不能确定时保留空 code，不用帖子 ID 生成假番号。
抓取默认跳过已存在帖子，更新识别代码不会自动修复历史空 code，需要单独补录：

```bash
# 预览单条或全部空 code，不写库
python scripts/backfill_x1080x_codes.py --dry-run --source-key 1013258
python scripts/backfill_x1080x_codes.py --dry-run --sample-limit 50

# 正式补录：不带 --dry-run 会写库
python scripts/backfill_x1080x_codes.py --source-key 1013258
python scripts/backfill_x1080x_codes.py --batch-size 500

# 容器内预览
docker exec sehuatang-crawler python /app/scripts/backfill_x1080x_codes.py --dry-run --sample-limit 50
```

脚本只读取 MongoDB，不访问网站；只处理缺失、null 或空字符串的 code，已有非空编号不覆盖。
`--source-key` 对应帖子 ID，可以重复指定；`--sample-limit` 只限制预览展示数，不限制扫描量。
补录同步资源指纹、`updated_at` 和 `resource_updated_at`，保留首次创建/采集时间及其他内容。
读取后被其他任务修改的记录会跳过并报告，可重新执行。完整说明见 [运维脚本](scripts/README.md)。

### collection 与资源时钟

业务数据库名称为 `sehuatang`。Sehuatang 保留现有按板块分 collection 的结构，使用 MongoDB 存储。
JavBee 固定写入 MongoDB `javbee_items`，以 `source_key` 唯一索引幂等 upsert。
来源之间保持独立的业务 collection schema。

x1080x 写入单一 collection `x1080x_items`，分区（typeid/section）作为文档
字段而不是分表：

- `source_key`（=tid）唯一索引，幂等 upsert；
- `(typeid, date)` 复合索引支持分区内按发布日期查询；
- `(date, tid)`、`(code_normalized, date)` 支持按日期、按番号查询；
- `collected_at` / `resource_updated_at` 时钟契约与 `javbee_items` 一致，
  typeid/section 归类变化也会推进有效变更时间；
- `magnet` 存主磁链（字符串），`magnets` 存全部磁链，`img` 存预览图列表。

`date` / `post_time` 是来源发布时间，`created_at` / `collected_at` 是记录创建与首次采集时间；
`resource_updated_at` 表示有效资源变更，`resource_fingerprint` 用于判断内容是否变化。

## Docker

首次部署先复制配置模板，再填写数据库和来源设置。**Docker 中必须将 `web.host` 改为
`0.0.0.0` 并配置 Token**；模板中的 `127.0.0.1` 会覆盖容器默认值，导致映射端口无法访问。
使用代理时注意容器中的 `127.0.0.1` 指向容器自身。

```powershell
docker compose up -d
docker compose logs -f sehuatang-crawler
docker compose down
```

容器默认运行 `python run.py --mode web`（调度器 + 管理页，端口 8181，请配置
`SHT_WEB_TOKEN`）。配置目录挂载到 `/app/config`（管理页需要写入），日志和
运行状态分别挂载到 `/app/logs`、`/app/data`。检查点与诊断文件保存在数据卷，
管理页重启通过重新执行进程实现，失败时由 `restart: unless-stopped` 接管。

CF 过盾使用 compose 内置的 `byparr` 服务（FlareSolverr 兼容 API），在
config 的 `crawler.sources.<source>.challenge.flaresolverr_url` 填
`http://byparr:8191/v1`；x1080x 必须配置，sehuatang 在触发 CF 时使用。
本地 Python 访问 Compose 的验证服务时使用 `http://127.0.0.1:8191/v1`。

Compose 使用 `cxsz16888/sehuatang:v2`。仓库工作流在 `v2` 推送后触发镜像构建，
需确认构建发布成功，再更新容器：

```bash
docker compose pull sehuatang-crawler
docker compose up -d sehuatang-crawler
docker compose logs --tail 100 sehuatang-crawler
```

Git 提交或 `git pull` 不会自动更新正在运行的容器；已有配置也不会随镜像替换，
新增字段需对照模板合并。构建说明见 [GitHub Actions 指南](docs/GITHUB_ACTIONS_GUIDE.md)。

脚本用途见 [scripts/README.md](scripts/README.md)。

## Telegram 内存队列

采集端仅构造通知任务并写入当前进程的内存队列；单独的 `telegram-sender` 线程
负责格式化、下载防盗链图片和调用 Telegram。MongoDB 只保存原有采集数据，
不新增通知 collection、事务、租约或投递状态。

- sehuatang 保存完成后入队资源通知及板块汇总；x1080x 每批成功保存后立即入队，
  与后续采集并行。仍只通知增量新资源，dry-run、补抓、失败恢复不新增通知。
- Bot 按需在发送线程初始化。关闭通知时无需有效 Token，导入爬虫不加载 Telegram。
- FIFO 顺序发送，默认每次请求间隔一秒。发送失败最多尝试五次；网络错误指数退避，
  Telegram 429 尊重 `retry_after`，等待期间后续通知也保持排队。
- 已成功的图片/文本分组记录内存进度，重试只从未完成的分组继续；单张图片使用
  `send_photo`，无图通知发送文本，长说明拆为独立文本消息。
- 队列默认最多等待 1000 条任务。容量用完或关闭时立即拒绝新任务，记录“未入队”数量，
  不阻塞采集；这些通知不会自动补回。可以在 `sendMessage.queue.capacity` 调整容量。
- 管理页「通知队列」显示等待、发送中、退避和最近结果；最近 100 条结果保存在内存，
  其中失败任务可重新入队。累计发送、失败和拒绝数都只统计本次进程。
- 普通单次 CLI 会在结束前等待队列处理完毕；常驻服务停止/重启时默认最多等待
  30 秒收尾，Compose 给进程 45 秒退出时间。当前网络请求可能等待自身超时。

这是进程内存队列：强制退出、崩溃或重启会丢失尚未完成的通知和失败记录。
资源已经保存时，下次增量采集不会自动重新通知这些资源。每个进程拥有自己的队列，
应沿用当前单进程运行方式，不额外启动独立通知容器，也不使用多个 Web worker。
Telegram 已接收但客户端超时的请求，重试仍可能重复；内存进度不能消除这个窗口。

可配置项见示例 `sendMessage.queue`：`capacity`、`max_attempts`、
`min_interval_seconds`、`shutdown_timeout_seconds`。保存配置后重启生效。

## 失败恢复与管理页

### x1080x 限流冷却与自动恢复

`crawler.sources.x1080x.rate_limit` 默认最小请求间隔 2 秒、初始冷却 60 秒、
最大递增冷却 900 秒；未填写这段配置时也使用上述默认值。示例并发为 1，
已有显式并发配置继续生效。并发限制不等于请求频率限制。

请求间隔覆盖列表、详情、HTTP 重试和发起过盾请求；直连或过盾服务返回的站点限流页
都会被识别，不再当成正常详情送入解析器。

| 运行方式 | 命中站点限流后 |
| --- | --- |
| 增量抓取、`retry-failed` | 停止本轮，等待下次调度；不增加单帖台账失败次数 |
| `backfill-pages` | 自动等待，重试原列表页或受限详情，恢复后继续剩余页和分类 |

补抓连续受限时按 60、120、240、480、900 秒递增，之后每 900 秒重试；成功响应后重置。
直连响应的数字 `Retry-After` 更长时优先遵守服务端要求。限流恢复没有次数上限，
等待期间任务仍处于运行状态，日志会说明正在等待和待重试目标。

同批成功获取的详情会保留；当前页处理完成后才推进检查点。CLI 的 Ctrl+C 或服务停止
会唤醒冷却等待，已入库批次保留，未完成页可 `--resume` 继续；尚在内存中的详情需重新获取。
普通网络终态失败、CF 无法完成及真正缺字段仍走各自的失败恢复，不属于无限限流重试。

这些设置是保守起点，不是站点公布的配额。来源互斥、Cookie 和节流状态只在当前进程共享；
另开 `docker exec ... python run.py` 不会与常驻调度器共享它们。持续补抓优先从管理页发起，
避免同一来源多进程同时请求。详细机制见 [爬虫说明](scrapers/README.md)。

### 失败台账与重新入队

`crawler.retry_failed.max_failures` 默认 **5**，表示一条失败台账本轮累计失败的上限，
包含首次失败，与 `http.retry.attempts`（一次 HTTP 请求内部尝试次数）分开计数。
失败后的等待时间按台账次数递增：5、10、20、40 分钟，最多 24 小时；
实际执行还要等自动重试任务的下一个检查周期（默认 60 分钟）。
到达上限后停止自动及普通手动 `retry-failed` 重试，但保留记录。
旧台账即使没有新增字段，也会立即受上限约束，无需批量重写历史数据。

管理页顶部显示当前运行任务、已运行时长、下次调度时间与倒计时。
运行记录、失败台账、分页补抓、日志和配置分为独立标签，配置仅打开时加载。
失败台账按来源和状态筛选；单条「重新入队」，或选择来源后「重新入队已达上限」，
可以开启新一轮重试，历史 `failure_count` 不清零。
重新入队只更新台账：等待自动检查，或点击来源卡片的「重试失败」立即执行。
来源运行期间不允许重置其台账；分页补抓、定时抓取、手动重试共享来源互斥锁。

台账以 `source + source_key + stage` 为原始身份。恢复过程中阶段变化时沿用原始身份
和次数，用 `last_stage` 记录最新失败阶段，避免旧阶段一直到期造成无限重试。
`retry_reset_count` 保存重新入队时的累计计数，本轮次数为二者差值；
`requeued_at` 保存手动恢复时间。Mongo 和 JSON 使用同一政策。
历史 sehuatang 详情恢复不再按当天过滤，正常的日期筛选也不再记为校验失败；
真正缺少 `post_time` / `magnet` 等字段时会保留具体原因。

Sehuatang 的 R18 与 CF 采用最多三轮的验证转换，剩余拦截页不会进入正文解析。
同一客户端的并发请求共享验证结果；线程内复用 HTTP 连接并合并响应 Cookie。
x1080x 在同一进程内按域名、HTTP 设置（包括代理和指纹）、验证服务端点隔离缓存，
一小时后重建，最多保留八个客户端。只有其他线程更新了验证结果时才补一次直连，
请求耗时包含等待过盾的时间。缓存不写入磁盘，进程重启后首次请求可能需要重新验证。

这些优化不能保证站点不再触发 CF。若每次请求仍遇挑战，请核对生产环境的
代理出口、`impersonate`、UA 和实际 byparr/FlareSolverr 浏览器是否匹配；
不能只改 UA 就认为浏览器指纹已一致。当前没有假定 byparr 支持持久浏览器 session。

x1080x 详情校验失败会记录 `page_unavailable`、`missing_title`、`missing_date`、
`missing_content` 或 `missing_magnet`。异常页面保存为 `data/debug/x1080x_detail_<tid>.html`，
最多 20 个文件，每个最多 512 KiB；日志输出原因、标题、大小与路径，dry-run 不写快照。
通过 CF 只代表验证步骤完成，不代表一定拿到了帖子正文。

### sehuatang 运行记录口径

普通采集按板块生成结构化汇总，再合并为来源运行记录；重试使用相同统计字段。

| 字段 | 含义 |
| --- | --- |
| `discovered` | 普通采集为列表中符合日期条件、每板块按 tid 去重后的帖子数，包含已存在资源；重试为本轮选中的到期台账目标数。 |
| `requested` | 实际进入详情抓取的目标数，HTTP 内部重试不重复计数，不包含列表请求。 |
| `saved` | 本轮确认成功新增入库的条数；dry-run 或关闭 MongoDB 时为 0。批量写入部分失败时仅保留明确确认的写入数。 |
| `failed` | 列表请求/解析失败页数、详情失败目标数、保存失败目标数，以及其他阻断步骤失败次数之和；`stage_failures` 保留阶段明细。 |

`results` 保存各板块的计数、状态及错误说明，`list_requested/list_succeeded` 单独统计列表页。
详情全部失败会标记失败；部分结果成功时标记部分成功；正常没有新帖子时显示成功和真实的零值。
预先过滤掉的已存在资源不会掩盖本轮所有详情请求失败。通知投递结果不影响采集状态。
旧历史记录没有足够信息还原缺失统计时继续显示 `—`，新统计口径用于后续运行。
无需数据库结构迁移或新增配置。

## 项目结构

```text
scrapers/
  core/                 公共合同、配置、HTTP、引擎和运行模型
  infrastructure/       MongoDB/JSON 失败台账
  sources/
    javbee/             JavBee source、parser、repository
    sehuatang/          Sehuatang 来源入口
    x1080x/             来源、解析、仓储、HTTP、限流与补抓恢复
  registry.py           显式来源注册表
scripts/
  backfill_x1080x_codes.py  历史空番号补录
  diagnostics/          手工只读诊断脚本
notifications/          Telegram 内存队列
web/                    管理页与 API
util/                   配置、日志、MongoDB、番号识别与资源时钟
tests/                  解析、配置、重试、存储和恢复测试
main.py                 应用编排
run.py                  唯一 CLI 与 scheduler 入口
```

公共层和新增来源约束见 [scrapers/README.md](scrapers/README.md)，当前重构检查项见
[docs/CRAWLER_REFACTOR_TODO.md](docs/CRAWLER_REFACTOR_TODO.md)，限流与番号检查项见
[X1080X_RATE_LIMIT_TODO](docs/X1080X_RATE_LIMIT_TODO.md)、[X1080X_CODE_TODO](docs/X1080X_CODE_TODO.md)。

## 测试

推荐使用隔离运行器，避免测试读取本机数据库配置或通知凭据：

```powershell
python -B -m tests.run_offline
# 仅验证番号、补录和 x1080x 解析
python -B -m tests.run_offline tests.x1080x_code_tests tests.x1080x_tests
```

Mongo 集成测试默认跳过；可将 `CRAWLER_TEST_MONGO_URI` 指向专用的本机临时
MongoDB 后运行同一命令。测试仅允许 `127.0.0.1`，自行建立随机名称的测试数据库，
结束后清理。不要将它指向日常使用的数据库实例。

```powershell
python -m compileall -q main.py run.py scrapers util scripts tests
python run.py --mode health
```

离线测试通过不代表真实站点限流恢复、生产回填或镜像发布已经验证。

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
