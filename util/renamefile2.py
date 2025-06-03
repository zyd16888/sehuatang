import os
import shutil
import pymongo
from log_util import log


class FileInfo:
    def __init__(self, file_name, file_path, code_number, file_path_name):
        self.file_name = file_name
        self.file_path = file_path
        self.code_number = code_number
        self.file_path_name = file_path_name


def get_file_info(path):
    file_info_list = []
    for root, dirs, files in os.walk(path):
        for file in files:
            if file.endswith(".mp4"):
                file_name = file.split("-C")[0].split("-UC")[0]
                code_number = file_name
                file_info_list.append(
                    FileInfo(file_name, root, code_number, os.path.join(root, file))
                )
    return file_info_list


def get_mongo_info():
    client = pymongo.MongoClient(
        "mongodb+srv://readonly:cS9NSuiJ1ebHnUL0@cluster0.8mosa.mongodb.net/Cluster0?retryWrites=true&w=majority"
    )
    db = client.sehuatang
    collection = db.hd_chinese_subtitles
    return collection.find({}, {"_id": 0, "number": 1, "title": 1}).sort(
        "_id", pymongo.DESCENDING
    )


def get_dirs(path):
    # 获取所有文件夹
    dir_info_list = []
    root, dirs, _ = os.walk(path).__next__()
    for dir_name in dirs:
        dir_info = {"dir_name": dir_name, "dir_path": root}
        dir_info["code_number"] = dir_name.split("-C")[0].split("-UC")[0]
        # dir_path = os.path.join(root, dir_name)
        dir_info_list.append(dir_info)
    return dir_info_list


def main():
    path = r"X:\local\uctest"

    file_info_list = get_file_info(path)
    res = get_mongo_info()

    file_number_to_info = {
        file_info.code_number.lower(): file_info for file_info in file_info_list
    }
    dir_number_to_info = {
        dir_info["code_number"].lower(): dir_info for dir_info in get_dirs(path)
    }

    for mongo_entry in res:
        file_info = file_number_to_info.get(mongo_entry["number"].lower())
        if file_info:
            new_name = f"{mongo_entry['number'].lower()}-C {mongo_entry['title']}.mp4"
            new_path = os.path.join(file_info.file_path, new_name)
            if not os.path.exists(new_path):
                try:
                    shutil.move(file_info.file_path_name, new_path)
                    print(
                        f"Renamed and moved: {file_info.file_path_name} --> {new_path}"
                    )
                    log.info(
                        f"Renamed and moved: {file_info.file_path_name} --> {new_path}"
                    )
                except Exception as e:
                    log.error(
                        f"Failed to rename and move: {file_info.file_path_name} --> {new_path}\n{e}"
                    )

    for file_info in file_info_list:
        dir_info = dir_number_to_info.get(file_info.code_number.lower())
        if dir_info:
            new_dir_path = os.path.join(
                dir_info["dir_path"],
                os.path.basename(file_info.file_path_name).split(".")[0],
            )
            if not os.path.exists(new_dir_path):
                try:
                    shutil.move(file_info.file_path, new_dir_path)
                    print(
                        f"Renamed directory: {file_info.file_path} --> {new_dir_path}"
                    )
                    log.info(
                        f"Renamed directory: {file_info.file_path} --> {new_dir_path}"
                    )
                except Exception as e:
                    log.error(
                        f"Failed to rename directory: {file_info.file_path} --> {new_dir_path}\n{e}"
                    )


if __name__ == "__main__":
    main()
