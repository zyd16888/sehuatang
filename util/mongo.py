# 连接mongodb

import pymongo
from util.log_util import log
from util.config import date, mongodb_host, mongodb_port, mongodb_conn_str, mongodb_use_conn_str

if mongodb_use_conn_str:
    client = pymongo.MongoClient(mongodb_conn_str)
else:
    client = pymongo.MongoClient(mongodb_host, mongodb_port)

send_context_str = "本次抓取的结果如下：\n"

db = client.sehuatang


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
        collection.insert_many(data_list)
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
