"""固定代理端口的公共常驻会话池，不探测出口、不切换线路。"""
import atexit
import threading
import queue
import time
from concurrent.futures import Future, wait, FIRST_COMPLETED
from dataclasses import replace
from urllib.parse import urlsplit

from .config import ProxySettings
from .models import FetchResult
from .rate_limit import CrawlStopped, RateLimitSettings, RequestGate, limited_result
from .session import SessionHttpClient
from .network import NetworkMonitor, WINDOW_SECONDS


class _SessionWorker:
    def __init__(self, lane, name):
        self.lane = lane
        self.jobs = queue.Queue(maxsize=1)
        self.thread = threading.Thread(target=self._run, name=name, daemon=True)
        self.thread.start()

    def submit(self, func, *args):
        future = Future()
        self.jobs.put((future, func, args))
        return future

    def _run(self):
        try:
            while True:
                job = self.jobs.get()
                if job is None:
                    return
                future, func, args = job
                if future.set_running_or_notify_cancel():
                    try:
                        future.set_result(func(*args))
                    except BaseException as exc:
                        future.set_exception(exc)
        finally:
            self.lane.close()

    def shutdown(self):
        self.jobs.put(None)
        self.thread.join()


class SessionPool:
    supports_cancellation = True

    def __init__(self, source, settings, *, session_factory=SessionHttpClient):
        settings.validate()
        self.settings = settings
        self.source = source
        self._condition = threading.Condition()
        self._closed = False
        self._inflight = {}
        self._busy = set()
        self._cursor = 0
        self._waiting = 0
        self._probe_stop = threading.Event()
        self._slots = threading.BoundedSemaphore(settings.concurrency)
        rate = RateLimitSettings(settings.min_interval_seconds,
                                 settings.cooldown_seconds, settings.max_cooldown_seconds)
        self._site_gate = RequestGate(replace(rate, min_interval_seconds=settings.site_interval_seconds))
        self.lanes = [session_factory(
            replace(settings, concurrency=1, proxy=ProxySettings(bool(url), url)),
            source=source, flaresolverr_url=settings.solver_url,
            rate_settings=rate, before_request=self._site_gate.acquire,
        ) for url in settings.proxy.addresses]
        for index, lane in enumerate(self.lanes):
            lane.gate.label = f"proxy_slot={index}"
        workers_per_proxy = min(settings.per_proxy_concurrency, settings.concurrency)
        self._worker_lanes = [i for i in range(len(self.lanes)) for _ in range(workers_per_proxy)]
        self._workers = [_SessionWorker(self.lanes[lane_index], f"{source}-proxy-{lane_index}-worker-{i}")
                         for i, lane_index in enumerate(self._worker_lanes)]
        self.lanes[0].log.info(
            f"HTTP 并发配置: 端口数={len(self.lanes)} "
            f"来源并发上限={settings.concurrency} 每端口并发上限={settings.per_proxy_concurrency} "
            f"有效并发上限={min(settings.concurrency, len(self._workers))} "
            f"每端口请求间隔={settings.min_interval_seconds}s")
        self._probe_thread = threading.Thread(target=self._diagnostics, name=f"{source}-network", daemon=True)
        self._probe_thread.start()

    def _submit(self, url, stage, recover, cancel_event=None):
        with self._condition:
            self._waiting += 1
        try:
            return self._submit_request(url, stage, recover, cancel_event)
        finally:
            with self._condition:
                self._waiting -= 1

    def _submit_request(self, url, stage, recover, cancel_event=None):
        key = (url, stage, recover, cancel_event)
        with self._condition:
            while True:
                if self._closed or (cancel_event is not None and cancel_event.is_set()):
                    raise CrawlStopped()
                if key in self._inflight:
                    return self._inflight[key]
                available = [i for i in range(len(self._workers)) if i not in self._busy]
                ready = [i for i in available if not self.lanes[self._worker_lanes[i]].gate.blocked()]
                if ready or (recover and available):
                    choices = ready or available
                    busy_per_lane = [0] * len(self.lanes)
                    for i in self._busy:
                        busy_per_lane[self._worker_lanes[i]] += 1
                    index = min(choices, key=lambda i: (
                        self.lanes[self._worker_lanes[i]].gate.ready_in(),
                        busy_per_lane[self._worker_lanes[i]],
                        (self._worker_lanes[i] - self._cursor) % len(self.lanes), i))
                    lane_index = self._worker_lanes[index]
                    self._cursor = (lane_index + 1) % len(self.lanes)
                    self._busy.add(index)
                    future = self._workers[index].submit(self._execute, lane_index, url, stage, recover, cancel_event)
                    self._inflight[key] = future
                    def release(done, index=index, key=key):
                        with self._condition:
                            self._busy.discard(index)
                            self._inflight.pop(key, None)
                            self._condition.notify_all()
                    future.add_done_callback(release)
                    return future
                if not recover and len(available) == len(self._workers):
                    future = Future()
                    future.set_result(FetchResult(url, None, 429, 0, 0,
                                      "rate_limited", "所有会话正在冷却"))
                    return future
                self._condition.wait(timeout=0.1)

    def _execute(self, index, url, stage, recover, cancel_event=None):
        lane = self.lanes[index]
        attempts = elapsed = 0
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise CrawlStopped()
            if recover:
                lane.gate.wait_for_retry(cancel_event=cancel_event)
            with self._slots:
                if self._closed or (cancel_event is not None and cancel_event.is_set()):
                    raise CrawlStopped()
                request_started = time.monotonic()
                result = lane.fetch(url, stage)
            attempts += result.attempts
            elapsed += result.elapsed_ms
            lane.log.debug(f"HTTP 会话结果: proxy_slot={index} stage={stage} status={result.status_code} "
                           f"attempts={result.attempts} error_type={result.error_type}")
            if not recover or not limited_result(result):
                if result.attempts and isinstance(getattr(lane, "network", None), NetworkMonitor):
                    lane.network.completed(url, result.ok, request_started, result.error_type)
                return replace(result, attempts=attempts, elapsed_ms=elapsed)
            # 原目标留在原会话中恢复；等待时不占用来源并发额度。
            lane.log.info(f"补抓会话冷却，等待原目标恢复: proxy_slot={index} stage={stage}")

    def _diagnostics(self):
        while not self._probe_stop.wait(1):
            self._dispatch_diagnostics()

    def _dispatch_diagnostics(self):
        with self._condition:
            if self._closed or self._waiting:
                return
            for lane_index, lane in enumerate(self.lanes):
                if not isinstance(getattr(lane, "network", None), NetworkMonitor):
                    continue
                if lane.gate.blocked() or any(self._worker_lanes[i] == lane_index for i in self._busy):
                    continue
                claim = lane.network.claim_probe()
                if not claim:
                    continue
                index = self._worker_lanes.index(lane_index)
                self._busy.add(index)
                future = self._workers[index].submit(self._probe, lane, claim)
                def release(done, index=index):
                    with self._condition:
                        self._busy.discard(index)
                        self._condition.notify_all()
                future.add_done_callback(release)

    def _probe(self, lane, claim):
        with self._slots:
            if self._closed:
                lane.network.finish_probe(*claim)
                return
            lane.probe_network(*claim)

    def fetch(self, url, stage="detail"):
        return self._submit(url, stage, False).result()

    def fetch_recovering(self, url, stage="detail"):
        return self._submit(url, stage, True).result()

    def iter_completed(self, urls, stage="detail", recover=False, cancel_event=None):
        # 在途任务不超过 worker 容量；输入重复 URL 共用一次结果。
        grouped = {}
        for index, url in enumerate(urls):
            grouped.setdefault(url, []).append(index)
        pending_urls = iter(grouped)
        pending = {}
        def fill():
            while len(pending) < len(self._workers):
                url = next(pending_urls, None)
                if url is None:
                    break
                pending[self._submit(url, stage, recover, cancel_event)] = url
        fill()
        while pending:
            if cancel_event is not None and cancel_event.is_set():
                raise CrawlStopped()
            completed, _ = wait(pending, timeout=0.1 if cancel_event is not None else None,
                                return_when=FIRST_COMPLETED)
            for future in completed:
                url = pending.pop(future)
                result = future.result()
                for index in grouped[url]:
                    yield index, result
            fill()

    def fetch_many(self, urls, stage="detail"):
        return self._many(urls, stage, False)

    def fetch_many_recovering(self, urls, stage="detail"):
        return self._many(urls, stage, True)

    def _many(self, urls, stage, recover):
        urls = list(urls)
        results = [None] * len(urls)
        for index, result in self.iter_completed(urls, stage, recover):
            results[index] = result
        return results

    def get_html(self, url):
        result = self.fetch(url)
        return result.body if result.ok else None

    @property
    def all_cooling(self):
        return all(lane.gate.blocked() for lane in self.lanes)

    def close(self):
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._probe_stop.set()
            self._site_gate.stop_event.set()
            for lane in self.lanes:
                lane.gate.stop_event.set()
            self._condition.notify_all()
        self._probe_thread.join()
        # close 在创建 Session 的同一常驻线程中执行。
        for worker in self._workers:
            worker.shutdown()


_POOLS = {}
_LOCK = threading.Lock()


def network_snapshot():
    with _LOCK:
        pools = list(_POOLS.values())
    rows = []
    for pool in pools:
        for index, lane in enumerate(pool.lanes):
            if not isinstance(getattr(lane, "network", None), NetworkMonitor):
                continue
            entries = lane.network.snapshot() or [dict(source=pool.source, proxy=lane.network.proxy,
                                                      target=None, state="stale")]
            rows.extend(dict(entry, proxy_slot=index) for entry in entries)
    return {"window_seconds": WINDOW_SECONDS, "lines": rows}


def shared_pool(source, settings, base_url, *, session_factory=SessionHttpClient, identity=None):
    key = (source, settings, urlsplit(base_url).netloc.lower(), session_factory, identity)
    with _LOCK:
        if key not in _POOLS:
            _POOLS[key] = SessionPool(source, settings, session_factory=session_factory)
        return _POOLS[key]


def stop_shared_clients():
    with _LOCK:
        pools = list(_POOLS.values())
        _POOLS.clear()
    for pool in pools:
        pool.close()


atexit.register(stop_shared_clients)
