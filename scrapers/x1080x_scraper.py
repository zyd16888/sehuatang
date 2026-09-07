"""x1080x 抓取入口，使用公共爬虫引擎。"""
import os
from typing import Dict

from scrapers.core.config import load_source_settings
from scrapers.core.engine import CrawlEngine
from scrapers.core.http import CrawlerHttpClient
from scrapers.infrastructure import build_failure_store
from scrapers.sources.x1080x import X1080XRepository, X1080XSource
from scrapers.sources.x1080x.http_client import X1080XHttpClient
from util.log_util import log
from util.mongo import find_existing_x1080x_keys, save_x1080x_items
from util.read_config import get_config


def _resolve_flaresolverr_url(config: dict) -> str:
    challenge = dict(config.get("challenge") or {})
    return str(
        os.getenv("CRAWLER_X1080X_FLARESOLVERR_URL")
        or challenge.get("flaresolverr_url")
        or ""
    ).strip()


class X1080XScraper:
    def __init__(self, config=None, http=None, failure_store=None):
        self.config = dict(config or get_config("x1080x", {}) or {})
        base_url = os.getenv("CRAWLER_X1080X_BASE_URL", "").strip()
        if base_url:
            self.config["base_url"] = base_url
        settings_config = (
            {"crawler": {"sources": {"x1080x": self.config}}}
            if "http" in self.config or "concurrency" in self.config
            else {"x1080x": self.config}
        )
        self.settings = load_source_settings(settings_config, "x1080x")
        self.http = http or X1080XHttpClient(
            self.settings,
            flaresolverr_url=_resolve_flaresolverr_url(self.config),
        )
        self.failure_store = failure_store or build_failure_store(
            mongodb_enabled=bool(get_config("mongodb.enable", False))
        )

    def crawl(
        self,
        *,
        dry_run: bool = False,
        retry_failed: bool = False,
    ) -> Dict[str, object]:
        source = X1080XSource(self.config)
        repository = X1080XRepository(
            existing_lookup=find_existing_x1080x_keys,
            save_func=save_x1080x_items,
            refresh_all=bool(self.config.get("refresh_all", False)),
        )
        summary = CrawlEngine(self.http, self.failure_store).run(
            source,
            repository,
            dry_run=dry_run,
            retry_failed=retry_failed,
        )
        summary.details["existing"] = repository.existing_count
        result = summary.as_dict()
        log.info(
            "x1080x 抓取汇总: "
            f"status={result['status']} pages={result.get('pages', 0)} "
            f"discovered={result['discovered']} existing={result['existing']} "
            f"requested={result['requested']} failed={result['failed']} "
            f"saved={result['saved']} updated={result['updated']}"
        )
        return result
