"""x1080x 入库仓储：按 source_key 幂等 upsert，默认只抓新帖。"""
from typing import Callable, List, Sequence

from scrapers.core.contracts import CrawlRecord, CrawlTarget, SaveResult


class X1080XRepository:
    def __init__(
        self,
        existing_lookup: Callable,
        save_func: Callable,
        refresh_all: bool = False,
    ):
        self._existing_lookup = existing_lookup
        self._save_func = save_func
        self.refresh_all = bool(refresh_all)
        self.existing_count = 0
        # 最近一次 save_many 的载荷；new_only 模式下即本次新增数据（供通知用）
        self.last_saved_payloads = []

    def select_targets(self, targets: Sequence[CrawlTarget]) -> List[CrawlTarget]:
        keys = [target.key for target in targets]
        existing = set(self._existing_lookup(keys))
        self.existing_count = len(existing)
        if self.refresh_all:
            return list(targets)
        return [target for target in targets if target.key not in existing]

    def save_many(self, records: Sequence[CrawlRecord]) -> SaveResult:
        payloads = [dict(record.payload) for record in records]
        result = self._save_func(payloads)
        self.last_saved_payloads = payloads
        return SaveResult(
            processed=int(result.get("processed", len(records))),
            saved=int(result.get("upserted", 0)),
            updated=int(result.get("modified", 0)),
        )
