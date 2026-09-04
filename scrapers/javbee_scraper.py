import time
from typing import Dict, List, Optional

from scrapers.core.config import load_source_settings
from scrapers.core.contracts import NullFailureStore
from scrapers.core.engine import CrawlEngine
from scrapers.core.http import CrawlerHttpClient
from scrapers.core.models import FetchResult
from scrapers.infrastructure import build_failure_store
from scrapers.sources.javbee import JavbeeRepository, JavbeeSource
from util.log_util import log
from util.mongo import (
    find_existing_javbee_urls,
    find_stale_javbee_urls,
    save_javbee_items,
)
from util.read_config import get_config

from .javbee_parser import JavbeeParser


class _CallableHttpAdapter:
    """把旧测试/调用方的 bytes getter 适配为公共 HTTP 结果。"""

    def __init__(self, getter, concurrency: int):
        self.getter = getter
        self.concurrency = concurrency

    def fetch(self, url: str, stage: str = "detail") -> FetchResult:
        started = time.monotonic()
        try:
            body = self.getter(url)
            return FetchResult(
                url=url,
                body=body,
                status_code=200 if body else None,
                attempts=1,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                error_type=None if body else "empty_response",
            )
        except Exception as exc:
            return FetchResult(
                url=url,
                body=None,
                status_code=None,
                attempts=1,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                error_type=type(exc).__name__.lower(),
                error_message=str(exc),
            )

    def fetch_many(self, urls, stage: str = "detail") -> List[FetchResult]:
        return [self.fetch(url, stage) for url in urls]


class JavbeeScraper:
    """JavBee 兼容入口，内部使用公共爬虫引擎。"""

    def __init__(
        self,
        config=None,
        http_get=None,
        failure_store=None,
    ):
        self.config = dict(config or get_config("javbee", {}) or {})
        settings_config = (
            {"crawler": {"sources": {"javbee": self.config}}}
            if "http" in self.config or "concurrency" in self.config
            else {"javbee": self.config}
        )
        self.settings = load_source_settings(settings_config, "javbee")
        self.base_url = str(
            self.config.get("base_url", "https://javbee.co")
        ).rstrip("/")
        self.start_path = str(self.config.get("start_path", "/new"))
        self.page_limit = max(1, int(self.config.get("page_limit", 30)))
        self.workers = self.settings.concurrency
        self.timeout = self.settings.timeout
        self.retry_attempts = self.settings.retry.attempts
        self.user_agent = self.settings.user_agent
        self.proxies = (
            {
                "http": self.settings.proxy.url,
                "https": self.settings.proxy.url,
            }
            if self.settings.proxy.enabled
            else None
        )
        self.parser = JavbeeParser()
        self.http = (
            _CallableHttpAdapter(http_get, self.workers)
            if http_get is not None
            else CrawlerHttpClient("javbee", self.settings)
        )
        if failure_store is not None:
            self.failure_store = failure_store
        elif http_get is not None:
            self.failure_store = NullFailureStore()
        else:
            self.failure_store = build_failure_store(
                mongodb_enabled=bool(get_config("mongodb.enable", False))
            )

    def crawl(
        self,
        *,
        dry_run: bool = False,
        retry_failed: bool = False,
    ) -> Dict[str, object]:
        source = JavbeeSource(self.config, parser=self.parser)
        repository = JavbeeRepository(
            self.config,
            existing_lookup=find_existing_javbee_urls,
            stale_lookup=find_stale_javbee_urls,
            save_func=save_javbee_items,
        )
        summary = CrawlEngine(self.http, self.failure_store).run(
            source,
            repository,
            dry_run=dry_run,
            retry_failed=retry_failed,
        )
        summary.details["existing"] = repository.existing_count
        result = summary.as_dict()
        result["list_retries"] = summary.details.get("list_retries", 0)
        log.info(
            "Javbee 抓取汇总: "
            f"status={result['status']} pages={result.get('pages', 0)} "
            f"discovered={result['discovered']} existing={result['existing']} "
            f"requested={result['requested']} failed={result['failed']} "
            f"saved={result['saved']} updated={result['updated']}"
        )
        return result

    def _get_html(self, url: str) -> Optional[bytes]:
        """兼容旧调用方；新代码应使用公共 HTTP 客户端。"""
        result = self.http.fetch(url)
        return result.body if result.ok else None

    def _fetch_many(self, urls: List[str]) -> List[Optional[bytes]]:
        return [
            result.body if result.ok else None
            for result in self.http.fetch_many(urls)
        ]
