from typing import Any, Mapping
from urllib.parse import urljoin

from scrapers.core.contracts import (
    CrawlContext,
    CrawlRecord,
    CrawlTarget,
    DiscoveryResult,
)
from scrapers.core.http import CrawlerHttpClient
from scrapers.core.models import FetchResult

from .parser import JavbeeParser


class JavbeeSource:
    name = "javbee"

    def __init__(self, config: Mapping[str, Any], parser=None):
        self.config = dict(config)
        self.base_url = str(
            self.config.get("base_url", "https://javbee.co")
        ).rstrip("/")
        self.start_path = str(self.config.get("start_path", "/new"))
        self.page_limit = max(1, int(self.config.get("page_limit", 30)))
        self.parser = parser or JavbeeParser()

    def discover(
        self,
        context: CrawlContext,
        http: CrawlerHttpClient,
    ) -> DiscoveryResult:
        start_url = urljoin(f"{self.base_url}/", self.start_path.lstrip("/"))
        first_page = http.fetch(start_url, stage="list")
        if not first_page.ok:
            raise RuntimeError(
                "Javbee 列表页获取失败: "
                f"url={start_url} error_type={first_page.error_type}"
            )

        last_page = min(
            self.parser.parse_last_page(first_page.body),
            self.page_limit,
        )
        page_urls = [
            f"{start_url}?page={page}"
            for page in range(2, last_page + 1)
        ]
        page_results = http.fetch_many(page_urls, stage="list")
        bodies = [first_page.body]
        list_failures = 0
        list_retries = max(0, first_page.attempts - 1)
        for result in page_results:
            list_retries += max(0, result.attempts - 1)
            if result.ok:
                bodies.append(result.body)
            else:
                list_failures += 1

        detail_urls = []
        for body in bodies:
            detail_urls.extend(self.parser.parse_list(body, self.base_url))
        detail_urls = list(dict.fromkeys(detail_urls))
        targets = [
            CrawlTarget(
                key=self.parser.source_key_from_url(url),
                url=url,
            )
            for url in detail_urls
        ]
        return DiscoveryResult(
            targets=targets,
            failed=list_failures,
            details={
                "pages": last_page,
                "list_pages_succeeded": len(bodies),
                "list_pages_failed": list_failures,
                "list_retries": list_retries,
                "discovery_retries": list_retries,
            },
        )

    def parse_detail(
        self,
        target: CrawlTarget,
        result: FetchResult,
    ):
        payload = self.parser.parse_detail(result.body, target.url)
        if payload is None:
            return None
        return CrawlRecord(target=target, payload=payload)
