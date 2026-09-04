from pathlib import Path
from typing import Optional

from .json_failures import JsonFailureStore
from .mongo_failures import MongoFailureStore


def build_failure_store(
    *,
    mongodb_enabled: bool,
    json_path: Optional[Path] = None,
):
    if mongodb_enabled:
        return MongoFailureStore()
    return JsonFailureStore(json_path)
