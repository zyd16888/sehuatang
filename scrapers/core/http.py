import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from curl_cffi import requests
from curl_cffi.requests.exceptions import RequestException

from util.log_util import log

from .config import HttpSettings
from .models import FetchResult


_SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "key",
    "password",
    "secret",
    "signature",
    "token",
}


def redact_url(url: str) -> str:
    """移除 URL 用户信息并隐藏常见敏感查询参数。"""
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    query = urlencode(
        [
            (key, "***" if key.lower() in _SENSITIVE_QUERY_KEYS else value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        ]
    )
    return urlunsplit((parsed.scheme, netloc, parsed.path, query, parsed.fragment))


class CrawlerHttpClient:
    """按来源隔离配置、代理和重试状态的 HTTP 客户端。"""

    def __init__(
        self,
        source: str,
        settings: HttpSettings,
        request_func: Optional[Callable] = None,
        sleeper: Callable[[float], None] = time.sleep,
        random_uniform: Callable[[float, float], float] = random.uniform,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        settings.validate()
        self.source = source
        self.log = log.bind(module=source)
        self.settings = settings
        self._request_func = request_func or requests.get
        self._sleeper = sleeper
        self._random_uniform = random_uniform
        self._monotonic = monotonic

    @property
    def proxies(self):
        if not self.settings.proxy.enabled:
            return None
        return {
            "http": self.settings.proxy.url,
            "https": self.settings.proxy.url,
        }

    def fetch(self, url: str, stage: str = "detail") -> FetchResult:
        started = self._monotonic()
        last_status = None
        last_body = None
        last_error_type = None
        last_error_message = None

        for attempt in range(1, self.settings.retry.attempts + 1):
            retry_after = 0.0
            try:
                response = self._request_func(
                    url,
                    headers={"User-Agent": self.settings.user_agent},
                    proxies=self.proxies,
                    timeout=self.settings.timeout,
                    allow_redirects=True,
                    impersonate=self.settings.impersonate,
                )
                last_status = int(response.status_code)
                body = response.content or b""
                last_body = body
                if last_status == 200 and body:
                    return FetchResult(
                        url=url,
                        body=body,
                        status_code=last_status,
                        attempts=attempt,
                        elapsed_ms=self._elapsed_ms(started),
                    )

                if last_status == 200:
                    last_error_type = "empty_response"
                    last_error_message = "HTTP 200 响应体为空"
                    retryable = True
                else:
                    last_error_type = "http_status"
                    last_error_message = f"HTTP {last_status}"
                    retryable = last_status in self.settings.retry.statuses
                    retry_after = self._retry_after(response)
            except Exception as exc:
                last_error_type = self._exception_type(exc)
                last_error_message = str(exc)
                retryable = isinstance(
                    exc,
                    (RequestException, TimeoutError, ConnectionError),
                )

            if not retryable or attempt >= self.settings.retry.attempts:
                break

            delay = max(self._retry_delay(attempt), retry_after)
            self.log.warning(
                "HTTP 请求将在退避后重试: "
                f"source={self.source} stage={stage} "
                f"attempt={attempt}/{self.settings.retry.attempts} "
                f"delay={delay:.2f}s status={last_status} "
                f"error_type={last_error_type} url={redact_url(url)}"
            )
            self._sleeper(delay)

        return FetchResult(
            url=url,
            body=last_body,
            status_code=last_status,
            attempts=attempt,
            elapsed_ms=self._elapsed_ms(started),
            error_type=last_error_type,
            error_message=last_error_message,
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
                        error_type=self._exception_type(exc),
                        error_message=str(exc),
                    )

        return [result for result in results if result is not None]

    def _retry_delay(self, attempt: int) -> float:
        retry = self.settings.retry
        base = min(retry.max_delay, retry.base_delay * (2 ** (attempt - 1)))
        multiplier = 1 + self._random_uniform(-retry.jitter, retry.jitter)
        return max(0.0, base * multiplier)

    @staticmethod
    def _retry_after(response) -> float:
        value = (getattr(response, "headers", {}) or {}).get("Retry-After")
        if value is None:
            return 0.0
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _exception_type(exc: Exception) -> str:
        name = type(exc).__name__.lower()
        if "timeout" in name or "timed out" in str(exc).lower():
            return "timeout"
        if "proxy" in name:
            return "proxy"
        if "dns" in name:
            return "dns"
        if "connection" in name:
            return "connection"
        return name or "request_error"

    def _elapsed_ms(self, started: float) -> int:
        return max(0, int((self._monotonic() - started) * 1000))
