"""将旧 MySQL javbee 表幂等迁移到 MongoDB javbee_items。"""

import argparse
import getpass
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scrapers.javbee_parser import JavbeeParser
from util.javbee_code import normalize_code_key, resolve_javbee_code
from util.log_util import log
from util.mongo import ensure_javbee_indexes, save_javbee_items


MYSQL_COLUMNS = (
    "id, date, url, title, code, img, size, magnet, torrent, "
    "complete, ised2k, publish"
)


def normalize_date(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    text = str(value or "").strip()
    for date_format in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, date_format).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def row_to_document(row: Dict[str, Any]) -> Dict[str, Any]:
    url = str(row.get("url") or "").strip()
    date_value = normalize_date(row.get("date"))
    if not url or not date_value:
        raise ValueError(f"旧数据缺少有效 date/url: id={row.get('id')}")

    source_key = JavbeeParser.source_key_from_url(url)
    if not source_key:
        raise ValueError(f"旧数据无法提取 source_key: id={row.get('id')} url={url}")

    title = str(row.get("title") or "").strip()
    legacy_code = str(row.get("code") or "").strip() or None
    title_resolution = resolve_javbee_code(None, title)
    return {
        "source_key": source_key,
        "legacy_mysql_id": int(row["id"]),
        "date": date_value,
        "url": url,
        "title": title,
        "code": legacy_code,
        "code_normalized": normalize_code_key(legacy_code),
        "code_source": "legacy_mysql" if legacy_code else None,
        "code_confidence": "high" if legacy_code else "unknown",
        "code_rule": "existing" if legacy_code else "unresolved",
        "title_kind": title_resolution.title_kind,
        "img": str(row.get("img") or "").strip(),
        "size": str(row.get("size") or "").strip(),
        "magnet": row.get("magnet") or None,
        "torrent": row.get("torrent") or None,
        "complete": int(row.get("complete") or 0),
        "ised2k": int(row.get("ised2k") or 0),
        "publish": int(row.get("publish") or 0),
        "migrated_at": datetime.now(timezone.utc),
    }


def fetch_batch(connection, last_id: int, batch_size: int) -> Iterable[Dict[str, Any]]:
    sql = f"SELECT {MYSQL_COLUMNS} FROM `javbee` WHERE id > %s ORDER BY id LIMIT %s"
    with connection.cursor() as cursor:
        cursor.execute(sql, (last_id, batch_size))
        return cursor.fetchall()


def migrate(connection, batch_size: int, resume_after_id: int, limit: int, dry_run: bool):
    if not dry_run:
        ensure_javbee_indexes()

    summary = {
        "read": 0,
        "valid": 0,
        "skipped": 0,
        "matched": 0,
        "modified": 0,
        "upserted": 0,
        "last_id": resume_after_id,
    }

    while limit <= 0 or summary["read"] < limit:
        remaining = batch_size if limit <= 0 else min(batch_size, limit - summary["read"])
        rows = list(fetch_batch(connection, summary["last_id"], remaining))
        if not rows:
            break

        documents = []
        for row in rows:
            summary["read"] += 1
            summary["last_id"] = int(row["id"])
            try:
                documents.append(row_to_document(row))
                summary["valid"] += 1
            except (TypeError, ValueError) as exc:
                summary["skipped"] += 1
                log.warning(f"跳过无效 MySQL 记录: {exc}")

        if documents and not dry_run:
            result = save_javbee_items(documents, preserve_existing=True)
            for key in ("matched", "modified", "upserted"):
                summary[key] += result[key]

        log.info(
            f"Javbee 迁移进度: read={summary['read']} valid={summary['valid']} "
            f"skipped={summary['skipped']} last_id={summary['last_id']} "
            f"dry_run={dry_run}"
        )

    return summary


def create_argument_parser():
    parser = argparse.ArgumentParser(description="迁移 MySQL javbee 表到 MongoDB javbee_items")
    parser.add_argument("--mysql-host", default="127.0.0.1")
    parser.add_argument("--mysql-port", type=int, default=3306)
    parser.add_argument("--mysql-user", default="root")
    parser.add_argument("--mysql-database", default="18R")
    parser.add_argument(
        "--mysql-password-env",
        default="JAVBEE_MYSQL_PASSWORD",
        help="保存 MySQL 密码的环境变量名",
    )
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--resume-after-id", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0, help="0 表示不限制")
    parser.add_argument("--dry-run", action="store_true", help="只读取并转换，不写 MongoDB")
    return parser


def main():
    args = create_argument_parser().parse_args()
    if args.batch_size <= 0:
        raise SystemExit("--batch-size 必须大于 0")
    if args.resume_after_id < 0 or args.limit < 0:
        raise SystemExit("--resume-after-id 和 --limit 不能小于 0")

    password = os.getenv(args.mysql_password_env)
    if password is None:
        password = getpass.getpass(f"MySQL password ({args.mysql_user}@{args.mysql_host}): ")

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
        summary = migrate(
            connection,
            batch_size=args.batch_size,
            resume_after_id=args.resume_after_id,
            limit=args.limit,
            dry_run=args.dry_run,
        )
    finally:
        connection.close()

    log.info(f"Javbee MySQL -> MongoDB 迁移结束: {summary}")
    print(summary)


if __name__ == "__main__":
    main()
