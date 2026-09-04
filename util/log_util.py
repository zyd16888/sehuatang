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
        "%(asctime)s - %(levelname)s - %(message)s"
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
            if value is not None
        )
        return f"{text} {fields}" if fields else text

    def debug(self, message: Any) -> None:
        self._logger.debug(self._message(message))

    def info(self, message: Any) -> None:
        self._logger.info(self._message(message))

    def warning(self, message: Any) -> None:
        self._logger.warning(self._message(message))

    def error(self, message: Any) -> None:
        self._logger.error(self._message(message))

    def critical(self, message: Any) -> None:
        self._logger.critical(self._message(message))

    def exception(self, message: Any) -> None:
        self._logger.exception(self._message(message))


log = TNLog()
