"""单生产者、有界队列、独立计时的批量写入器。"""
import queue
import atexit
import threading
import time
from dataclasses import dataclass

from pymongo.errors import ConnectionFailure

from .config import StorageSettings
from util.log_util import log

_WRITERS = set()
_WRITERS_LOCK = threading.Lock()


@dataclass(frozen=True)
class PendingWrite:
    key: str
    record: object = None
    failure: object = None


def retry_resource_write(operation, settings, logger):
    """仅重试幂等资源保存，不能把失败计数或通知副作用包进此循环。"""
    for attempt in range(1, settings.retry_attempts + 1):
        try:
            return operation()
        except ConnectionFailure:
            if attempt == settings.retry_attempts:
                raise
            delay = settings.retry_delay_seconds * attempt
            logger.warning(f"资源写入连接异常，重试原批次: attempt={attempt} delay={delay}s")
            time.sleep(delay)


class BatchWriter:
    def __init__(self, source, write_batch, *, settings=None, context=""):
        self.settings = settings or StorageSettings()
        self.settings.validate()
        self.log = log.bind(module=source)
        self.context = context
        self.write_batch = write_batch
        self.queue = queue.Queue(maxsize=self.settings.queue_capacity)
        self.failed = threading.Event()
        self._closing = threading.Event()
        self._pending_lock = threading.Lock()
        self._admission_lock = threading.Lock()
        self._pending_keys = set()
        self._thread = None
        self._error = None
        self.unconfirmed_batch = ()
        self.batches = 0
        self.persist_ms = 0
        self.queue_wait_ms = 0
        with _WRITERS_LOCK:
            _WRITERS.add(self)

    def check(self):
        if self.failed.is_set():
            raise self._error

    def submit(self, item):
        self.check()
        if self._closing.is_set():
            raise RuntimeError("写入器已关闭")
        with self._pending_lock:
            if item.key in self._pending_keys:
                return False
            self._pending_keys.add(item.key)
        started = time.monotonic()
        try:
            while True:
                self.check()
                with self._admission_lock:
                    if self._closing.is_set():
                        raise RuntimeError("写入器已关闭")
                    if self._thread is None:
                        self._thread = threading.Thread(target=self._run, name="crawler-batch-writer", daemon=True)
                        self._thread.start()
                    try:
                        self.queue.put_nowait(item)
                        break
                    except queue.Full:
                        pass
                self.failed.wait(0.05)
        except BaseException:
            with self._pending_lock:
                self._pending_keys.discard(item.key)
            raise
        finally:
            self.queue_wait_ms += int((time.monotonic() - started) * 1000)
        self.check()
        return True

    def _flush(self, batch, reason):
        self.unconfirmed_batch = tuple(batch)
        started = time.monotonic()
        self.write_batch(batch)
        elapsed = int((time.monotonic() - started) * 1000)
        self.persist_ms += elapsed
        self.batches += 1
        with self._pending_lock:
            self._pending_keys.difference_update(item.key for item in batch)
        self.unconfirmed_batch = ()
        self.log.info(f"写入批次完成: {self.context} records={sum(item.record is not None for item in batch)} "
                      f"failures={sum(item.failure is not None for item in batch)} "
                      f"persist_ms={elapsed} queued={self.queue.qsize()} reason={reason}")

    def _run(self):
        batch = []
        deadline = None
        try:
            while True:
                if batch and (len(batch) >= self.settings.batch_size or time.monotonic() >= deadline):
                    self._flush(batch, "size" if len(batch) >= self.settings.batch_size else "interval")
                    batch = []
                    deadline = None
                if self._closing.is_set() and self.queue.empty():
                    if batch:
                        self._flush(batch, "finish")
                    return
                timeout = min(0.05, max(0, deadline - time.monotonic())) if batch else 0.05
                try:
                    item = self.queue.get(timeout=timeout)
                except queue.Empty:
                    continue
                if not batch:
                    deadline = time.monotonic() + self.settings.flush_interval_seconds
                batch.append(item)
        except BaseException as exc:
            self.unconfirmed_batch = tuple(batch)
            self._error = exc
            self.failed.set()
            self.log.error(f"批量写入未确认，停止抓取且不推进检查点: {self.context} "
                           f"buffered={len(batch)} queued={self.queue.qsize()} error_type={type(exc).__name__}")

    def close(self):
        with self._admission_lock:
            self._closing.set()
        try:
            if self._thread is not None:
                self._thread.join()
            self.check()
        finally:
            with _WRITERS_LOCK:
                _WRITERS.discard(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def completed_results(http, urls, cancel_event):
    if hasattr(type(http), "iter_completed"):
        if getattr(type(http), "supports_cancellation", False):
            yield from http.iter_completed(urls, cancel_event=cancel_event)
        else:
            yield from http.iter_completed(urls)
    else:
        yield from enumerate(http.fetch_many(urls, stage="detail"))


def drain_writers():
    """停止 HTTP 后调用，等待手工后台任务已入队的结果完成落库。"""
    with _WRITERS_LOCK:
        writers = list(_WRITERS)
    for writer in writers:
        try:
            writer.close()
        except Exception as exc:
            writer.log.error(f"退出时写入仍未确认，保留原检查点: error_type={type(exc).__name__}")


atexit.register(drain_writers)
