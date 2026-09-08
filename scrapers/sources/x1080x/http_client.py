"""x1080x 专用 HTTP 客户端。

请求策略（学习自可用实现，成本从低到高）：
1. 带缓存 Cookie + 浏览器指纹直连；
2. 命中 CF 挑战时经 FlareSolverr 过盾，缓存 solution 的 Cookie/UA；
3. 过盾失败则清空缓存 Cookie 再过盾一次，避免失效 Cookie 持续污染。
过盾全程持锁，并发线程等待共享结果而不是各自触发过盾。
"""
import threading
import time
from collections import OrderedDict
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from typing import Iterable, List, Optional

from curl_cffi import requests

from scrapers.core.cf_challenge import (
    CF_STATUS,
    FlareSolverrClient,
    is_cf_challenge,
    is_rate_limited,
    merge_solution_cookies,
)
from scrapers.core.config import HttpSettings
from scrapers.core.http import CrawlerHttpClient, redact_url
from scrapers.core.models import FetchResult
from util.log_util import log
from .rate_limit import RateLimitSettings, RequestGate, SiteRateLimited


class X1080XHttpClient:
    """实现引擎所需的 fetch/fetch_many 合同，内部处理 CF 挑战。"""

    def __init__(
        self,
        settings: HttpSettings,
        flaresolverr_url: str = "",
        transport: Optional[CrawlerHttpClient] = None,
        rate_settings: Optional[RateLimitSettings] = None,
    ):
        self.settings = settings
        self.gate = RequestGate(rate_settings or RateLimitSettings())
        self._cookie: dict = {}
        self._cookie_lock = threading.Lock()
        self._solve_lock = threading.Lock()
        self._cookie_version = 0
        self._local = threading.local()
        self._user_agent = settings.user_agent
        self._flaresolverr = (
            FlareSolverrClient(
                flaresolverr_url,
                proxy_url=settings.proxy.url if settings.proxy.enabled else None,
                raise_on_rate_limit=True,
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
        try:
            self.gate.check()
            result = self._fetch_once(url, stage)
        except SiteRateLimited:
            return FetchResult(url, None, 429, 0, 0,
                               error_type="rate_limited", error_message="站点正在冷却")
        if (is_rate_limited(result.body) or result.error_type == "siteratelimited"
                or (result.status_code == 429 and not is_cf_challenge(result.body, 200))):
            self.gate.limit()
            return replace(result, status_code=429, error_type="rate_limited",
                           error_message="站点请求过于频繁，等待冷却后恢复")
        if result.ok:
            self.gate.success()
        return result

    def wait_for_retry(self):
        self.gate.wait_for_retry()

    def _fetch_once(self, url: str, stage: str) -> FetchResult:
        started = time.monotonic()
        version = self._cookie_version
        result = self._transport.fetch(url, stage)
        if (is_rate_limited(result.body) or result.error_type == "siteratelimited"
                or (result.status_code == 429 and not is_cf_challenge(result.body, 200))):
            return result
        if not is_cf_challenge(result.body, result.status_code):
            return result

        log.info(f"触发 CF 挑战: stage={stage} url={redact_url(url)}")
        try:
            body, extra_attempts = self._resolve_challenge(url, version)
        except SiteRateLimited as exc:
            return FetchResult(url, None, 429,
                               result.attempts + getattr(exc, "attempts", 0),
                               int((time.monotonic() - started) * 1000),
                               error_type="rate_limited", error_message="站点请求过于频繁")
        if body is not None:
            return FetchResult(
                url=url,
                body=body,
                status_code=200,
                attempts=result.attempts + extra_attempts,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
        return FetchResult(
            url=url,
            body=None,
            status_code=result.status_code,
            attempts=result.attempts + extra_attempts,
            elapsed_ms=int((time.monotonic() - started) * 1000),
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
        # 每次真实请求（包括 transport 内部重试）都经过同一个来源闸门。
        self.gate.acquire()
        headers = dict(kwargs.pop("headers", {}) or {})
        with self._cookie_lock:
            headers["User-Agent"] = self._user_agent
            cookies = {key: value for key, value in self._cookie.items() if value}
        if not hasattr(self._local, "session"):
            self._local.session = requests.Session()
        session = self._local.session
        session.cookies.clear()
        response = session.get(url, cookies=cookies, headers=headers, **kwargs)
        if (is_rate_limited(response.content)
                or (response.status_code == 429 and not is_cf_challenge(response.content, 200))):
            self.gate.limit(CrawlerHttpClient._retry_after(response))
        with self._cookie_lock:
            self._cookie.update({key: value for key, value in response.cookies.get_dict().items()
                                 if key != "cf_clearance"})
        return response

    def _cookie_copy(self) -> dict:
        with self._cookie_lock:
            return {k: v for k, v in self._cookie.items() if v}

    def _resolve_challenge(self, url: str, version: int):
        """仅当等待期间验证状态更新，才尝试复用；省掉必然失败的重复直连。"""
        attempts = 0
        with self._solve_lock:
            self.gate.check()
            if version != self._cookie_version:
                retry = self._transport.fetch(url, stage="cf_retry")
                attempts += retry.attempts
                if (is_rate_limited(retry.body) or retry.error_type == "siteratelimited"
                        or (retry.status_code == 429 and not is_cf_challenge(retry.body, 200))):
                    self.gate.limit()
                    raise SiteRateLimited()
                if retry.ok and not is_cf_challenge(retry.body, retry.status_code):
                    return retry.body, attempts
            body = self._bypass(url)
            attempts += 1
            if body is not None:
                return body, attempts
            if self._flaresolverr and self._cookie_copy():
                with self._cookie_lock:
                    self._cookie.clear()
                    self._cookie_version += 1
                log.warning("CF 过盾失败，已清空缓存 Cookie 重试")
                return self._bypass(url), attempts + 1
            return None, attempts

    def _bypass(self, url: str) -> Optional[bytes]:
        if self._flaresolverr is None:
            log.warning("未配置 flaresolverr_url，无法自动过 CF")
            return None
        started = time.monotonic()
        self.gate.acquire()
        try:
            solution = self._flaresolverr.solve(url, cookies=self._cookie_copy())
        except SiteRateLimited as exc:
            self.gate.limit()
            exc.attempts = 1
            raise
        if solution is None:
            return None
        body, cookies, user_agent = solution
        if is_rate_limited(body):
            self.gate.limit()
        with self._cookie_lock:
            merge_solution_cookies(self._cookie, cookies)
            if user_agent:
                self._user_agent = user_agent
            self._cookie_version += 1
        log.info(
            "CF 挑战处理完成: "
            f"url={redact_url(url)} cookies={len(cookies)} "
            f"elapsed_ms={int((time.monotonic() - started) * 1000)}"
        )
        return body


# 只缓存内存中的来源会话；域名、代理、指纹或验证端点变化时自然隔离。
_CLIENTS = OrderedDict()
_CLIENTS_LOCK = threading.Lock()


def shared_http_client(settings, endpoint, base_url, rate_settings=None):
    key = (settings, endpoint, urlsplit(base_url).netloc.lower(), rate_settings)
    now = time.monotonic()
    with _CLIENTS_LOCK:
        cached = _CLIENTS.get(key)
        if cached and now - cached[0] < 3600:
            _CLIENTS.move_to_end(key)
            return cached[1]
        client = X1080XHttpClient(settings, flaresolverr_url=endpoint,
                                 rate_settings=rate_settings)
        _CLIENTS[key] = (now, client)
        _CLIENTS.move_to_end(key)
        while len(_CLIENTS) > 8:
            _CLIENTS.popitem(last=False)
        return client


def stop_shared_clients():
    """服务退出时唤醒冷却等待，保留未完成页检查点供 --resume 恢复。"""
    with _CLIENTS_LOCK:
        for _, client in _CLIENTS.values():
            client.gate.stop_event.set()
        _CLIENTS.clear()
