"""
配置文件读取模块
支持环境变量覆盖和多级配置
"""
import yaml
import os
from typing import Any, Optional, Dict
from pathlib import Path


LEGACY_KEY_PATHS = {
    "cookie": "sehuatang.cookie",
    "date": "sehuatang.date",
    "domain_name": "sehuatang.domain_name",
    "fid": "sehuatang.fid",
    "image_proxy_url": "sendMessage.image_proxy_url",
    "page_num": "sehuatang.page_num",
    "proxy_enable": "proxy.proxy_enable",
    "proxy_host": "proxy.proxy_host",
    "proxy_url": "proxy.proxy_url",
    "schedule_cron": "schedule.schedule_cron",
    "schedule_time": "schedule.schedule_time",
    "tg_bot_token": "sendMessage.tg_bot_token",
    "tg_chat_id": "sendMessage.tg_chat_id",
}


class ConfigManager:
    """配置管理器"""

    def __init__(self):
        self._config_cache: Optional[Dict[str, Any]] = None
        self._config_path = self._find_config_path()

    def _find_config_path(self) -> str:
        """查找配置文件路径"""
        current_dir = Path(__file__).parent

        # 优先级：config/config.yaml > config.yaml > config.example.yaml
        possible_paths = [
            current_dir.parent / "config" / "config.yaml",
            current_dir.parent / "config.yaml",
            current_dir.parent / "config" / "config.example.yaml"
        ]

        for path in possible_paths:
            if path.exists():
                return str(path)

        raise FileNotFoundError(
            "未找到配置文件，请确保存在 config/config.yaml 或 config.yaml")

    def _load_config(self) -> Dict[str, Any]:
        """加载配置文件"""
        if self._config_cache is None:
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    self._config_cache = yaml.safe_load(f) or {}
            except Exception as e:
                raise RuntimeError(f"读取配置文件失败: {e}")

        return self._config_cache

    def get_config(self, key: Optional[str] = None, default: Any = None) -> Any:
        """
        获取配置项

        Args:
            key: 配置键，支持点号分隔的多级键，如 'mongodb.host'
            default: 默认值

        Returns:
            配置值
        """
        config = self._load_config()

        if key is None:
            return config

        # 支持环境变量覆盖
        env_key = f"SHT_{key.upper().replace('.', '_')}"
        env_value = os.getenv(env_key)
        if env_value is not None:
            return self._convert_env_value(env_value)

        # 支持点号分隔的多级键
        if '.' in key:
            keys = key.split('.')
            value = config
            for k in keys:
                if isinstance(value, dict) and k in value:
                    value = value[k]
                else:
                    return default
            return value

        # 一级键查找
        if key in config:
            return config[key]

        # 兼容旧调用，但只允许显式别名，禁止跨来源模糊命中同名键。
        legacy_path = LEGACY_KEY_PATHS.get(key)
        if legacy_path:
            return self.get_config(legacy_path, default)

        return default

    def _convert_env_value(self, value: str) -> Any:
        """转换环境变量值为合适的类型"""
        # 布尔值
        if value.lower() in ('true', 'false'):
            return value.lower() == 'true'

        # 数字
        if value.isdigit():
            return int(value)

        try:
            return float(value)
        except ValueError:
            pass

        # 字符串
        return value

    def reload_config(self):
        """重新加载配置"""
        self._config_cache = None


# 全局配置管理器实例
_config_manager = ConfigManager()

# 兼容旧版本的函数


def get_config(key: Optional[str] = None, default: Any = None) -> Any:
    """获取配置项（兼容旧版本）"""
    return _config_manager.get_config(key, default)


def read_config(config_path: str) -> Dict[str, Any]:
    """读取配置文件（兼容旧版本）"""
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
