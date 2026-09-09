"""来源/会话通用节流；补抓在原目标上等待恢复。"""
import threading
import time
import math
from dataclasses import dataclass, replace

from scrapers.core.cf_challenge import is_rate_limited, SiteRateLimited
from util.log_util import log


class CrawlStopped(BaseException):
    """用户停止任务，不能被 HTTP 重试或失败台账吞掉。"""


@dataclass(frozen=True)
class RateLimitSettings:
    min_interval_seconds: float = 2.0
    cooldown_seconds: float = 60.0
    max_cooldown_seconds: float = 900.0

    @classmethod
    def from_config(cls, config):
        raw = config.get("rate_limit") or {}
        settings = cls(**{key: float(raw.get(key, default)) for key, default in (
            ("min_interval_seconds", 2), ("cooldown_seconds", 60),
            ("max_cooldown_seconds", 900),
        )})
        if not all(math.isfinite(value) for value in (
            settings.min_interval_seconds, settings.cooldown_seconds, settings.max_cooldown_seconds,
        )):
            raise ValueError("节流与冷却时间必须为有限数值")
        if not settings.min_interval_seconds >= 0:
            raise ValueError("min_interval_seconds 必须大于等于 0")
        if not 0 < settings.cooldown_seconds <= settings.max_cooldown_seconds:
            raise ValueError("冷却时间必须为正数且不超过 max_cooldown_seconds")
        return settings


class RequestGate:
    def __init__(self, settings, *, monotonic=time.monotonic, waiter=None, logger=None):
        self.settings = settings
        self.log = logger or log
        self.label = ""
        self.stop_event = threading.Event()
        self._now = monotonic
        self._wait = waiter or self.stop_event.wait
        self._lock = threading.Lock()
        self._next_request = 0.0
        self._blocked_until = 0.0
        self._delay = settings.cooldown_seconds

    def ready_in(self):
        with self._lock:
            return max(0.0, max(self._next_request, self._blocked_until) - self._now())

    def blocked(self):
        with self._lock:
            return self._now() < self._blocked_until

    def check(self):
        if self.stop_event.is_set():
            raise CrawlStopped()
        with self._lock:
            if self._now() < self._blocked_until:
                raise SiteRateLimited("站点正在冷却")

    def acquire(self):
        while True:
            self.check()
            with self._lock:
                now = self._now()
                if now < self._blocked_until:
                    raise SiteRateLimited("站点正在冷却")
                delay = self._next_request - now
                if delay <= 0:
                    self._next_request = now + self.settings.min_interval_seconds
                    return
            self._wait(min(delay, 60))

    def limit(self, retry_after=0):
        with self._lock:
            now = self._now()
            if now < self._blocked_until:
                return
            delay = max(self._delay, retry_after)
            self._blocked_until = now + delay
            self._delay = min(self.settings.max_cooldown_seconds, self._delay * 2)
        self.log.warning(f"{self.label} 命中限流，冷却 {delay:.2f} 秒后允许重试")

    def success(self):
        with self._lock:
            # 限流前已在途的成功请求不能解除其他线程刚设置的冷却。
            if self._now() >= self._blocked_until:
                self._delay = self.settings.cooldown_seconds

    def wait_for_retry(self, cancel_event=None):
        while True:
            if self.stop_event.is_set() or (cancel_event is not None and cancel_event.is_set()):
                raise CrawlStopped()
            with self._lock:
                delay = self._blocked_until - self._now()
            if delay <= 0:
                return
            self._wait(min(delay, 0.1 if cancel_event is not None else 60))


def limited_result(result):
    return result.error_type == "rate_limited" or is_rate_limited(result.body)


class BackfillHttpClient:
    """只重试限流目标，已取得的详情留在原结果位置；不消耗台账重试次数。"""
    supports_cancellation = True
    def __init__(self, http):
        self.http = http

    def _recover(self, result, stage):
        attempts = result.attempts
        elapsed_ms = result.elapsed_ms
        while limited_result(result):
            log.info(f"补抓等待限流恢复，随后重试原目标: stage={stage} url={result.url}")
            self.http.wait_for_retry()
            result = self.http.fetch(result.url, stage)
            attempts += result.attempts
            elapsed_ms += result.elapsed_ms
        return replace(result, attempts=attempts, elapsed_ms=elapsed_ms)

    def fetch(self, url, stage="detail"):
        if hasattr(self.http, "fetch_recovering"):
            return self.http.fetch_recovering(url, stage)
        return self._recover(self.http.fetch(url, stage), stage)

    def fetch_many(self, urls, stage="detail"):
        if hasattr(self.http, "fetch_many_recovering"):
            return self.http.fetch_many_recovering(urls, stage)
        return [self._recover(result, stage)
                for result in self.http.fetch_many(urls, stage)]

    def iter_completed(self, urls, stage="detail", cancel_event=None):
        if hasattr(self.http, "iter_completed"):
            kwargs = {"cancel_event": cancel_event} if getattr(type(self.http), "supports_cancellation", False) else {}
            yield from self.http.iter_completed(urls, stage, recover=True, **kwargs)
        else:
            yield from enumerate(self.fetch_many(urls, stage))

    @property
    def settings(self):
        return self.http.settings

    def get_html(self, url):
        result = self.fetch(url)
        return result.body if result.ok else None
