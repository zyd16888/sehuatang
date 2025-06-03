import os
import pymongo
from log_util import log


VIDEO_PATH = "Z:/pikpak/My Pack"


def get_mongo_info() -> pymongo.cursor.Cursor:
    """
    获取 MongoDB 中的数据

    :return: pymongo.cursor.Cursor 游标对象
    """
    try:
        client = pymongo.MongoClient(
            "mongodb+srv://readonly:cS9NSuiJ1ebHnUL0@cluster0.8mosa.mongodb.net/Cluster0?retryWrites=true&w=majority"
        )
        db = client.sehuatang
        collection = db.hd_chinese_subtitles
        res = collection.find({}, {"_id": 0, "number": 1, "title": 1}).sort(
            "_id", pymongo.DESCENDING
        )
        return res
    except pymongo.errors.PyMongoError as e:
        log.error(f"MongoDB查询出错: {e}")


def rename_folder(old_name: str, new_name: str) -> None:
    """
    重命名文件夹

    :param old_name: str 原文件夹名称
    :param new_name: str 新文件夹名称
    """
    # log.info(f"Renaming folder: {old_name} -> {new_name}")
    # print(f"Renaming folder: {old_name} -> {new_name}")
    try:
        os.rename(
            os.path.join(VIDEO_PATH, old_name), os.path.join(VIDEO_PATH, new_name)
        )
        print(f"重命名文件夹成功：'{old_name}' to '{new_name}'")
        log.info(f"重命名文件夹成功：{old_name} -> {new_name}")
    except OSError as e:
        log.error(f"重命名文件夹失败：{old_name} -> {new_name}，错误信息：{e}")


def construct_new_folder_name(number: str, title: str) -> str:
    """
    构造新的文件夹名称
    :param number: 视频编号
    :param title: 视频标题
    :return: 新的文件夹名称
    """
    # 将不能用作文件名的字符替换为空格
    for c in r'<>:"/\|?*':
        title = title.replace(c, " ")
    # 构造新文件夹名称
    new_folder_name = number + "-C " + title
    return new_folder_name


def rename_folders() -> None:
    """
    重命名所有视频文件夹名称
    """
    log.info("Start renaming folders")
    try:
        for result in get_mongo_info():
            number = result["number"].lower()
            title = result["title"].lower()
            # 将所有文件夹名称转换为小写，进行匹配
            for folder_name in os.listdir(VIDEO_PATH):
                if (
                    folder_name.lower().endswith("-c")
                    and folder_name[:-2].lower() == number
                ):
                    # 去掉末尾的 '-C'
                    # folder_name = folder_name[:-2]
                    # 构造新文件夹名称
                    new_folder_name = construct_new_folder_name(number, title)
                    # 重命名文件夹
                    rename_folder(folder_name, new_folder_name)
                    break
    except Exception as e:
        log.error(f"批量重命名文件夹出错: {e}")
    log.info("Finish renaming folders")


def main():
    rename_folders()


if __name__ == "__main__":
    main()
