from datetime import datetime, timedelta, timezone
from typing import Callable, List, Mapping, Optional, Sequence

from scrapers.core.contracts import CrawlRecord, CrawlTarget, SaveResult


_REFRESH_MODES = {"refresh_all", "new_only", "stale_after"}


class JavbeeRepository:
    def __init__(
        self,
        config: Mapping,
        existing_lookup: Callable,
        save_func: Callable,
        stale_lookup: Optional[Callable] = None,
    ):
        refresh = config.get("refresh") or {}
        if isinstance(refresh, str):
            refresh = {"mode": refresh}
        self.refresh_mode = str(refresh.get("mode") or "refresh_all")
        if self.refresh_mode not in _REFRESH_MODES:
            raise ValueError(f"不支持的 Javbee refresh.mode: {self.refresh_mode}")
        self.stale_days = max(1, int(refresh.get("days", 7)))
        self._existing_lookup = existing_lookup
        self._stale_lookup = stale_lookup
        self._save_func = save_func
        self.existing_count = 0

    def select_targets(self, targets: Sequence[CrawlTarget]) -> List[CrawlTarget]:
        urls = [target.url for target in targets]
        existing = set(self._existing_lookup(urls))
        self.existing_count = len(existing)
        if self.refresh_mode == "refresh_all":
            return list(targets)

        selected_urls = set(urls) - existing
        if self.refresh_mode == "stale_after" and existing:
            if self._stale_lookup is None:
                raise RuntimeError("stale_after 刷新策略缺少 stale_lookup")
            cutoff = datetime.now(timezone.utc) - timedelta(days=self.stale_days)
            selected_urls.update(self._stale_lookup(existing, cutoff))
        return [target for target in targets if target.url in selected_urls]

    def save_many(self, records: Sequence[CrawlRecord]) -> SaveResult:
        result = self._save_func([dict(record.payload) for record in records])
        return SaveResult(
            processed=int(result.get("processed", len(records))),
            saved=int(result.get("upserted", 0)),
            updated=int(result.get("modified", 0)),
        )
