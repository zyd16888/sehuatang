"""爬虫公共基础设施适配器。"""

from .failure_store import build_failure_store
from .json_failures import JsonFailureStore
from .mongo_failures import MongoFailureStore

__all__ = ["JsonFailureStore", "MongoFailureStore", "build_failure_store"]
