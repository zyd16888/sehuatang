#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补录 x1080x_items 缺失的番号字段。

历史数据 code 为空可能来自旧的标题开头规则，或旧规则对字母/数字长度的限制。
x1080x 的番号通常在标题括号里，如「(麻豆傳媒)(m-331)标题」或「(香蕉視頻)(xjx-2)标题」。
本脚本用 resolve_x1080x_code（标题括号 → 磁链 dn → 开头规则）
重新识别并批量回填 code / code_normalized / code_source /
code_confidence，并同步资源指纹与变更时间；只处理 code 为空的文档。

用法（容器内运行: docker exec sehuatang-crawler python scripts/backfill_x1080x_codes.py ...）:
    python scripts/backfill_x1080x_codes.py --dry-run   # 只统计并抽样展示，不写库
    python scripts/backfill_x1080x_codes.py --dry-run --source-key 1013258
    python scripts/backfill_x1080x_codes.py             # 实际回填
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymongo

from util.javbee_code import normalize_code_key, resolve_x1080x_code
from util.resource_clock import X1080X_RESOURCE_FIELDS, fingerprint


EMPTY_CODE_QUERY = {"$or": [{"code": None}, {"code": ""}]}
CODE_FIELDS = ("code", "code_normalized", "code_source", "code_confidence")


def code_patch(document):
    if document.get("code") not in (None, ""):
        return None
    magnets = document.get("magnets") or (
        [document["magnet"]] if document.get("magnet") else []
    )
    resolution = resolve_x1080x_code(document.get("title"), magnets)
    if not resolution.code:
        return None
    return {
        "code": resolution.code,
        "code_normalized": normalize_code_key(resolution.code),
        "code_source": resolution.source,
        "code_confidence": resolution.confidence,
    }


def build_update(document, patch):
    return pymongo.UpdateOne(
        {
            "_id": document["_id"],
            **EMPTY_CODE_QUERY,
            # 读取后若采集进程修改过文档，就留待下次补录，避免写入过时的指纹。
            "resource_fingerprint": document.get("resource_fingerprint"),
            "updated_at": document.get("updated_at"),
        },
        {
            "$set": patch,
            "$currentDate": {"updated_at": True, "resource_updated_at": True},
        },
    )


def backfill_codes(collection, *, dry_run=False, batch_size=500,
                   source_keys=None, sample_limit=20, emit=print):
    if batch_size < 1 or sample_limit < 0:
        raise ValueError("batch_size 必须大于 0，sample_limit 不能小于 0")
    query = dict(EMPTY_CODE_QUERY)
    if source_keys:
        query["source_key"] = {"$in": list(source_keys)}
    total = collection.count_documents(query)
    emit(f"待处理: {total} 条（code 为空）")
    stats = {"total": total, "resolved": 0, "unresolved": 0,
             "matched": 0, "modified": 0, "skipped_concurrent": 0}
    operations = []

    def flush():
        if not operations:
            return
        result = collection.bulk_write(operations, ordered=False)
        stats["matched"] += result.matched_count
        stats["modified"] += result.modified_count
        stats["skipped_concurrent"] += len(operations) - result.matched_count
        operations.clear()
        emit(f"已回填 {stats['modified']} 条，并发更新跳过 {stats['skipped_concurrent']} 条")

    projection = {field: 1 for field in (
        *X1080X_RESOURCE_FIELDS, *CODE_FIELDS, "source_key", "resource_fingerprint",
        "updated_at", "resource_updated_at",
    )}
    with collection.find(query, projection) as cursor:
        for document in cursor:
            patch = code_patch(document)
            if patch is None:
                stats["unresolved"] += 1
                continue
            stats["resolved"] += 1
            patch["resource_fingerprint"] = fingerprint({**document, **patch}, X1080X_RESOURCE_FIELDS)
            if dry_run:
                if stats["resolved"] <= sample_limit:
                    changes = {
                        key: {"before": document.get(key), "after": value}
                        for key, value in patch.items()
                        if document.get(key) != value
                    }
                    for key in ("updated_at", "resource_updated_at"):
                        changes[key] = {"before": document.get(key), "after": "写入时的数据库时间"}
                    emit(json.dumps({"source_key": document.get("source_key"),
                                     "_id": str(document["_id"]), "changes": changes},
                                    ensure_ascii=False, default=str))
                continue
            operations.append(build_update(document, patch))
            if len(operations) >= batch_size:
                flush()
    flush()
    suffix = f"（dry-run 未写库，最多展示 {sample_limit} 条差异）" if dry_run else ""
    emit(f"完成: 识别 {stats['resolved']} 条，无法识别 {stats['unresolved']} 条，"
         f"实际修改 {stats['modified']} 条，并发更新跳过 {stats['skipped_concurrent']} 条{suffix}")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="补录 x1080x 缺失番号")
    parser.add_argument(
        "--dry-run", action="store_true", help="只统计并抽样展示，不写库"
    )
    parser.add_argument(
        "--batch-size", type=int, default=500, help="每批写库条数"
    )
    parser.add_argument("--source-key", action="append", help="仅处理指定帖子，可重复指定")
    parser.add_argument("--sample-limit", type=int, default=20, help="dry-run 展示差异的最大条数")
    args = parser.parse_args()
    if args.batch_size < 1 or args.sample_limit < 0:
        parser.error("batch-size 必须大于 0，sample-limit 不能小于 0")
    from util.mongo import get_x1080x_collection
    backfill_codes(get_x1080x_collection(), dry_run=args.dry_run,
                   batch_size=args.batch_size, source_keys=args.source_key,
                   sample_limit=args.sample_limit)


if __name__ == "__main__":
    main()
