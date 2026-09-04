import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

from scrapers.core.contracts import CrawlFailure, CrawlTarget


class JsonFailureStore:
    """MongoDB 未启用时使用的轻量持久化失败台账。"""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or (
            Path(__file__).resolve().parents[2] / "data" / "crawl_failures.json"
        )
        self._lock = threading.Lock()

    def record(self, failures: Iterable[CrawlFailure]) -> None:
        failures = list(failures)
        if not failures:
            return
        with self._lock:
            rows = self._load()
            by_key = {
                (row["source"], row["source_key"], row["stage"]): row
                for row in rows
            }
            now = datetime.now(timezone.utc)
            for failure in failures:
                key = (failure.source, failure.key, failure.stage)
                previous = by_key.get(key) or {}
                attempts = max(1, failure.attempts)
                retry_minutes = min(24 * 60, 5 * (2 ** (attempts - 1)))
                by_key[key] = {
                    "source": failure.source,
                    "source_key": failure.key,
                    "url": failure.url,
                    "stage": failure.stage,
                    "attempts": attempts,
                    "failure_count": int(previous.get("failure_count", 0)) + 1,
                    "error_type": failure.error_type,
                    "error_message": failure.error_message[:1000],
                    "metadata": dict(failure.metadata),
                    "created_at": previous.get("created_at") or now.isoformat(),
                    "last_failed_at": now.isoformat(),
                    "next_retry_at": (
                        now + timedelta(minutes=retry_minutes)
                    ).isoformat(),
                }
            self._save(list(by_key.values()))

    def clear(self, source: str, keys: Iterable[str]) -> None:
        key_set = set(keys)
        if not key_set:
            return
        with self._lock:
            rows = [
                row
                for row in self._load()
                if not (
                    row.get("source") == source
                    and row.get("source_key") in key_set
                )
            ]
            self._save(rows)

    def due_targets(self, source: str):
        now = datetime.now(timezone.utc)
        with self._lock:
            rows = self._load()
        targets = []
        for row in rows:
            if row.get("source") != source:
                continue
            try:
                due_at = datetime.fromisoformat(row["next_retry_at"])
            except (KeyError, TypeError, ValueError):
                due_at = now
            if due_at.tzinfo is None:
                due_at = due_at.replace(tzinfo=timezone.utc)
            if due_at > now:
                continue
            metadata = dict(row.get("metadata") or {})
            metadata["retry_stage"] = row.get("stage")
            targets.append(
                CrawlTarget(
                    key=row["source_key"],
                    url=row["url"],
                    partition=str(metadata.get("fid") or "") or None,
                    metadata=metadata,
                )
            )
        return targets

    def _load(self):
        if not self.path.exists():
            return []
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, list) else []
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return []

    def _save(self, rows) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.path)
