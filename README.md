# 色花堂BT区页面爬取脚本

---

Fork from [SingleJohn/sehuatang](https://github.com/SingleJohn/sehuatang) , 自用修改

---
v2 版本使用说明
- 增加了tgbot远程控制功能（`bot.py` 需要保持后台运行，具体效果可参考：[tgbot](effect%20picture/tgbot)）
  - 需要在 `tgbot/models/users_model.py` 中设置管理员的id
- 配置文件中的fid配置项格式修改，冒号后面为tg的发送标签
- 修正了一些问题

其他功能请参照下方说明（与v1版一致）

1. 使用方式
   1. `pip install -r requirements.txt` 安装依赖
   2. 将 `config_bak.yaml` 文件改为 `config.yaml`
   3. 修改配置项，运行 `main.py` 即可

2. 目前实现的功能
   - 按板块抓取指定日期的信息
   - 将数据存入 MongoDB
   - 判断数据是否存在，存在则跳过
   - 异步并发
   - ~~推送到企业微信~~
   - 推送到telegram（带封面图），效果可见telegram频道：[sehuatang crawler](https://t.me/sehuatang_crawler)
   - 将数据存入 mysql (2022-07-13 新增)
     - 需要使用[mysql_init.sql](mysql_init/mysql_init.sql)对数据库进行初始化（建库、建表）
     - 需要在配置文件中配置相关配置项
     - 增加了控制项控制是否存入mysql或mongodb
   - 支持docker方式运行(未经完全测试)
     - `git clone` 代码后，修改配置文件，运行 `docker-compose up -d` 启动服务
     - `docker-compose.yml`文件默认包含了mongodb和mysql服务，自行按需修改 
     - `config_docker.yaml` 为docker运行示例配置文件
     - 不提供预构建的`image`，使用默认的`docker-compose.yml`文件启动服务时会自行在本地构建

3. 当前功能基本够用，可能会改进的地方
   - 导出数据到 Excel


4. 常见问题
   1. cloudflare 防护绕过
      - 可以使用 cloudflare 的 workers ，对网站进行反向代理，然后爬取域名设置为 workers 的域名即可
      - workers 代码参考 ：[production.js](util/production.js)
   2. ~~telegram 频道条目不全~~(已修复)
      - telegram api 限制单条消息的大小，如图片过大，则会导致发送的消息过大，报错--()
      - 完整数据在 mongodb 中


5. 其他
   - `main` 、 `dev` 分支都是过渡版本(应该不会再维护了)，最新版本为`async`分支
   - 一个公共的mongodb库(权限只读)
      - `mongodb+srv://readonly:cS9NSuiJ1ebHnUL0@cluster0.8mosa.mongodb.net/Cluster0?retryWrites=true&w=majority`
   - 有使用问题可以提issues
   - 为避免原站压力过大，建议直接订阅上面的 telegram 频道，或使用上方提供的公共数据库查看数据
   - 预览图可见：[effect picture](effect%20picture)
