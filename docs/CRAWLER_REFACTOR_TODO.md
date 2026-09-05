# 多来源爬虫重构 TODO

目标：把 Sehuatang 与 JavBee 收敛为同一套爬虫运行体系，同时保留各站的解析、反爬、数据模型和业务策略。

## 阶段 1：行为基线与公共基础层

- [x] 盘点 CLI、Docker、scheduler、HTTP、配置、存储、通知和日志链路
- [x] 为严格配置合并、HTTP 重试分类、运行结果模型补充单元测试
- [x] 建立 `scrapers/core` 公共契约和数据模型
- [x] 实现每来源独立的 HTTP、代理、超时、重试和并发配置
- [x] 保持旧配置可读取，并对二级模糊键读取进行兼容迁移

## 阶段 2：迁移 JavBee

- [x] 将 JavBee 拆成 source、parser、repository
- [x] 使用公共 HTTP 和批量抓取能力
- [x] 支持 `new_only`、`refresh_all`、`stale_after` 刷新策略
- [x] 输出 `success`、`partial_success`、`failed` 运行状态
- [x] 将终态失败写入失败台账，并支持后续恢复

## 阶段 3：迁移 Sehuatang

- [x] 将 `WebScraper` 收敛为 Sehuatang source adapter
- [x] 使用公共 HTTP、重试、代理和运行汇总
- [x] 保留 R18 safeid 与可选 FlareSolverr 处理
- [x] 保留板块抓取、Mongo/MySQL 写入和 Telegram 通知
- [x] 保留年度二分定位、批量保存及失败时不推进 checkpoint

## 阶段 4：入口与调度

- [x] 建立显式 source registry
- [x] 支持 `crawl --source sehuatang|javbee|all`
- [x] 保留现有 `--mode once|javbee|backfill|bot|health` 兼容入口
- [x] 为各来源建立独立 cron job 和 `max_instances=1` 约束
- [x] 统一本地、Docker 和 scheduler 启动链路

## 阶段 5：恢复、文档与验收

- [x] 增加 `retry-failed` 与 `dry-run` 运行能力
- [x] 统一运行日志字段并对代理凭据等敏感值脱敏
- [x] 更新示例配置、架构文档和 CLI 文档
- [x] 运行单元测试、编译检查、CLI smoke test 和 Git 范围检查
- [ ] 只在明确授权后进行真实站点和数据库写入验收

## 硬性兼容约束

2026-09-06 资源服务联动已批准并实现：

- [x] 新采集记录增加 `collected_at`，原站发布日期保持独立。
- [x] `resource_updated_at` 仅随资源内容或下载载荷改变；操作刷新仍使用原 `updated_at`。
- [x] Javbee 首次成功写入载荷时确定收录时间；重复采集、迁入历史和中断写入不伪造首次收录。
- [x] 写入路径创建 `(collected_at, _id)`、`(resource_updated_at, _id)` 索引。
- [x] 43 项爬虫单元测试及临时 MongoDB 7.0.24 原生写入检查通过；没有修改线上数据库。

上线顺序：先更新爬虫，再更新独立 Worker，最后更新 subtitleGeneration 并重建开发阶段的每日任务。
历史记录缺失的收录时间不回填；可按原站发布日期单独补采。集合与资源身份不变。

- 不迁移或合并现有 MongoDB collection schema。
- 不改变 JavBee `source_key` 唯一键和现有字段语义。
- 不改变 Sehuatang 的板块 collection 映射。
- 年度补抓存在详情失败时不得推进对应 checkpoint。
- 站点配置必须使用完整路径；旧配置只作为迁移期兼容输入。
- 每个来源独立持有 HTTP session、cookie、代理、超时和限速状态。
