"""固定代理端口的常驻会话池，网络失败时跨端口重试。"""
import atexit
import threading
import queue
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import replace
from urllib.parse import urlsplit

from .config import ProxySettings
from .models import FetchResult
from .rate_limit import CrawlStopped, RateLimitSettings, RequestGate, limited_result
from .session import SessionHttpClient
from .network import NETWORK_ERRORS, NetworkMonitor, WINDOW_SECONDS


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
        self._manual_probes = {}
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
            if isinstance(lane, SessionHttpClient):
                lane._transport.stop_on_network_error = len(self.lanes) > 1
        workers_per_proxy = min(settings.per_proxy_concurrency, settings.concurrency)
        self._worker_lanes = [i for i in range(len(self.lanes)) for _ in range(workers_per_proxy)]
        self._workers = [_SessionWorker(self.lanes[lane_index], f"{source}-proxy-{lane_index}-worker-{i}")
                         for i, lane_index in enumerate(self._worker_lanes)]
        # 协调重试的线程不持有 HTTP Session，等待其他端口时释放原 worker。
        self._retry_executor = ThreadPoolExecutor(max_workers=len(self._workers), thread_name_prefix=f"{source}-retry")
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
            if key in self._inflight:
                return self._inflight[key]
            context = dict(key=key, future=Future(), url=url, stage=stage, recover=recover,
                           cancel_event=cancel_event, attempts=0, elapsed=0, last_lane=None)
            self._inflight[key] = context["future"]
            try:
                self._schedule(context)
            except BaseException as exc:
                self._inflight.pop(key, None)
                context["future"].set_exception(exc)
            return context["future"]

    def _network_available(self, lane_index, url):
        monitor = getattr(self.lanes[lane_index], "network", None)
        return not isinstance(monitor, NetworkMonitor) or monitor.available(url)

    def _schedule(self, context):
        url, recover, cancel_event = context["url"], context["recover"], context["cancel_event"]
        with self._condition:
            while True:
                if self._closed or (cancel_event is not None and cancel_event.is_set()):
                    raise CrawlStopped()
                available = [i for i in range(len(self._workers)) if i not in self._busy]
                usable = [i for i in available if self._network_available(self._worker_lanes[i], url)]
                ready = [i for i in usable if not self.lanes[self._worker_lanes[i]].gate.blocked()]
                if ready or (recover and usable):
                    choices = ready or usable
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
                    self._inflight[context["key"]] = context["future"]
                    future = self._workers[index].submit(
                        self._execute, lane_index, url, context["stage"], recover, cancel_event,
                        max(1, self.settings.retry.attempts - context["attempts"]), context["attempts"] > 0)
                    future.add_done_callback(lambda done, index=index: self._release_request(context, index, done))
                    return
                if not any(self._network_available(i, url) for i in range(len(self.lanes))):
                    self._finish(context, FetchResult(url, None, None, 0, 0,
                                 "proxy_unavailable", "所有线路访问该目标均不可用，等待检测恢复"))
                    return
                if not recover and len(available) == len(self._workers):
                    self._finish(context, FetchResult(url, None, 429, 0, 0,
                                 "rate_limited", "所有可用会话正在冷却"))
                    return
                self._condition.wait(timeout=0.1)

    def _finish(self, context, result):
        self._inflight.pop(context["key"], None)
        if context["attempts"] and context["last_lane"] is not None:
            lane = self.lanes[context["last_lane"]]
            if isinstance(getattr(lane, "network", None), NetworkMonitor):
                lane.network.completed(context["url"], result.ok, context["last_started"], context["last_error_type"])
        context["future"].set_result(replace(result, attempts=context["attempts"], elapsed_ms=context["elapsed"]))

    def _release_request(self, context, worker_index, done):
        with self._condition:
            self._busy.discard(worker_index)
            self._condition.notify_all()
            try:
                result, started = done.result()
            except BaseException as exc:
                self._inflight.pop(context["key"], None)
                context["future"].set_exception(exc)
                return
            context["attempts"] += result.attempts
            context["elapsed"] += result.elapsed_ms
            if result.attempts:
                context["last_lane"] = self._worker_lanes[worker_index]
                context["last_started"] = started
                context["last_error_type"] = result.error_type
            lane = self.lanes[self._worker_lanes[worker_index]]
            if (result.error_type in NETWORK_ERRORS and context["attempts"] < self.settings.retry.attempts
                    and not self._closed):
                self._retry_executor.submit(self._retry_request, context, lane)
                return
            self._finish(context, result)

    def _retry_request(self, context, lane):
        try:
            delay = lane._transport._retry_delay(context["attempts"])
            lane.log.info(f"网络失败，重新分配代理: source={self.source} attempts={context['attempts']} delay={delay:.2f}s")
            deadline = time.monotonic() + delay
            while time.monotonic() < deadline:
                if context["cancel_event"] is not None and context["cancel_event"].is_set():
                    raise CrawlStopped()
                if self._probe_stop.wait(min(.1, max(0, deadline - time.monotonic()))):
                    raise CrawlStopped()
            with self._condition:
                self._waiting += 1
            try:
                self._schedule(context)
            finally:
                with self._condition:
                    self._waiting -= 1
        except BaseException as exc:
            with self._condition:
                self._inflight.pop(context["key"], None)
                context["future"].set_exception(exc)

    def _execute(self, index, url, stage, recover, cancel_event=None, attempt_budget=None, retrying=False):
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
                result = lane.fetch(url, stage, attempt_budget=attempt_budget, retrying=retrying)
            attempts += result.attempts
            elapsed += result.elapsed_ms
            lane.log.debug(f"HTTP 会话结果: proxy_slot={index} stage={stage} status={result.status_code} "
                           f"attempts={result.attempts} error_type={result.error_type}")
            if not recover or not limited_result(result):
                return replace(result, attempts=attempts, elapsed_ms=elapsed), request_started
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
                manual = lane_index in self._manual_probes
                claim = self._manual_probes.pop(lane_index) if manual else lane.network.claim_probe()
                if not claim:
                    continue
                index = self._worker_lanes.index(lane_index)
                self._busy.add(index)
                future = self._workers[index].submit(self._probe, lane, claim, manual)
                def release(done, index=index):
                    with self._condition:
                        self._busy.discard(index)
                        self._condition.notify_all()
                future.add_done_callback(release)

    def check_network(self, url, proxy_slot=None):
        with self._condition:
            if self._closed:
                raise CrawlStopped()
            indices = range(len(self.lanes)) if proxy_slot is None else [proxy_slot]
            if proxy_slot is not None and not 0 <= proxy_slot < len(self.lanes):
                raise ValueError("代理编号不存在")
            queued = 0
            for index in indices:
                claim = self.lanes[index].network.claim_probe(url)
                if claim:
                    self._manual_probes[index] = claim
                    queued += 1
            self._dispatch_diagnostics()
            return queued

    def _probe(self, lane, claim, manual=False):
        with self._slots:
            if self._closed:
                lane.network.finish_probe(*claim)
                return
            lane.probe_network(*claim, check_control=manual)

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
        self._retry_executor.shutdown(wait=True)
        # close 在创建 Session 的同一常驻线程中执行。
        for worker in self._workers:
            worker.shutdown()


_POOLS = {}
_LOCK = threading.Lock()


def network_snapshot(pools=None):
    if pools is None:
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
