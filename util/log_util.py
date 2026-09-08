"""统一日志配置与兼容日志门面。"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Mapping, Optional


project_root = Path(__file__).parent.parent
logs_dir = project_root / "logs"
logs_dir.mkdir(exist_ok=True)

DEFAULT_LOG_CONFIG = {
    "level": "INFO",
    "max_file_size": 10 * 1024 * 1024,
    "backup_count": 5,
    "console_output": True,
}

LOG_MODULES = {
    "sehuatang": "Sehuatang",
    "javbee": "JavBee",
    "x1080x": "x1080x",
    "telegram": "Telegram",
    "scheduler": "调度",
    "database": "数据库与存储",
    "system": "系统",
    "unclassified": "未分类（历史日志）",
}


def module_for_logger(caller: str) -> str:
    """按代码所属组件归类，不根据日志正文关键词猜测模块。"""
    if caller.startswith("notifications.") or caller in {"util.sendTelegram", "scrapers.notification_manager"}:
        return "telegram"
    if caller == "util.mongo" or caller.startswith("scrapers.infrastructure."):
        return "database"
    if caller == "util.scheduler_manager":
        return "scheduler"
    for source in ("sehuatang", "javbee", "x1080x"):
        if caller.startswith(f"scrapers.sources.{source}.") or caller in {
            f"scrapers.{source}_scraper", f"scrapers.{source}_parser",
        }:
            return source
    if caller in {"scrapers.web_scraper", "scrapers.page_parser", "scrapers.http_client",
                  "scrapers.data_manager", "scrapers.data_processor"}:
        return "sehuatang"
    return "system"


def get_log_config():
    try:
        from util.read_config import get_config

        configured = get_config("logging", {}) or {}
        return {**DEFAULT_LOG_CONFIG, **configured}
    except Exception:
        return dict(DEFAULT_LOG_CONFIG)


def _configure_stdout_utf8() -> None:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def _build_logger() -> logging.Logger:
    config = get_log_config()
    logger = logging.getLogger("crawler")
    if getattr(logger, "_crawler_configured", False):
        return logger

    level_name = str(config.get("level", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)
    logger.propagate = False
    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - [module=%(component)s] %(message)s",
        defaults={"component": "system"},
    )

    app_handler = RotatingFileHandler(
        logs_dir / "crawler.log",
        maxBytes=int(config["max_file_size"]),
        backupCount=int(config["backup_count"]),
        encoding="utf-8",
    )
    app_handler.setLevel(level)
    app_handler.setFormatter(formatter)
    logger.addHandler(app_handler)

    error_handler = RotatingFileHandler(
        logs_dir / "error.log",
        maxBytes=int(config["max_file_size"]),
        backupCount=int(config["backup_count"]),
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    logger.addHandler(error_handler)

    if config.get("console_output", True):
        _configure_stdout_utf8()
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    logger._crawler_configured = True
    return logger


class TNLog:
    """兼容旧调用方式，并支持绑定统一上下文字段。"""

    def __init__(
        self,
        level: int = logging.NOTSET,
        context: Optional[Mapping[str, Any]] = None,
    ):
        self._logger = _build_logger()
        self._context = dict(context or {})

    def bind(self, **context: Any) -> "TNLog":
        return TNLog(context={**self._context, **context})

    def set_level(self, level: Any) -> None:
        resolved = (
            getattr(logging, str(level).upper(), logging.INFO)
            if isinstance(level, str)
            else int(level)
        )
        self._logger.setLevel(resolved)
        for handler in self._logger.handlers:
            if not (
                isinstance(handler, RotatingFileHandler)
                and Path(handler.baseFilename).name == "error.log"
            ):
                handler.setLevel(resolved)

    def _message(self, message: Any) -> str:
        text = str(message)
        if not self._context:
            return text
        fields = " ".join(
            f"{key}={value}"
            for key, value in self._context.items()
            if value is not None and key != "module"
        )
        return f"{text} {fields}" if fields else text

    def _emit(self, level: int, message: Any, *, exc_info=False) -> None:
        caller = sys._getframe(2).f_globals.get("__name__", "")
        component = self._context.get("module") or module_for_logger(caller)
        if component not in LOG_MODULES:
            component = "system"
        self._logger.log(level, self._message(message), extra={"component": component},
                         exc_info=exc_info, stacklevel=3)

    def debug(self, message: Any) -> None:
        self._emit(logging.DEBUG, message)

    def info(self, message: Any) -> None:
        self._emit(logging.INFO, message)

    def warning(self, message: Any) -> None:
        self._emit(logging.WARNING, message)

    def error(self, message: Any) -> None:
        self._emit(logging.ERROR, message)

    def critical(self, message: Any) -> None:
        self._emit(logging.CRITICAL, message)

    def exception(self, message: Any) -> None:
        self._emit(logging.ERROR, message, exc_info=True)


log = TNLog()
