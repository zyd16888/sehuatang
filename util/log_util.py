# -*- coding: utf-8 -*-
"""
优化的日志管理模块
支持配置文件控制和更灵活的日志设置
"""
import os
import sys
import time
import logging
import inspect
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

# 获取项目根目录
project_root = Path(__file__).parent.parent
logs_dir = project_root / "logs"

# 确保日志目录存在
logs_dir.mkdir(exist_ok=True)

# 默认日志配置
DEFAULT_LOG_CONFIG = {
    "level": "INFO",
    "max_file_size": 10 * 1024 * 1024,  # 10MB
    "backup_count": 5,
    "console_output": True
}

# 日志文件路径配置
LOG_FILES = {
    logging.NOTSET: logs_dir / "notset.log",
    logging.DEBUG: logs_dir / "debug.log",
    logging.INFO: logs_dir / "info.log",
    logging.WARNING: logs_dir / "warning.log",
    logging.ERROR: logs_dir / "error.log",
    logging.CRITICAL: logs_dir / "critical.log",
}


def get_log_config():
    """获取日志配置"""
    try:
        from util.read_config import get_config
        return get_config("logging", DEFAULT_LOG_CONFIG)
    except Exception:
        return DEFAULT_LOG_CONFIG


def create_handlers():
    """创建日志处理器"""
    config = get_log_config()
    handlers = {}

    for level, log_file in LOG_FILES.items():
        handler = RotatingFileHandler(
            str(log_file),
            maxBytes=config.get(
                "max_file_size", DEFAULT_LOG_CONFIG["max_file_size"]),
            backupCount=config.get(
                "backup_count", DEFAULT_LOG_CONFIG["backup_count"]),
            encoding="utf-8"
        )
        handlers[level] = handler

    return handlers


def create_console_handler():
    """创建控制台处理器"""
    config = get_log_config()

    if not config.get("console_output", True):
        return None

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    return handler


# 创建处理器
handlers = create_handlers()
console_handler = create_console_handler()


class TNLog(object):
    def printfNow(self):
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    def __init__(self, level=logging.NOTSET):
        self.__loggers = {}

        logLevels = handlers.keys()

        for level in logLevels:
            logger = logging.getLogger(str(level))

            # 如果不指定level，获得的handler似乎是同一个handler?

            logger.addHandler(handlers[level])

            # 添加标准输出处理器（只给INFO及以上级别的logger添加控制台输出）
            if level >= logging.INFO and console_handler:
                logger.addHandler(console_handler)

            logger.setLevel(level)

            # 防止日志重复输出
            logger.propagate = False

            self.__loggers.update({level: logger})

    def getLogMessage(self, level, message):
        frame_info = inspect.stack()[2]
        filename = frame_info.filename
        lineNo = frame_info.lineno
        functionName = frame_info.function

        """日志格式：[时间] [类型] [记录代码] 信息"""

        return "[%s] [%s] [%s - %s - %s] %s" % (
            self.printfNow(),
            level,
            filename,
            lineNo,
            functionName,
            message,
        )

    def info(self, message):
        message = self.getLogMessage("info", message)

        self.__loggers[logging.INFO].info(message)

    def error(self, message):
        message = self.getLogMessage("error", message)

        self.__loggers[logging.ERROR].error(message)

    def warning(self, message):
        message = self.getLogMessage("warning", message)

        self.__loggers[logging.WARNING].warning(message)

    def debug(self, message):
        message = self.getLogMessage("debug", message)

        self.__loggers[logging.DEBUG].debug(message)

    def critical(self, message):
        message = self.getLogMessage("critical", message)

        self.__loggers[logging.CRITICAL].critical(message)


log = TNLog()

if __name__ == "__main__":
    logger = TNLog()

    logger.debug("debug")
    logger.info("info")
    logger.warning("warning")
    logger.error("error")
    logger.critical("critical")
