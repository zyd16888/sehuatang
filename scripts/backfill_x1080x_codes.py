#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补录 x1080x_items 缺失的番号字段。

历史数据 code 为空是因为旧识别规则只匹配标题开头，而 x1080x 的
番号在标题括号里（如「(杏吧傳媒)(xb-5441)(20260828)标题」）。
本脚本用 resolve_x1080x_code（标题括号 → 磁链 dn → 开头规则）
重新识别并批量回填 code / code_normalized / code_source /
code_confidence，只处理 code 为空的文档，重复执行是安全的。

用法（容器内运行: docker exec sehuatang-crawler python scripts/backfill_x1080x_codes.py ...）:
    python scripts/backfill_x1080x_codes.py --dry-run   # 只统计并抽样展示，不写库
    python scripts/backfill_x1080x_codes.py             # 实际回填
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymongo

from util.javbee_code import normalize_code_key, resolve_x1080x_code
from util.mongo import get_x1080x_collection


def main() -> None:
    parser = argparse.ArgumentParser(description="补录 x1080x 缺失番号")
    parser.add_argument(
        "--dry-run", action="store_true", help="只统计并抽样展示，不写库"
    )
    parser.add_argument(
        "--batch-size", type=int, default=500, help="每批写库条数"
    )
    args = parser.parse_args()

    collection = get_x1080x_collection()
    query = {
        "$or": [
            {"code": None},
            {"code": ""},
            {"code": {"$exists": False}},
        ]
    }
    total = collection.count_documents(query)
    print(f"待处理: {total} 条（code 为空）")

    resolved = 0
    unresolved = 0
    sample_shown = 0
    operations = []
    cursor = collection.find(
        query, {"_id": 1, "title": 1, "magnet": 1, "magnets": 1}
    )
    for doc in cursor:
        magnets = doc.get("magnets") or (
            [doc["magnet"]] if doc.get("magnet") else []
        )
        resolution = resolve_x1080x_code(doc.get("title"), magnets)
        if not resolution.code:
            unresolved += 1
            continue
        resolved += 1

        if args.dry_run:
            if sample_shown < 20:
                sample_shown += 1
                print(
                    f"  {resolution.code:<16} rule={resolution.rule:<10} "
                    f"<- {str(doc.get('title', ''))[:60]}"
                )
            continue

        operations.append(
            pymongo.UpdateOne(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "code": resolution.code,
                        "code_normalized": normalize_code_key(resolution.code),
                        "code_source": resolution.source,
                        "code_confidence": resolution.confidence,
                    }
                },
            )
        )
        if len(operations) >= args.batch_size:
            collection.bulk_write(operations, ordered=False)
            print(f"已回填 {resolved} 条…")
            operations = []

    if operations:
        collection.bulk_write(operations, ordered=False)

    suffix = "（dry-run 未写库）" if args.dry_run else ""
    print(f"完成: 识别 {resolved} 条, 无法识别 {unresolved} 条{suffix}")


if __name__ == "__main__":
    main()
