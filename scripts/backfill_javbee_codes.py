"""以 MySQL 原始空 code 记录为基准，修复 MongoDB javbee_items。"""

import argparse
import getpass
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pymongo
import pymysql

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from util.javbee_code import normalize_code_key, resolve_javbee_code
from util.log_util import log
from util.mongo import get_javbee_collection


CODE_PROJECTION = {
    "legacy_mysql_id": 1,
    "source_key": 1,
    "code": 1,
    "code_normalized": 1,
    "code_source": 1,
    "code_candidate": 1,
    "code_candidate_normalized": 1,
    "code_confidence": 1,
    "code_rule": 1,
    "title_kind": 1,
}


def fetch_missing_code_batch(connection, last_id: int, batch_size: int):
    sql = """
        SELECT id, title
        FROM `javbee`
        WHERE id > %s AND NULLIF(TRIM(code), '') IS NULL
        ORDER BY id
        LIMIT %s
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, (last_id, batch_size))
        return cursor.fetchall()


def backfill_codes(
    mysql_connection,
    collection,
    apply: bool = False,
    include_medium: bool = False,
    batch_size: int = 1000,
    resume_after_id: int = 0,
    limit: int = 0,
):
    summary = Counter(last_id=resume_after_id)
    allowed_confidence = {"high", "medium"} if include_medium else {"high"}

    while limit <= 0 or summary["scanned"] < limit:
        remaining = (
            batch_size
            if limit <= 0
            else min(batch_size, limit - summary["scanned"])
        )
        rows = list(
            fetch_missing_code_batch(
                mysql_connection,
                summary["last_id"],
                remaining,
            )
        )
        if not rows:
            break

        legacy_ids = [int(row["id"]) for row in rows]
        mongo_rows = collection.find(
            {"legacy_mysql_id": {"$in": legacy_ids}},
            CODE_PROJECTION,
        )
        mongo_by_legacy_id = {
            int(document["legacy_mysql_id"]): document
            for document in mongo_rows
        }
        summary["mongo_found"] += len(mongo_by_legacy_id)
        operations = []
        for row in rows:
            legacy_id = int(row["id"])
            summary["scanned"] += 1
            summary["last_id"] = legacy_id
            resolution = resolve_javbee_code(None, row.get("title"))
            summary[f"rule_{resolution.rule}"] += 1
            summary[f"confidence_{resolution.confidence}"] += 1

            current = mongo_by_legacy_id.get(legacy_id)
            if current is None:
                summary["mongo_missing"] += 1
                if not apply:
                    _print_change(
                        legacy_id,
                        None,
                        resolution,
                        "mongo_missing",
                        None,
                    )
                continue
            if current.get("code_source") not in (None, "title"):
                summary["protected"] += 1
                continue

            eligible = bool(
                resolution.code and resolution.confidence in allowed_confidence
            )
            desired_fields = _desired_fields(resolution, eligible)
            if not eligible:
                summary["skipped"] += 1
            else:
                summary["eligible"] += 1
            if current.get("code") and desired_fields["code"] is None:
                summary["would_clear"] += 1

            changed_fields = {
                key: value
                for key, value in desired_fields.items()
                if current.get(key) != value
            }
            if not changed_fields:
                summary["unchanged"] += 1
                continue

            summary["proposed"] += 1
            if not apply:
                _print_change(
                    legacy_id,
                    current,
                    resolution,
                    _change_action(current, desired_fields),
                    desired_fields,
                )
                continue

            operations.append(
                pymongo.UpdateOne(
                    {
                        "legacy_mysql_id": legacy_id,
                        "$or": [
                            {"code_source": {"$exists": False}},
                            {"code_source": None},
                            {"code_source": "title"},
                        ],
                    },
                    {
                        "$set": {
                            **desired_fields,
                            "code_completed_at": datetime.now(timezone.utc),
                        }
                    },
                )
            )

        if apply and operations:
            result = collection.bulk_write(operations, ordered=False)
            summary["mongo_matched"] += result.matched_count
            summary["updated"] += result.modified_count

        if apply:
            log.info(
                f"Javbee code 补全进度: scanned={summary['scanned']} "
                f"proposed={summary['proposed']} updated={summary['updated']} "
                f"last_id={summary['last_id']}"
            )

    result = dict(sorted(summary.items()))
    if apply:
        log.info(f"Javbee code 补全结束: apply=True summary={result}")
    return result


def _desired_fields(resolution, eligible):
    if eligible:
        code_fields = {
            "code": resolution.code,
            "code_normalized": normalize_code_key(resolution.code),
            "code_source": resolution.source,
            "code_candidate": None,
            "code_candidate_normalized": None,
        }
    else:
        code_fields = {
            "code": None,
            "code_normalized": None,
            "code_source": None,
            "code_candidate": resolution.code,
            "code_candidate_normalized": normalize_code_key(resolution.code),
        }
    return {
        **code_fields,
        "code_confidence": resolution.confidence,
        "code_rule": resolution.rule,
        "title_kind": resolution.title_kind,
    }


def _change_action(current, desired_fields):
    if desired_fields["code"]:
        if current.get("code") == desired_fields["code"]:
            return "annotate_code"
        return "replace_code"
    if desired_fields["code_candidate"]:
        return "clear_to_candidate" if current.get("code") else "record_candidate"
    return "clear_unresolved" if current.get("code") else "mark_unresolved"


def _print_change(legacy_id, current, resolution, action, desired_fields):
    changes = {}
    if current and desired_fields:
        changes = {
            key: {"from": current.get(key), "to": value}
            for key, value in desired_fields.items()
            if current.get(key) != value
        }
    change = {
        "legacy_mysql_id": legacy_id,
        "source_key": current.get("source_key") if current else None,
        "current_code": current.get("code") if current else None,
        "action": action,
        "new_code": desired_fields.get("code") if desired_fields else None,
        "code_candidate": (
            desired_fields.get("code_candidate") if desired_fields else resolution.code
        ),
        "confidence": resolution.confidence,
        "rule": resolution.rule,
        "changes": changes,
    }
    print(json.dumps(change, ensure_ascii=False, sort_keys=True))


def create_argument_parser():
    parser = argparse.ArgumentParser(
        description="按 MySQL 原始空 code 记录补全 MongoDB javbee_items"
    )
    parser.add_argument("--mysql-host", default=os.getenv("MYSQL_HOST", "127.0.0.1"))
    parser.add_argument(
        "--mysql-port",
        type=int,
        default=int(os.getenv("MYSQL_PORT", "3306")),
    )
    parser.add_argument("--mysql-user", default=os.getenv("MYSQL_USER", "root"))
    parser.add_argument(
        "--mysql-database",
        default=os.getenv("MYSQL_DATABASE", "18R"),
    )
    parser.add_argument(
        "--mysql-password-env",
        default="MYSQL_PASSWORD",
        help="保存 MySQL 密码的环境变量名",
    )
    parser.add_argument("--apply", action="store_true", help="实际写入；默认只输出统计")
    parser.add_argument(
        "--include-medium",
        action="store_true",
        help="同时写入中置信度候选；默认只处理高置信度",
    )
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--resume-after-id", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0, help="0 表示不限制")
    return parser


def main():
    args = create_argument_parser().parse_args()
    if args.batch_size <= 0:
        raise SystemExit("--batch-size 必须大于 0")
    if args.resume_after_id < 0 or args.limit < 0:
        raise SystemExit("--resume-after-id 和 --limit 不能小于 0")

    password = os.getenv(args.mysql_password_env)
    if password is None:
        password = getpass.getpass(
            f"MySQL password ({args.mysql_user}@{args.mysql_host}): "
        )

    connection = pymysql.connect(
        host=args.mysql_host,
        port=args.mysql_port,
        user=args.mysql_user,
        password=password,
        database=args.mysql_database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )
    try:
        summary = backfill_codes(
            connection,
            get_javbee_collection(),
            apply=args.apply,
            include_medium=args.include_medium,
            batch_size=args.batch_size,
            resume_after_id=args.resume_after_id,
            limit=args.limit,
        )
    finally:
        connection.close()

    print(summary)


if __name__ == "__main__":
    main()
