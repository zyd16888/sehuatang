# 连接mongodb

import pymongo
from datetime import datetime, timedelta, timezone
from util.log_util import log
from util.config import date, mongodb_host, mongodb_port, mongodb_conn_str, mongodb_use_conn_str
from util.resource_clock import collected_document, resource_update_pipeline

if mongodb_use_conn_str:
    client = pymongo.MongoClient(mongodb_conn_str)
else:
    client = pymongo.MongoClient(mongodb_host, mongodb_port)

send_context_str = "本次抓取的结果如下：\n"

db = client.sehuatang

JAVBEE_COLLECTION_NAME = "javbee_items"
CRAWL_FAILURE_COLLECTION_NAME = "crawl_failures"


# 枚举，通过fid获取板块名称
def get_plate_name(fid):
    if fid == 103:
        return "hd_chinese_subtitles"
    elif fid == 104:
        return "vegan_with_mosaic"
    elif fid == 37:
        return "asia_mosaic_originate"
    elif fid == 36:
        return "asia_codeless_originate"
    elif fid == 39:
        return "anime_originate"
    elif fid == 160:
        return "vr_video"
    elif fid == 151:
        return "4k_video"
    elif fid == 2:
        return "domestic_original"
    elif fid == 38:
        return "EU_US_no_mosaic"
    elif fid == 107:
        return "three_levels_photo"
    elif fid == 152:
        return "korean_anchorman"
    else:
        return "other"


# 保存数据(已存在的数据不保存)
def save_data(data_list, fid):
    collection_name = get_plate_name(fid)
    collection = db[collection_name]
    if len(data_list) > 0:
        ensure_resource_clock_indexes(collection)
        now = datetime.now(timezone.utc)
        collection.insert_many([collected_document(item, now) for item in data_list])
        send_context(data_list, collection_name)
        log.info("mongo 保存数据成功, 共存入数据库{}条".format(len(data_list)))
    else:
        global send_context_str
        send_context_str += "\n " + collection_name + ":\n"
        send_context_str += "没有新数据\n"
        log.info("mongodb 未存入新数据")


def filter_data(data_list, fid):     # 过滤数据
    collection_name = get_plate_name(fid)
    tid_list = find_existing_tids(
        collection_name,
        [item["tid"] for item in data_list],
    )
    data_list_new = compare_data(data_list, tid_list)
    return data_list_new


# 查询数据, 拿到已存在的数据id
def find_data_tid(collection_name, date):
    """
    :param data: 字典
    """
    collection = db[collection_name]
    # 构造查询条件
    query = {"post_time": {"$regex": "^" + date}}
    log.info("mongodb 查询条件: {}, collection_name: {}".format(query, collection_name))
    # 查询数据, 返回指定的字段
    res = collection.find(query, {"_id": 0, "date": 1, "tid": 1})
    # 将查询结果中的id提取出来
    tid_list = []
    for i in res:
        tid_list.append(i["tid"])
    return tid_list


def find_existing_tids(collection_name, tid_list):
    """只查询候选列表中已存在的 tid，不依赖抓取日期。"""
    if not tid_list:
        return []

    normalized_tids = {str(tid) for tid in tid_list}
    query_values = list(normalized_tids)
    query_values.extend(
        int(tid) for tid in normalized_tids if tid.isdigit()
    )

    collection = db[collection_name]
    res = collection.find(
        {"tid": {"$in": query_values}},
        {"_id": 0, "tid": 1},
    )
    return [str(item["tid"]) for item in res]


# 比对tid，将不存在的信息筛选出来
def compare_data(data_list, id_list):
    """
    :param data: 字典
    """
    data_list_new = []
    for i in data_list:
        if i["tid"] not in id_list:
            data_list_new.append(i)
    return data_list_new


# 筛选不存在的tids
def compare_tid(tid_list, fid, info_list):
    collection_name = get_plate_name(fid)
    id_list = find_existing_tids(collection_name, tid_list)
    log.info("collection_name: {}".format(collection_name))
    log.info(f"mongodb 查询到{len(id_list)}条数据, id为：{' '.join(id_list)}")

    tid_list_new = []
    for i in tid_list:
        if i not in id_list:
            tid_list_new.append(i)

    temp = []
    for item in tid_list_new:
        if item not in temp:
            temp.append(item)

    info_list_new = []
    for info in info_list:
        if info["tid"] in temp:
            info_list_new.append(info)

    temp2 = []
    for item in info_list_new:
        if item not in temp2:
            temp2.append(item)

    return temp, temp2  # 返回去重后的结果


def send_context(data_list, collection_name):
    global send_context_str

    send_context_str += "\n " + collection_name + ":\n"

    for i in data_list:
        # send_context_str.join(i["number"] + " " + i["title"] + "\n")
        send_context_str += i["number"] + " " + i["title"] + "\n"
    # send_context_str.join(len(data_list).__str__() + "条\n")
    send_context_str += len(data_list).__str__() + "条\n"


def get_send_context():
    global send_context_str
    return send_context_str


def get_javbee_collection():
    return db[JAVBEE_COLLECTION_NAME]


def ensure_resource_clock_indexes(collection):
    collection.create_index([("collected_at", pymongo.ASCENDING), ("_id", pymongo.ASCENDING)],
                            name="idx_resource_collected")
    collection.create_index([("resource_updated_at", pymongo.ASCENDING), ("_id", pymongo.ASCENDING)],
                            name="idx_resource_updated")


def ensure_javbee_indexes(collection=None):
    """创建 javbee_items 所需索引；重复调用是安全的。"""
    if collection is None:
        collection = get_javbee_collection()

    ensure_resource_clock_indexes(collection)

    collection.create_index(
        [("source_key", pymongo.ASCENDING)],
        unique=True,
        name="uniq_source_key",
    )
    collection.create_index([("url", pymongo.ASCENDING)], name="idx_url")
    collection.create_index(
        [("code", pymongo.ASCENDING), ("date", pymongo.DESCENDING)],
        name="idx_code_date",
    )
    collection.create_index(
        [("code_normalized", pymongo.ASCENDING), ("date", pymongo.DESCENDING)],
        name="idx_code_normalized_date",
    )
    collection.create_index(
        [("date", pymongo.DESCENDING)],
        name="idx_date",
    )


def find_existing_javbee_urls(urls, collection=None):
    if not urls:
        return set()
    if collection is None:
        collection = get_javbee_collection()

    rows = collection.find(
        {"url": {"$in": list(dict.fromkeys(urls))}},
        {"_id": 0, "url": 1},
    )
    return {row["url"] for row in rows if row.get("url")}


def find_stale_javbee_urls(urls, cutoff, collection=None):
    """返回需要按时间刷新、或尚无刷新时间的 Javbee URL。"""
    if not urls:
        return set()
    if collection is None:
        collection = get_javbee_collection()

    rows = collection.find(
        {
            "url": {"$in": list(dict.fromkeys(urls))},
            "$or": [
                {"updated_at": {"$lte": cutoff}},
                {"updated_at": {"$exists": False}},
            ],
        },
        {"_id": 0, "url": 1},
    )
    return {row["url"] for row in rows if row.get("url")}


def get_crawl_failure_collection():
    return db[CRAWL_FAILURE_COLLECTION_NAME]


def ensure_crawl_failure_indexes(collection=None):
    if collection is None:
        collection = get_crawl_failure_collection()
    collection.create_index(
        [
            ("source", pymongo.ASCENDING),
            ("source_key", pymongo.ASCENDING),
            ("stage", pymongo.ASCENDING),
        ],
        unique=True,
        name="uniq_source_key_stage",
    )
    collection.create_index(
        [("next_retry_at", pymongo.ASCENDING)],
        name="idx_next_retry_at",
    )


def record_crawl_failures(failures, collection=None):
    if not failures:
        return
    if collection is None:
        collection = get_crawl_failure_collection()
    ensure_crawl_failure_indexes(collection)

    now = datetime.now(timezone.utc)
    operations = []
    for failure in failures:
        attempts = max(1, int(failure.get("attempts", 1)))
        retry_delay_minutes = min(24 * 60, 5 * (2 ** (attempts - 1)))
        operations.append(
            pymongo.UpdateOne(
                {
                    "source": failure["source"],
                    "source_key": failure["source_key"],
                    "stage": failure["stage"],
                },
                {
                    "$set": {
                        "url": failure["url"],
                        "attempts": attempts,
                        "error_type": failure.get("error_type") or "unknown",
                        "error_message": str(failure.get("error_message") or "")[:1000],
                        "metadata": dict(failure.get("metadata") or {}),
                        "last_failed_at": now,
                        "next_retry_at": now + timedelta(minutes=retry_delay_minutes),
                        "resolved_at": None,
                    },
                    "$inc": {"failure_count": 1},
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
            )
        )
    collection.bulk_write(operations, ordered=False)


def clear_crawl_failures(source, source_keys, collection=None):
    if not source_keys:
        return
    if collection is None:
        collection = get_crawl_failure_collection()
    collection.delete_many(
        {
            "source": source,
            "source_key": {"$in": list(dict.fromkeys(source_keys))},
        }
    )


def find_due_crawl_failures(source, now=None, collection=None, limit=500):
    if collection is None:
        collection = get_crawl_failure_collection()
    now = now or datetime.now(timezone.utc)
    rows = collection.find(
        {
            "source": source,
            "resolved_at": None,
            "next_retry_at": {"$lte": now},
        },
        {
            "_id": 0,
            "source_key": 1,
            "url": 1,
            "stage": 1,
            "metadata": 1,
            "next_retry_at": 1,
        },
    ).sort("next_retry_at", pymongo.ASCENDING).limit(max(1, int(limit)))
    return list(rows)


def save_javbee_items(data_list, collection=None, preserve_existing=False):
    """按 Javbee 详情页标识幂等写入 javbee_items。"""
    if not data_list:
        return {"processed": 0, "matched": 0, "modified": 0, "upserted": 0}
    if collection is None:
        collection = get_javbee_collection()

    ensure_javbee_indexes(collection)
    now = datetime.now(timezone.utc)
    operations = []
    for item in data_list:
        if not item.get("url"):
            raise ValueError("javbee 数据缺少 url")
        if not item.get("source_key"):
            raise ValueError(f"javbee 数据缺少 source_key: {item['url']}")
        if not item.get("date"):
            raise ValueError(f"javbee 数据缺少 date: {item['url']}")

        document = dict(item)
        if preserve_existing:
            metadata_fields = {
                "legacy_mysql_id",
                "complete",
                "ised2k",
                "publish",
                "migrated_at",
            }
            set_fields = {
                key: value
                for key, value in document.items()
                if key in metadata_fields
            }
            set_fields["updated_at"] = now
            insert_fields = {
                key: value
                for key, value in document.items()
                if key not in metadata_fields
            }
            insert_fields["created_at"] = now
        else:
            # Insert provenance first. Until the following atomic payload update
            # succeeds, the row has no resource_updated_at and is not incremental.
            insert_fields = {"created_at": now, "resource_collection_pending": True}
            for field in ("complete", "ised2k", "publish"):
                if field not in document:
                    insert_fields[field] = 0
            operations.append(pymongo.UpdateOne(
                {"source_key": document["source_key"]},
                {"$setOnInsert": insert_fields}, upsert=True))
            operations.append(pymongo.UpdateOne(
                {"source_key": document["source_key"]}, resource_update_pipeline(document)))
            continue
        operations.append(
            pymongo.UpdateOne(
                {"source_key": document["source_key"]},
                {
                    "$set": set_fields,
                    "$setOnInsert": insert_fields,
                },
                upsert=True,
            )
        )

    result = collection.bulk_write(operations, ordered=True)
    summary = {
        "processed": len(data_list),
        "matched": result.matched_count if preserve_existing else max(0, result.matched_count - len(data_list)),
        "modified": result.modified_count if preserve_existing else max(0, result.modified_count - result.upserted_count),
        "upserted": result.upserted_count,
    }
    log.info(
        "MongoDB 保存 javbee_items 完成: "
        f"processed={summary['processed']} matched={summary['matched']} "
        f"modified={summary['modified']} upserted={summary['upserted']}"
    )
    return summary
