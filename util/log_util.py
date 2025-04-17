# -*- coding: utf-8 -*-
import os
import sys
import time
import logging
import inspect
from logging.handlers import RotatingFileHandler

dir = os.path.dirname(__file__)
dir_time = time.strftime("%Y-%m-%d", time.localtime())

parent_dir = os.path.dirname(dir)


if os.path.exists(parent_dir + "/logs") is False:
    os.mkdir(parent_dir + "/logs")


handlers = {
    logging.NOTSET: os.path.join(parent_dir, "logs/notset.log"),
    logging.DEBUG: os.path.join(parent_dir, "logs/debug.log"),
    logging.INFO: os.path.join(parent_dir, "logs/info.log"),
    logging.WARNING: os.path.join(parent_dir, "logs/warning.log"),
    logging.ERROR: os.path.join(parent_dir, "logs/error.log"),
    logging.CRITICAL: os.path.join(parent_dir, "logs/critical.log"),
}


# 创建标准输出流处理器
stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setLevel(logging.INFO)  # 可以调整级别，默认INFO级别以上才输出到控制台
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
stdout_handler.setFormatter(formatter)

def createHandlers():
    logLevels = handlers.keys()

    for level in logLevels:
        path = os.path.abspath(handlers[level])
        handlers[level] = RotatingFileHandler(
            path, maxBytes=10000000, backupCount=2, encoding="utf-8"
        )


# 加载模块时创建全局变量

createHandlers()


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
            if level >= logging.INFO:
                logger.addHandler(stdout_handler)

            logger.setLevel(level)

            # 防止日志重复输出
            logger.propagate = False

            self.__loggers.update({level: logger})

    def getLogMessage(self, level, message):
        frame, filename, lineNo, functionName, code, unknowField = inspect.stack()[2]

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
