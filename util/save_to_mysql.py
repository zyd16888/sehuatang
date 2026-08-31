import pymysql

from util.log_util import TNLog
from util.read_config import get_config

log = TNLog()


class SaveToMysql:
    def __init__(self):
        self.config = get_config("mysql")
        self.host = self.config["host"]
        self.port = self.config["port"]
        self.user = self.config["user"]
        self.password = self.config["password"]
        self.db = self.config["db"]
        self.conn = pymysql.connect(host=self.host, port=self.port, user=self.user, password=self.password, db=self.db)
        self.cursor = self.conn.cursor()

    def show_table(self):
        res = self.cursor.execute("show tables")
        print(res)

    def save_data_batch(self, data_list, fid):
        # 批量插入数据
        sql = "insert into " + 'sht_data' + "(magnet, number, title, post_time, date, tid, fid) values (%s, %s, %s, " \
                                            "%s, %s, %s, %s) "
        values = []
        for data in data_list:
            values.append(
                (data["magnet"], data["number"], data["title"], data["post_time"], data['date'], data["tid"], fid))
        self.cursor.executemany(sql, values)
        self.conn.commit()

    def save_data(self, data_list, fid):
        if len(data_list) == 0:
            log.info("mysql 未存入新数据")
            return
        try:
            for data in data_list:
                sql = "insert into " + 'sht_data' + "(magnet, number, title, post_time, date, tid, fid) values (%s, " \
                                                    "%s, %s, %s, %s, %s, %s) "
                self.cursor.execute(sql, (
                    data["magnet"], data["number"], data["title"], data["post_time"], data['date'], data["tid"], fid))
                id = self.cursor.lastrowid
                # 图片数据关联写入sht_images表
                sql = "insert into " + 'sht_images' + " (sht_data_id, image_url) values (%s, %s)"
                for image_url in data["img"]:
                    self.cursor.execute(sql, (id, image_url))
            self.conn.commit()
            log.info("mysql 保存数据成功, 共存入数据库 %s 条" % len(data_list))
        except Exception as e:
            log.error("mysql 写入失败：%s " % e)
            self.conn.rollback()

    def compare_tid(self, tid_list, fid, info_list):
        id_list = self.find_tid(fid, tid_list)
        tid_list_new = []
        for i in tid_list:
            if i not in id_list:
                tid_list_new.append(i)

        info_list_new = []
        for info in info_list:
            if info["tid"] in tid_list_new:
                info_list_new.append(info)

        return tid_list_new, info_list_new

    def find_tid(self, fid, tid_list=None):
        params = [fid]
        sql = "select tid from sht_data where fid = %s"
        if tid_list:
            placeholders = ", ".join(["%s"] * len(tid_list))
            sql += f" and tid in ({placeholders})"
            params.extend(tid_list)
        self.cursor.execute(sql, params)
        res = self.cursor.fetchall()
        tid_list = [str(i[0]) for i in res]
        return tid_list

    def filter_data(self, data_list, fid):
        id_list = self.find_tid(fid, [item["tid"] for item in data_list])
        data_list_new = []
        for data in data_list:
            if data["tid"] not in id_list:
                data_list_new.append(data)
        return data_list_new

    def close(self):
        self.cursor.close()
        self.conn.close()


if __name__ == '__main__':
    SaveToMysql().find_tid(104)
