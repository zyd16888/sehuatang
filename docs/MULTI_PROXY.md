# 通用多代理会话

Sehuatang、Javbee、x1080x 共用同一套会话池。一个代理地址（含端口）就是一条固定线路；程序不探测公网 IP、不管理 Clash 节点，也不自动切换代理。每个端口对应一个共享验证上下文，独立保存 Cookie、UA 和冷却状态；可配置多个常驻工作线程，每个线程拥有独立的 HTTP Session。无代理时使用一个直连组，同样受每组并发上限控制。

## 配置

下面是公共默认配置示例，可合并进现有 `crawler` 配置。`sources.<source>` 中的同名字段优先于 `defaults`，因此需同时检查已有来源里的 `proxy.enabled`、`urls`、并发与浏览器设置。

```yaml
crawler:
  defaults:
    concurrency: 4
    per_proxy_concurrency: 1
    http:
      timeout: 30
      impersonate: firefox147
      proxy:
        enabled: true
        urls:
          - http://proxy-host:17891
          - http://proxy-host:17892
          - http://proxy-host:17893
          - http://proxy-host:17894
    rate_limit:
      site_interval_seconds: 0
      cooldown_seconds: 60
      max_cooldown_seconds: 900
    challenge:
      provider: byparr
      flaresolverr_url: http://byparr:8191/v1
  sources:
    sehuatang:
      enabled: true
      concurrency: 12
      per_proxy_concurrency: 3
      rate_limit:
        min_interval_seconds: 0.5
    javbee:
      enabled: true
      concurrency: 8
      per_proxy_concurrency: 2
      rate_limit:
        min_interval_seconds: 1
    x1080x:
      enabled: true
      concurrency: 4
      per_proxy_concurrency: 1
      rate_limit:
        min_interval_seconds: 2
```

| 配置 | 含义 |
| --- | --- |
| `http.proxy.urls` | 非空时优先于旧 `url`；重复地址拒绝启动 |
| `http.proxy.enabled: false` | 使用一个直连组，不使用代理列表；仍受每组并发限制 |
| `concurrency` | 每个来源的逻辑抓取任务并发上限，不是 Session 数量或每秒速率 |
| `per_proxy_concurrency` | 同一代理端口内的并发上限；正整数，默认 1 |
| `rate_limit.min_interval_seconds` | 每个端口的所有 worker 共享的请求启动间隔，覆盖列表、详情、HTTP 重试和过盾 |
| `rate_limit.site_interval_seconds` | 来源所有会话共享的额外最小间隔；0 表示不增加间隔 |
| `cooldown_seconds` / `max_cooldown_seconds` | 限流冷却与指数退避上限；服务端 `Retry-After` 更长时优先遵守服务端 |
| `challenge.provider` | `byparr` 或 `flaresolverr`，必须与实际部署服务一致 |

上述并发和间隔是待实测的起始值，不代表已验证的站点阈值。不配置间隔时，x1080x 默认 2 秒，其他来源默认 0 秒。

有效并发上限为 `min(concurrency, 代理端口数 × per_proxy_concurrency)`。例如 4 个端口、每端口 3、来源总并发 12，最多同时执行 12 个逻辑抓取任务；总并发改成 6 后最多 6 个。无代理时端口数按 1 计算。

来源总额度覆盖一次抓取内的普通重试、过盾及间隔等待；补抓的站点限流冷却在额度外等待。每端口的在途任务不会超过自己的 worker 数。三个站点的额度分别计算；上面配置在三个站点同时运行时合计上限为 24，不是整个应用共享 12。浏览器内部子资源请求不计为独立爬虫任务。

配置新增 `CRAWLER_<SOURCE>_PER_PROXY_CONCURRENCY` 环境变量。未设置该字段的旧配置保持每端口串行。

仍支持旧 `http.proxy.url`、旧配置与 `CRAWLER_<SOURCE>_PROXY_URL`。多代理环境变量为 `CRAWLER_<SOURCE>_PROXY_URLS`，值必须是 JSON 数组，例如 `["http://proxy-host:17891","http://proxy-host:17892"]`。显式环境变量 `PROXY_URL` 会清除继承的列表；同时设置 `PROXY_URLS` 时使用列表。代理开关仍需为 true。

容器中的 `127.0.0.1` 指向容器自身。代理地址必须同时能被采集器和过盾服务访问，且固定端口的出口由运维侧保证。

## CF 与站点验证

Byparr 通过 `X-Proxy-Server` 传代理，FlareSolverr 通过 JSON `proxy.url` 传代理。正文和过盾始终绑定当前会话的代理地址。同一个过盾端点串行执行，不同端点互不阻塞。同端口普通请求使用独立 Cookie 快照；新请求在该端口验证期间等待，验证完成后复用最新状态。旧请求带回的 Cookie 不覆盖新一代验证状态。同一批并发 CF 请求也会复用失败的验证结果，后续独立请求仍可重新验证。Cookie 仅保存在内存中，保留 domain/path/expires；程序重启后可能需要重新验证。普通 403/503 不凭状态码判成 CF，需有挑战页面特征。

浏览器指纹应与服务匹配：Byparr 的 Firefox 系列可使用 `firefox147`；FlareSolverr 的 Chrome 服务使用对应的 Chrome 指纹。代码复用过盾响应的 UA，但不会自动推断或切换浏览器指纹。这里复用的是采集器 HTTP Session，不假设 Byparr 支持 FlareSolverr 的持久浏览器 Session API。

Sehuatang 的 R18 `safeid` 转换保留为来源插件，其他来源无需复制。验证未通过的页面不交给正文解析。

## 限流与恢复

一个端口命中限流后，其所有 worker 共同冷却，其他端口仍可执行。增量请求在所有端口冷却时返回 `rate_limited`，不增加单帖失败台账次数。分页/年度补抓由公共 `BackfillHttpClient` 在原端口的原 worker 重试原目标；不换端口、不把同一目标广播到其他代理。

补抓详情按完成顺序解析并送入有界写入队列；独立写入线程按数量或时间提交，一条线路冷却不会拖住已有结果的落库。任务队列按端口 worker 总数限制在途量，公共引擎仍按小批次发现详情。停止会唤醒冷却和退避等待，已开始的网络调用受请求超时约束，Session 在拥有它的工作线程中关闭。

## 通用批量保存

三个来源共用批量写入器。请求前的数据库过滤遵循各来源既有刷新策略；抓取/解析线程只向有界队列提交结果，写入线程集中执行资源保存、失败记账和批量清理。

```yaml
crawler:
  defaults:
    storage:
      batch_size: 10
      flush_interval_seconds: 1
      queue_capacity: 100
      retry_attempts: 3
      retry_delay_seconds: 1
```

也可以在 `crawler.sources.<source>.storage` 覆盖。保存配置独立于 HTTP 配置，不改变代理会话身份或清空 CF Cookie。

- `batch_size` 按待持久化目标计数，含成功记录和普通失败；队列和正在提交的批次按帖子 key 去重。
- 缓冲达到 10 个目标、首条在缓冲中等待 1 秒、或本页生产结束时提交，先到者生效。时间刷新由写入线程独立触发，不等待下一个 HTTP 结果；这不是数据库必须在 1 秒内完成写入的保证。
- `queue_capacity` 限制排队目标数；此外写入器最多持有一个 `batch_size` 大小的批次。队列满时阻塞结果生产，避免数据库变慢导致结果无限积压。
- 资源写入的瞬态连接异常最多尝试 3 次（含首次），退避 1、2 秒，重试原幂等批次。连接中断后无法确认的首次写入可能已成功，统计以获得确认的保存返回值为准。
- 失败台账写入会累加次数，不自动重放；资源保存失败或失败台账写入失败会中止本次流水线，不推进本页检查点。失败台账清理是批量调用，失败时保留原记录供以后恢复。
- 完成队列入队不代表已保存。本页会等待尾批写入确认后再返回并更新检查点；只有整个页结束后才请求下一页。
- 正常停止会停止 HTTP 任务并冲刷已入队数据；强杀/断电无法保证内存缓冲已写入，但检查点不会越过未确认页，续跑时重新发现该页并跳过已入库记录。
- `dry-run` 只抓取/解析，不提交写入目标，不更新资源、台账或检查点。

日志现在区分 `写入批次完成`、`本页处理完成` 和 `分页补抓结束`。批次日志包含记录数、失败数、`persist_ms`（资源与台账操作耗时）、剩余排队数及刷新原因。运行汇总包含 `write_batches`、`persist_ms`、`write_queue_wait_ms`，用于判断瓶颈是写入还是队列等待。

## 去重、锁与进度

- 来源运行锁覆盖定时、手动、失败重试、分页补抓和年度补抓。锁位于 `data/locks`，同机不同进程/容器必须共享这个目录。它不是跨机器的分布式租约。锁文件存在不代表正在运行，不要删除正在使用的锁文件。
- 公共引擎按来源内帖子 key 去重；HTTP 批次重复 URL 共用结果，并发调用合并同一在途请求。不同标题、番号或磁链不作为帖子身份。
- x1080x/Javbee 使用唯一 `source_key`；尚未完成资源写入的占位记录不会被当成已完成。Sehuatang 在原有分板块集合内使用唯一 `tid` 和 `$setOnInsert`，已存在记录不覆盖。
- Sehuatang 首次写入会建立 `uniq_tid`；若历史数据已存在重复 tid，索引创建会失败并停止本次保存，程序不会自动清理历史数据。应先只读检查重复记录，再单独处理。
- 详情失败只有可靠写入失败台账后才允许页进度前移。写库或台账故障会停止当前页；失败台账清理失败可在后续重试，不会丢失数据。
- 列表页顺序推进，因此不会越过未完成页。已完成记录和失败台账都与代理端口无关，重启后可继续复用。
- 分页检查点优先存到 MongoDB `crawl_checkpoints`；未启用 MongoDB 时保存在 `data/page_backfill_progress.json`。JSON 检查点与失败台账的整个读改写过程都有跨进程文件锁。
- 新分页检查点按来源、页区间、站点和排序/板块入口标识隔离，分区分别保存。改变页区间即视为另一个任务。旧无范围 JSON 检查点不自动继承，升级后可以从原范围重扫，已入库帖子会被跳过；旧文件不删除。
- 年度补抓保留 `data/backfill_progress.json` 中的年份/板块键与旧恢复语义，并加文件锁。它不会迁移到分页检查点。
- `--dry-run` 不写资源、失败台账或检查点，仍会发起网络请求并使用运行互斥锁。

原有命令不变：

```bash
python run.py backfill-pages --source x1080x --typeid 5479 --end-page 200 --resume
python run.py backfill-pages --source sehuatang --fid 103 --end-page 200 --resume
python run.py crawl --source javbee
```

Javbee 保留其现有增量/刷新入口；公共会话及恢复包装器可供新的来源/补抓入口复用，未新增 Javbee 的页区间业务命令。

## 验证

离线行为测试：`python -m unittest tests.multi_proxy_tests tests.batch_storage_tests`；回归：`python -m unittest discover -s tests -p '*tests.py'`。

检查点列表的页面逻辑回归：`node --test tests/backfill_progress_ui_tests.mjs`，覆盖新旧键与按来源筛选。

离线验证不证明真实站点提速。部署后应先对比单端口/双端口的成功新增量、限流比例、过盾频率及资源使用，再调整线路数量和速率。
