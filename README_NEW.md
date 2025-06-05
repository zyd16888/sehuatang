# 🚀 数据抓取系统 (重构优化版)

一个高效、可扩展的论坛数据抓取系统，支持定时任务、Telegram Bot控制和多种数据存储方式。

## ✨ 新版本特性

### 🔄 重构亮点
- **面向对象设计**: 清晰的模块化架构，易于维护和扩展
- **统一异常处理**: 完善的错误处理和日志记录
- **配置管理优化**: 支持环境变量覆盖和多级配置
- **调度器统一**: 整合多种调度方式，支持Cron表达式
- **安全性提升**: 配置文件模板化，敏感信息保护

### 🆕 新增功能
- **多运行模式**: 支持调度器、单次执行、Bot、健康检查模式
- **优雅关闭**: 信号处理和资源清理
- **Docker优化**: 多阶段构建，非root用户，更小镜像
- **命令行工具**: 丰富的命令行参数支持
- **项目清理**: 自动清理临时文件和日志

## 📋 系统要求

- Python 3.11+
- Chrome/Chromium浏览器
- MongoDB 或 MySQL（可选）
- Docker（可选）

## 🚀 快速开始

### 1. 安装和配置

```bash
# 克隆项目
git clone <repository-url>
cd sehuatang

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或 venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt

# 配置系统
cp config/config.example.yaml config/config.yaml
# 编辑 config/config.yaml 填入真实配置
```

### 2. 运行系统

```bash
# 查看帮助
python run.py --help

# 运行调度器（默认模式）
python run.py

# 单次执行任务
python run.py --mode once

# 运行Telegram Bot
python run.py --mode bot

# 健康检查
python run.py --mode health

# 详细输出模式
python run.py --verbose
```

## 🐳 Docker部署

### 快速部署

```bash
# 使用docker-compose（推荐）
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down
```

### 手动构建

```bash
# 构建镜像
docker build -t sehuatang-crawler .

# 运行容器
docker run -d \
  --name sehuatang-crawler \
  -v $(pwd)/config:/app/config \
  -v $(pwd)/logs:/app/logs \
  -e SHT_MONGODB_ENABLE=true \
  sehuatang-crawler
```

## ⚙️ 配置说明

### 配置文件结构

```yaml
# MongoDB配置
mongodb:
  enable: true
  connection_string: "mongodb+srv://user:pass@cluster.mongodb.net/db"

# MySQL配置  
mysql:
  enable: false
  host: "localhost"
  port: 3306
  user: "root"
  password: "password"
  db: "sehuatang"

# 爬取配置
sehuatang:
  domain_name: "sehuatang.org"
  fid:
    103: "高清中文字幕"
    104: "素人有码系列"
  page_num: 5
  date: null  # 留空表示当天

# 代理配置
proxy:
  proxy_enable: true
  proxy_url: "http://127.0.0.1:7890"

# 通知配置
sendMessage:
  send_telegram_enable: true
  tg_bot_token: "your_bot_token"
  tg_chat_id: "your_chat_id"

# 调度配置
schedule:
  schedule_cron: "0 2 * * *"  # 每天凌晨2点执行

# 日志配置
logging:
  level: "INFO"
  max_file_size: 10485760  # 10MB
  backup_count: 5
  console_output: true

# 性能配置
performance:
  max_concurrent_requests: 10
  request_timeout: 30
  retry_attempts: 3
```

### 环境变量覆盖

```bash
# 数据库配置
export SHT_MONGODB_ENABLE=true
export SHT_MYSQL_ENABLE=false

# 代理配置
export SHT_PROXY_PROXY_ENABLE=false

# Telegram配置
export SHT_SENDMESSAGE_TG_BOT_TOKEN=your_token
export SHT_SENDMESSAGE_TG_CHAT_ID=your_chat_id

# 调度配置
export SHT_SCHEDULE_SCHEDULE_CRON="0 */6 * * *"
```

## 🤖 Telegram Bot命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/start` | 显示帮助信息 | `/start` |
| `/c <fid>` | 爬取指定板块 | `/c 103` |
| `/config` | 查看配置信息 | `/config` |
| `/status` | 查看任务状态 | `/status` |
| `/reload` | 重新加载配置 | `/reload` |

## 📁 项目架构

```
sehuatang/
├── 📁 config/                 # 配置文件
│   ├── config.yaml           # 主配置文件
│   └── config.example.yaml   # 配置模板
├── 📁 scrapers/              # 爬虫核心模块
│   ├── web_scraper.py        # 🕷️ 主爬虫协调器
│   ├── page_parser.py        # 📄 页面解析器
│   ├── data_processor.py     # 🔄 数据处理器
│   ├── data_manager.py       # 💾 数据管理器
│   └── notification_manager.py # 📢 通知管理器
├── 📁 util/                  # 工具模块
│   ├── config.py             # ⚙️ 配置管理
│   ├── read_config.py        # 📖 配置读取器
│   ├── log_util.py           # 📝 日志工具
│   ├── exceptions.py         # ⚠️ 异常处理
│   └── scheduler_manager.py  # ⏰ 调度器管理
├── 📁 tgbot/                 # Telegram Bot
├── 📁 scripts/               # 工具脚本
├── 📁 logs/                  # 日志文件
├── 🐍 main.py               # 主程序入口
├── 🚀 run.py                # 统一运行器
└── 📋 requirements.txt      # 依赖列表
```

## 🔧 开发和维护

### 代码质量

```bash
# 代码格式化
black .

# 代码检查
flake8 .

# 类型检查
mypy .

# 运行测试
python test_refactored.py
```

### 项目维护

```bash
# 清理临时文件
python scripts/cleanup.py

# 查看日志
tail -f logs/info.log

# 健康检查
python run.py --mode health
```

## 📊 监控和故障排除

### 日志分析

```bash
# 实时查看日志
tail -f logs/info.log

# 查看错误日志
tail -f logs/error.log

# 搜索特定错误
grep "ERROR" logs/*.log | tail -20
```

### 常见问题

1. **Chrome浏览器问题**
   - 确保Chrome已正确安装
   - 检查Chrome版本兼容性

2. **网络连接问题**
   - 验证代理配置
   - 检查防火墙设置
   - 测试目标网站可访问性

3. **配置问题**
   - 检查配置文件语法
   - 验证必要配置项
   - 使用健康检查模式诊断

## 🆚 版本对比

| 功能 | 旧版本 | 新版本 |
|------|--------|--------|
| 代码结构 | 单文件245行 | 模块化架构 |
| 错误处理 | 分散处理 | 统一异常管理 |
| 配置管理 | 简单读取 | 环境变量+多级配置 |
| 调度器 | 多个实现 | 统一管理器 |
| 运行模式 | 单一模式 | 多模式支持 |
| Docker | 基础镜像 | 优化镜像+安全性 |
| 日志系统 | 固定配置 | 可配置日志 |

## 🤝 贡献指南

1. Fork项目
2. 创建功能分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 创建Pull Request

## 📄 许可证

本项目采用MIT许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。

---

## 🙏 致谢

- 原项目: [SingleJohn/sehuatang](https://github.com/SingleJohn/sehuatang)
- 感谢所有贡献者和开源社区的支持！

**注意**: 请合理使用本工具，遵守目标网站的robots.txt和使用条款。
