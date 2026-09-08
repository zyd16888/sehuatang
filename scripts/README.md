# 运维脚本

脚本按用途保留在本目录，均应从项目根目录运行。

## 数据查询生成

- `extract_and_query.py`：读取 `scripts/list.txt` 中的“番号 + 标题”，生成跨
  collection 的 MongoDB 聚合查询。
- 输出写入 `scripts/generated/mongodb_priority_query.js`，该目录属于生成物，
  不提交 Git。

```powershell
python scripts/extract_and_query.py
```

## x1080x 空番号补录

`backfill_x1080x_codes.py` 复用当前抓取识别规则，只处理 `code` 缺失、null 或空字符串的记录。
支持标题括号及磁链 `dn` 中的 `M-331`、`XJX-2`、`MDSR-0010-1`、`MNSC-MB-112`，
以及 `thxp20230329_003` 这类站点资源编号。统一大写、下划线转连字符（后者输出
`THXP20230329-003`），保留前导零和分集后缀。标题和磁链共用完整匹配规则，不截取编号前半段。
单字母要求 `-` 或 `_` 分隔；纯日期括号、厂牌及括号里的 `H264` 等标记不按这些规则识别。
已有非空 `code` 不覆盖。

先预览指定帖子的字段差异：

```powershell
python scripts/backfill_x1080x_codes.py --dry-run --source-key 1013258
# 预览全部空 code，默认展示前 20 条；可调整展示数量
python scripts/backfill_x1080x_codes.py --dry-run --sample-limit 50
```

确定预览后，去掉 `--dry-run` 实际补录。**不带 `--dry-run` 会写库**，保持原有脚本用法：

```powershell
python scripts/backfill_x1080x_codes.py --source-key 1013258
# 不指定 source-key 时处理全部空 code；source-key 可以重复指定
python scripts/backfill_x1080x_codes.py --batch-size 500
```

补录只更新 `code`、`code_normalized`、`code_source`、`code_confidence`、
`resource_fingerprint`、`updated_at` 和 `resource_updated_at`，保持原有
`created_at`、`collected_at` 及其他资源内容。指纹字段与正常采集共用，变更时间由 MongoDB
写入时生成，使下游按资源更新时间读取时可以获取到此次补齐。

写入前仍要求 code 为空，且文档指纹与 `updated_at` 未变；如果读取后被其他任务修改，
该条跳过并计入“并发更新跳过”，可以重新执行。最终分别报告识别数、实际修改数和跳过数。

## 诊断

- `diagnostics/http_smoke.py`：只读验证 Sehuatang 列表页与一个详情页。
- `diagnostics/source_smoke.py`：只读执行少量列表、详情解析，不写数据库。
- `diagnostics/x1080x_probe.py`：只读探测 x1080x 镜像域名连通性与 CF 挑战。

诊断脚本会真实访问站点，只在需要排查网络或解析问题时手工运行。
