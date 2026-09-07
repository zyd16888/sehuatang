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

## 诊断

- `diagnostics/http_smoke.py`：只读验证 Sehuatang 列表页与一个详情页。
- `diagnostics/source_smoke.py`：只读执行少量列表、详情解析，不写数据库。

诊断脚本会真实访问站点，只在需要排查网络或解析问题时手工运行。
