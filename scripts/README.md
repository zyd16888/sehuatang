# 运维脚本

脚本按用途保留在本目录，均应从项目根目录运行。

## JavBee 数据维护

- `migrate_javbee_mysql_to_mongodb.py`：旧 MySQL `javbee` 表幂等迁移到 MongoDB。
- `backfill_javbee_codes.py`：基于旧 MySQL 标题补全或纠正 MongoDB 番号。

这两个脚本默认提供只读或 `--dry-run` 路径。正式写入前先用小 `--limit` 抽样，
数据库密码只通过环境变量或交互输入传递。

## 数据查询生成

- `extract_and_query.py`：读取 `scripts/list.txt` 中的“番号 + 标题”，生成跨
  collection 的 MongoDB 聚合查询。
- 输出写入 `scripts/generated/mongodb_priority_query.js`，该目录属于生成物，
  不提交 Git。

```powershell
python scripts/extract_and_query.py
```

## 诊断

- `diagnostics/http_smoke.py`：只读验证 Sehuatang 列表页与一个详情页。
- `diagnostics/source_smoke.py`：只读执行少量列表、详情解析，不写数据库。

诊断脚本会真实访问站点，只在需要排查网络或解析问题时手工运行。
