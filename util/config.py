import time
from util.read_config import get_config


mongodb = get_config("mongodb", {}) or {}
mongodb_enable = get_config("mongodb.enable", mongodb.get("enable"))
mongodb_host = get_config("mongodb.db_host", mongodb.get("db_host"))
mongodb_port = get_config("mongodb.db_port", mongodb.get("db_port"))
mongodb_conn_str = get_config(
    "mongodb.connection_string",
    mongodb.get("connection_string"),
)
mongodb_use_conn_str = get_config(
    "mongodb.use_conn_str",
    mongodb.get("use_conn_str"),
)

mysql = get_config("mysql", {}) or {}
mysql_enable = get_config("mysql.enable", mysql.get("enable"))
mysql_host = get_config("mysql.host", mysql.get("host"))
mysql_port = get_config("mysql.port", mysql.get("port"))
mysql_user = get_config("mysql.user", mysql.get("user"))
mysql_passwd = get_config("mysql.password", mysql.get("password"))
mysql_db = get_config("mysql.db", mysql.get("db"))

domain = get_config("domain_name")
cookie = get_config("cookie")
fid_json = get_config("fid")
fid_list = [key for key in fid_json]
fid_value_list = [fid_json[key] for key in fid_json]
page_num = get_config("page_num")

proxy = get_config("proxy")
proxy_url = proxy.get("proxy_url")
proxy_enable = proxy.get("proxy_enable")

if proxy_enable:
    proxy = proxy_url
else:
    proxy = None

send_msg = get_config("sendMessage", {}) or {}
tg_enable = get_config(
    "sendMessage.send_telegram_enable",
    send_msg.get("send_telegram_enable"),
)
tg_bot_token = get_config("sendMessage.tg_bot_token", send_msg.get("tg_bot_token"))
tg_chat_id = get_config("sendMessage.tg_chat_id", send_msg.get("tg_chat_id"))

image_proxy_url = get_config(
    "sendMessage.image_proxy_url",
    send_msg.get("image_proxy_url"),
)


def date():
    date_time = get_config("date")
    if date_time is None:
        date_time = time.strftime("%Y-%m-%d", time.localtime())
    else:
        date_time = date_time.__str__()
    return date_time


schedule_time = get_config("schedule_time")


schedule_cron = get_config("schedule_cron")
