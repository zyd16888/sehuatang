# 数据抓取模块重构说明

## 概述

本次重构将原本混乱的数据抓取代码重新组织为清晰的面向对象架构，提高了代码的可读性、可维护性和可扩展性。

## 架构设计

### 核心类

1. **WebScraper** (`web_scraper.py`)
   - 主要的爬虫协调器
   - 负责整个抓取流程的协调
   - 管理浏览器实例和其他组件
   - 支持上下文管理器，确保资源正确释放

2. **PageParser** (`page_parser.py`)
   - 页面解析器
   - 负责HTML页面的解析和数据提取
   - 分离了解析逻辑，便于维护和测试

3. **DataProcessor** (`data_processor.py`)
   - 数据处理器
   - 负责数据的合并、验证和清理
   - 统一处理数据转换逻辑

4. **DataManager** (`data_manager.py`)
   - 数据管理器
   - 统一管理MongoDB和MySQL操作
   - 提供数据比较和保存功能

5. **NotificationManager** (`notification_manager.py`)
   - 通知管理器
   - 统一管理Telegram等通知方式
   - 支持多种通知渠道扩展

## 主要改进

### 1. 代码结构优化
- **分离关注点**: 每个类只负责特定功能
- **面向对象设计**: 提高代码复用性和可维护性
- **模块化**: 便于单独测试和维护

### 2. 错误处理改进
- **统一异常处理**: 在各个层级都有适当的异常处理
- **资源管理**: 使用上下文管理器确保资源正确释放
- **日志记录**: 详细的日志记录便于调试

### 3. 性能优化
- **保持异步特性**: 继续使用asyncio进行并发处理
- **资源复用**: 浏览器实例在整个爬取过程中复用
- **批量处理**: 优化了数据处理流程

### 4. 可维护性提升
- **类型提示**: 添加了完整的类型注解
- **文档字符串**: 详细的函数和类说明
- **代码注释**: 关键逻辑都有清晰的注释

## 使用方式

### 基本用法
```python
from scrapers.web_scraper import WebScraper

# 使用上下文管理器
with WebScraper() as scraper:
    result = await scraper.crawl_forum_section(fid)
```

### 单独使用各个组件
```python
from scrapers.page_parser import PageParser
from scrapers.data_processor import DataProcessor

parser = PageParser()
processor = DataProcessor()

# 解析页面
info_list, tid_list = parser.parse_plate_page(html_content, date_time)

# 处理数据
cleaned_data = processor.clean_data(raw_data)
```

## 配置要求

重构后的代码继续使用原有的配置系统，无需修改配置文件。所有配置项都通过 `util.config` 模块获取。

## 兼容性

- 保持与原有drissio模块的兼容性
- 保持与现有数据库操作的兼容性
- 保持与Telegram通知系统的兼容性

## 扩展性

新的架构便于扩展：
- 添加新的解析器只需继承PageParser
- 添加新的数据库支持只需扩展DataManager
- 添加新的通知方式只需扩展NotificationManager

## 测试建议

建议为每个模块编写单元测试：
1. PageParser的HTML解析功能
2. DataProcessor的数据处理逻辑
3. DataManager的数据库操作
4. WebScraper的整体流程

这样可以确保重构后的代码质量和稳定性。
