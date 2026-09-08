import asyncio
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Iterable, Mapping

from scrapers.javbee_scraper import JavbeeScraper
from scrapers.sources.sehuatang import SehuatangSource
from util.log_util import log


SourceRunner = Callable[..., Awaitable[Dict[str, Any]]]


@dataclass(frozen=True)
class SourceDefinition:
    name: str
    runner: SourceRunner
    enabled_by_default: bool


def _merge_dict(base: Mapping[str, Any], override: Mapping[str, Any]):
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _source_config(config: Mapping[str, Any], name: str) -> Dict[str, Any]:
    crawler_sources = ((config.get("crawler") or {}).get("sources") or {})
    return _merge_dict(
        dict(config.get(name) or {}),
        dict(crawler_sources.get(name) or {}),
    )


async def _run_sehuatang(
    config: Mapping[str, Any],
    *,
    force: bool = False,
    dry_run: bool = False,
    retry_failed: bool = False,
) -> Dict[str, Any]:
    source_config = _source_config(config, "sehuatang")
    configured_fids = source_config.get("fid") or {}
    fids = [int(fid) for fid in configured_fids]
    results = {}
    with SehuatangSource(dry_run=dry_run) as source:
        if retry_failed:
            return {"source": "sehuatang", **source.retry_failed_details()}
        for fid in fids:
            results[str(fid)] = await source.crawl_forum_section(fid)
    counters = ("discovered", "requested", "succeeded", "saved", "failed", "existing",
                "filtered", "list_requested", "list_succeeded")
    totals = {key: sum(row[key] for row in results.values()) for key in counters}
    failures = sum(row["status"] != "success" for row in results.values())
    any_success = any(row["status"] != "failed" for row in results.values())
    stages = {}
    for row in results.values():
        for stage, count in row["stage_failures"].items():
            stages[stage] = stages.get(stage, 0) + count
    return {"source": "sehuatang", "dry_run": dry_run, **totals,
            "status": "success" if not failures else "partial_success" if any_success else "failed",
            "sections": len(fids), "failed_sections": failures,
            "stage_failures": stages, "results": results}


async def _run_javbee(
    config: Mapping[str, Any],
    *,
    force: bool = False,
    dry_run: bool = False,
    retry_failed: bool = False,
) -> Dict[str, Any]:
    from util.read_config import get_config

    mongodb_enabled = get_config(
        "mongodb.enable",
        (config.get("mongodb") or {}).get("enable", False),
    )
    if not mongodb_enabled:
        raise RuntimeError("Javbee 数据源要求启用 MongoDB")
    source_config = _source_config(config, "javbee")
    scraper = JavbeeScraper(config=source_config)
    return await asyncio.to_thread(
        scraper.crawl,
        dry_run=dry_run,
        retry_failed=retry_failed,
    )


async def _run_x1080x(
    config: Mapping[str, Any],
    *,
    force: bool = False,
    dry_run: bool = False,
    retry_failed: bool = False,
) -> Dict[str, Any]:
    from util.read_config import get_config

    mongodb_enabled = get_config(
        "mongodb.enable",
        (config.get("mongodb") or {}).get("enable", False),
    )
    if not mongodb_enabled:
        raise RuntimeError("x1080x 数据源要求启用 MongoDB")
    from scrapers.x1080x_scraper import X1080XScraper

    source_config = _source_config(config, "x1080x")
    scraper = X1080XScraper(config=source_config)
    return await asyncio.to_thread(
        scraper.crawl,
        dry_run=dry_run,
        retry_failed=retry_failed,
    )


class SourceRegistry:
    def __init__(self):
        self._sources: Dict[str, SourceDefinition] = {}
        self._run_locks: Dict[str, threading.Lock] = {}
        self._activities = {}
        self._activity_lock = threading.Lock()

    def register(self, definition: SourceDefinition) -> None:
        if definition.name in self._sources:
            raise ValueError(f"来源重复注册: {definition.name}")
        self._sources[definition.name] = definition
        self._run_locks[definition.name] = threading.Lock()

    def names(self):
        return tuple(self._sources)

    def running_sources(self):
        """返回当前进程内正在运行的来源名。"""
        return tuple(
            name for name, lock in self._run_locks.items() if lock.locked()
        )

    def active_tasks(self):
        with self._activity_lock:
            return [dict(task) for task in self._activities.values()]

    @contextmanager
    def activity(self, source, kind, description):
        lock = self._run_locks[source]
        acquired = lock.acquire(blocking=False)
        if not acquired:
            yield False
            return
        with self._activity_lock:
            self._activities[source] = {"source": source, "kind": kind,
                "description": description, "started_at": time.time(), "running": True}
        try:
            yield True
        finally:
            with self._activity_lock:
                self._activities.pop(source, None)
            lock.release()

    @staticmethod
    def _record_run(result: Mapping[str, Any]) -> None:
        """把运行结果落库；MongoDB 未启用或写入失败时静默跳过。"""
        if not result or result.get("status") == "skipped":
            return
        try:
            from util.read_config import get_config

            if not get_config("mongodb.enable", False):
                return
            from util.mongo import record_crawl_run

            record_crawl_run(result)
        except Exception as exc:
            log.warning(f"运行历史落库失败: {exc}")

    def is_enabled(self, config: Mapping[str, Any], name: str) -> bool:
        definition = self._sources[name]
        source_config = _source_config(config, name)
        return bool(
            source_config.get(
                "enabled",
                source_config.get("enable", definition.enabled_by_default),
            )
        )

    async def run(
        self,
        name: str,
        config: Mapping[str, Any],
        *,
        force: bool = False,
        dry_run: bool = False,
        retry_failed: bool = False,
    ) -> Dict[str, Any]:
        if name not in self._sources:
            raise ValueError(f"未知来源: {name}")
        if not force and not self.is_enabled(config, name):
            log.info(f"来源未启用，跳过: source={name}")
            return {"source": name, "status": "skipped"}

        kind = "retry" if retry_failed else "crawl"
        description = f"{name} {'失败重试' if retry_failed else '抓取'}" + (" · dry-run" if dry_run else "")
        started = time.monotonic()
        with self.activity(name, kind, description) as acquired:
            if not acquired:
                log.warning(f"来源已在运行中，跳过本次触发: source={name}")
                return {"source": name, "status": "already_running"}
            result = await self._sources[name].runner(
                config, force=force, dry_run=dry_run, retry_failed=retry_failed,
            )
        result.setdefault("elapsed_ms", int((time.monotonic() - started) * 1000))
        result.setdefault("kind", kind)
        self._record_run(result)
        return result

    async def run_many(
        self,
        names: Iterable[str],
        config: Mapping[str, Any],
        **options,
    ) -> Dict[str, Dict[str, Any]]:
        results = {}
        for name in names:
            try:
                results[name] = await self.run(name, config, **options)
            except Exception as exc:
                log.error(f"来源执行失败: source={name} error={exc}")
                results[name] = {
                    "source": name,
                    "status": "failed",
                    "error": str(exc),
                }
                self._record_run(results[name])
        return results


source_registry = SourceRegistry()
source_registry.register(SourceDefinition("sehuatang", _run_sehuatang, True))
source_registry.register(SourceDefinition("javbee", _run_javbee, False))
source_registry.register(SourceDefinition("x1080x", _run_x1080x, False))
