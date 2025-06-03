import os

# 定义视频文件夹路径
videos_dir = r"V:\bban"

file_size = 20 * 1024 * 1024

# 遍历每个视频文件夹
for subdir, dirs, files in os.walk(videos_dir):
    for file in files:
        # 拼接视频文件的完整路径
        file_path = os.path.join(subdir, file)
        # print(file_path)

        # 检查视频文件大小是否小于20M
        if os.path.getsize(file_path) < file_size:
            # 删除小于20M的视频文件
            os.remove(file_path)
            print(f"Deleted file: {file_path}")
