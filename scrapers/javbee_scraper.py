import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional
from urllib.parse import urljoin

from curl_cffi import requests

from util.log_util import log
from util.mongo import find_existing_javbee_urls, save_javbee_items
from util.read_config import get_config

from .javbee_parser import JavbeeParser


class JavbeeScraper:
    """独立的 Javbee 数据源爬虫，共用项目调度、日志和 MongoDB。"""

    def __init__(self, config=None, http_get=None):
        self.config = config or get_config("javbee", {})
        self.base_url = str(self.config.get("base_url", "https://javbee.co")).rstrip("/")
        self.start_path = str(self.config.get("start_path", "/new"))
        self.page_limit = max(1, int(self.config.get("page_limit", 30)))
        self.workers = max(1, int(self.config.get("concurrent_workers", 6)))
        self.timeout = max(1, int(self.config.get("request_timeout", 20)))
        self.retry_attempts = max(1, int(self.config.get("retry_attempts", 3)))
        self.user_agent = str(
            self.config.get(
                "user_agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            )
        )
        proxy_url = str(self.config.get("proxy_url", "")).strip()
        self.proxies = (
            {"http": proxy_url, "https": proxy_url}
            if self.config.get("proxy_enable") and proxy_url
            else None
        )
        self.parser = JavbeeParser()
        self._http_get = http_get or self._get_html

    def crawl(self) -> Dict[str, int]:
        start_url = urljoin(f"{self.base_url}/", self.start_path.lstrip("/"))
        first_page = self._http_get(start_url)
        if not first_page:
            raise RuntimeError(f"Javbee 列表页获取失败: {start_url}")

        last_page = min(self.parser.parse_last_page(first_page), self.page_limit)
        page_urls = [start_url] + [f"{start_url}?page={page}" for page in range(2, last_page + 1)]
        page_bodies = [first_page] + self._fetch_many(page_urls[1:])

        detail_urls = []
        for body in page_bodies:
            if body:
                detail_urls.extend(self.parser.parse_list(body, self.base_url))
        detail_urls = list(dict.fromkeys(detail_urls))

        existing_urls = find_existing_javbee_urls(detail_urls)
        log.info(
            f"Javbee 列表解析完成: pages={last_page} "
            f"discovered={len(detail_urls)} existing={len(existing_urls)} "
            f"new={len(detail_urls) - len(existing_urls)}"
        )

        detail_bodies = self._fetch_many(detail_urls)
        items = []
        failed = 0
        for url, body in zip(detail_urls, detail_bodies):
            item = self.parser.parse_detail(body, url) if body else None
            if item:
                items.append(item)
            else:
                failed += 1
                log.warning(f"Javbee 详情解析失败: {url}")

        save_summary = save_javbee_items(items)
        return {
            "pages": last_page,
            "discovered": len(detail_urls),
            "existing": len(existing_urls),
            "requested": len(detail_urls),
            "failed": failed,
            "saved": save_summary["upserted"],
            "updated": save_summary["modified"],
        }

    def _fetch_many(self, urls: List[str]) -> List[Optional[bytes]]:
        if not urls:
            return []
        with ThreadPoolExecutor(max_workers=min(self.workers, len(urls))) as pool:
            return list(pool.map(self._http_get, urls))

    def _get_html(self, url: str) -> Optional[bytes]:
        for attempt in range(1, self.retry_attempts + 1):
            try:
                response = requests.get(
                    url,
                    headers={"User-Agent": self.user_agent},
                    proxies=self.proxies,
                    timeout=self.timeout,
                    allow_redirects=True,
                    impersonate="chrome110",
                )
                if response.status_code == 200 and response.content:
                    return response.content
                log.warning(
                    f"Javbee 请求异常: status={response.status_code} "
                    f"attempt={attempt}/{self.retry_attempts} url={url}"
                )
            except Exception as exc:
                log.warning(
                    f"Javbee 请求失败: attempt={attempt}/{self.retry_attempts} "
                    f"url={url} error={exc}"
                )
            if attempt < self.retry_attempts:
                time.sleep(attempt)
        return None
