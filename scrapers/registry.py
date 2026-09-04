import asyncio
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
    failed = 0
    with SehuatangSource(dry_run=dry_run) as source:
        if retry_failed:
            summary = source.retry_failed_details()
            return {
                "source": "sehuatang",
                "status": (
                    "partial_success"
                    if summary["failed"] and summary["saved"]
                    else ("failed" if summary["failed"] else "success")
                ),
                "dry_run": dry_run,
                **summary,
            }
        for fid in fids:
            result = await source.crawl_forum_section(fid)
            results[str(fid)] = result
            if isinstance(result, str) and result.startswith("爬取失败"):
                failed += 1
    return {
        "source": "sehuatang",
        "status": "partial_success" if failed and failed < len(fids) else (
            "failed" if failed else "success"
        ),
        "sections": len(fids),
        "failed_sections": failed,
        "dry_run": dry_run,
        "results": results,
    }


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


class SourceRegistry:
    def __init__(self):
        self._sources: Dict[str, SourceDefinition] = {}

    def register(self, definition: SourceDefinition) -> None:
        if definition.name in self._sources:
            raise ValueError(f"来源重复注册: {definition.name}")
        self._sources[definition.name] = definition

    def names(self):
        return tuple(self._sources)

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
        return await self._sources[name].runner(
            config,
            force=force,
            dry_run=dry_run,
            retry_failed=retry_failed,
        )

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
        return results


source_registry = SourceRegistry()
source_registry.register(SourceDefinition("sehuatang", _run_sehuatang, True))
source_registry.register(SourceDefinition("javbee", _run_javbee, False))
