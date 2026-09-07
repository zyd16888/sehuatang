"""x1080x 专用 HTTP 客户端。

请求策略（学习自可用实现，成本从低到高）：
1. 带缓存 Cookie + 浏览器指纹直连；
2. 命中 CF 挑战时经 FlareSolverr 过盾，缓存 solution 的 Cookie/UA；
3. 过盾失败则清空缓存 Cookie 再过盾一次，避免失效 Cookie 持续污染。
过盾全程持锁，并发线程等待共享结果而不是各自触发过盾。
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from typing import Iterable, List, Optional

from curl_cffi import requests

from scrapers.core.cf_challenge import (
    CF_STATUS,
    FlareSolverrClient,
    is_cf_challenge,
    merge_solution_cookies,
)
from scrapers.core.config import HttpSettings
from scrapers.core.http import CrawlerHttpClient, redact_url
from scrapers.core.models import FetchResult
from util.log_util import log


class X1080XHttpClient:
    """实现引擎所需的 fetch/fetch_many 合同，内部处理 CF 挑战。"""

    def __init__(
        self,
        settings: HttpSettings,
        flaresolverr_url: str = "",
        transport: Optional[CrawlerHttpClient] = None,
    ):
        self.settings = settings
        self._cookie: dict = {}
        self._cookie_lock = threading.Lock()
        self._solve_lock = threading.Lock()
        self._user_agent = settings.user_agent
        self._flaresolverr = (
            FlareSolverrClient(
                flaresolverr_url,
                proxy_url=settings.proxy.url if settings.proxy.enabled else None,
            )
            if flaresolverr_url
            else None
        )
        # CF 挑战状态由本类处理，通用重试层不应按普通 429/503 重试。
        retry = replace(
            settings.retry,
            statuses=tuple(
                status
                for status in settings.retry.statuses
                if status not in CF_STATUS
            ),
        )
        self._transport = transport or CrawlerHttpClient(
            "x1080x",
            replace(settings, retry=retry),
            request_func=self._request_with_cookies,
        )

    # ---------- 引擎合同 ----------

    def fetch(self, url: str, stage: str = "detail") -> FetchResult:
        result = self._transport.fetch(url, stage)
        if not is_cf_challenge(result.body, result.status_code):
            return result

        log.info(f"触发 CF 挑战: stage={stage} url={redact_url(url)}")
        body = self._resolve_challenge(url)
        if body is not None:
            return FetchResult(
                url=url,
                body=body,
                status_code=200,
                attempts=result.attempts + 1,
                elapsed_ms=result.elapsed_ms,
            )
        return FetchResult(
            url=url,
            body=None,
            status_code=result.status_code,
            attempts=result.attempts + 1,
            elapsed_ms=result.elapsed_ms,
            error_type="cf_challenge",
            error_message="Cloudflare 挑战未通过",
        )

    def fetch_many(
        self,
        urls: Iterable[str],
        stage: str = "detail",
    ) -> List[FetchResult]:
        ordered_urls = list(urls)
        if not ordered_urls:
            return []
        results: List[Optional[FetchResult]] = [None] * len(ordered_urls)
        worker_count = min(self.settings.concurrency, len(ordered_urls))
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = {
                pool.submit(self.fetch, url, stage): index
                for index, url in enumerate(ordered_urls)
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    results[index] = future.result()
                except Exception as exc:
                    results[index] = FetchResult(
                        url=ordered_urls[index],
                        body=None,
                        status_code=None,
                        attempts=1,
                        elapsed_ms=0,
                        error_type=type(exc).__name__.lower(),
                        error_message=str(exc),
                    )
        return [result for result in results if result is not None]

    # ---------- 内部 ----------

    def _request_with_cookies(self, url: str, **kwargs):
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["User-Agent"] = self._user_agent
        return requests.get(
            url,
            cookies=self._cookie_copy(),
            headers=headers,
            **kwargs,
        )

    def _cookie_copy(self) -> dict:
        with self._cookie_lock:
            return {k: v for k, v in self._cookie.items() if v}

    def _resolve_challenge(self, url: str) -> Optional[bytes]:
        """持锁过盾。等锁的线程先用（可能已更新的）Cookie 直连重试。"""
        with self._solve_lock:
            retry = self._transport.fetch(url, stage="cf_retry")
            if retry.ok and not is_cf_challenge(retry.body, retry.status_code):
                return retry.body

            body = self._bypass(url)
            if body is not None:
                return body

            if self._cookie_copy():
                with self._cookie_lock:
                    self._cookie.clear()
                log.warning("CF 过盾失败，已清空缓存 Cookie 重试")
                return self._bypass(url)
            return None

    def _bypass(self, url: str) -> Optional[bytes]:
        if self._flaresolverr is None:
            log.warning("未配置 flaresolverr_url，无法自动过 CF")
            return None
        started = time.monotonic()
        solution = self._flaresolverr.solve(url, cookies=self._cookie_copy())
        if solution is None:
            return None
        body, cookies, user_agent = solution
        with self._cookie_lock:
            merge_solution_cookies(self._cookie, cookies)
        if user_agent:
            self._user_agent = user_agent
        log.info(
            "CF 过盾成功: "
            f"url={redact_url(url)} cookies={len(cookies)} "
            f"elapsed_ms={int((time.monotonic() - started) * 1000)}"
        )
        return body
