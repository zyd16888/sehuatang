"""有界内存队列与单个发送线程。退出后不恢复，发送失败不影响采集。"""
import copy
import queue
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

from util.log_util import log
from util.read_config import get_config


@dataclass
class NotificationJob:
    source: str
    key: str
    title: str
    payload: dict
    kind: str = "resource"
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.time)
    attempts: int = 0
    state: str = "queued"
    error: str = ""
    next_retry_at: float | None = None
    parts: list | None = None
    next_part: int = 0
    message_ids: list = field(default_factory=list)

    def summary(self):
        return {"id": self.id, "source": self.source, "key": self.key,
                "title": self.title, "kind": self.kind, "created_at": self.created_at,
                "attempts": self.attempts, "state": self.state, "error": self.error,
                "next_retry_at": self.next_retry_at, "completed_parts": self.next_part,
                "total_parts": len(self.parts) if self.parts is not None else (self.next_part if self.state == "sent" else None)}


def error_details(exc):
    code = getattr(exc, "error_code", None)
    result = getattr(exc, "result_json", None) or {}
    delay = float((result.get("parameters") or {}).get("retry_after") or 0)
    retryable = code == 429 or code is None or int(code) >= 500
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        retryable = False
    message = re.sub(r"(?:bot)?\d+:[A-Za-z0-9_-]+", "<redacted-token>", str(exc))[:500]
    return retryable, delay, message


class MemoryNotificationQueue:
    def __init__(self, *, capacity=1000, max_attempts=5, retry_base=5,
                 sender=None, history_limit=100):
        if capacity < 1 or max_attempts < 1 or retry_base < 0:
            raise ValueError("通知队列容量和尝试上限必须大于零，退避不能为负数")
        self.capacity = capacity
        self.max_attempts = max_attempts
        self.retry_base = retry_base
        self._queue = queue.Queue(maxsize=capacity)
        self._lock = threading.RLock()
        self._history = deque(maxlen=history_limit)
        self._pending_keys = set()
        self._sender = sender
        self._thread = None
        self._active = None
        self._closing = False
        self._abort = threading.Event()
        self._sent = 0
        self._failed = 0
        self._rejected = 0

    def enqueue(self, job):
        """立即返回；容量用完时显式拒绝，不阻塞采集线程。"""
        with self._lock:
            identity = (job.source, job.kind, job.key)
            if identity in self._pending_keys:
                return True
            if self._closing:
                self._rejected += 1
                log.warning(f"通知队列已关闭，未入队: source={job.source} key={job.key}")
                return False
            try:
                self._queue.put_nowait(copy.deepcopy(job))
            except queue.Full:
                self._rejected += 1
                log.warning(f"通知队列已满，未入队: source={job.source} key={job.key}")
                return False
            self._pending_keys.add(identity)
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="telegram-sender", daemon=True)
                self._thread.start()
            return True

    def _send(self, job):
        if self._sender is None:
            # Bot、图片下载与 Telegram 依赖只在消费者线程中加载。
            from util.sendTelegram import TelegramSender
            self._sender = TelegramSender(stop_event=self._abort)
        self._sender(job)

    def _run(self):
        while not self._abort.is_set():
            try:
                job = self._queue.get(timeout=0.1)
            except queue.Empty:
                with self._lock:
                    if self._closing:
                        return
                continue
            with self._lock:
                self._active = job
            try:
                self._deliver(job)
            finally:
                with self._lock:
                    self._active = None
                    self._pending_keys.discard((job.source, job.kind, job.key))
                    self._history.append(job)
                self._queue.task_done()

    def _deliver(self, job):
        for attempt in range(1, self.max_attempts + 1):
            if self._abort.is_set():
                job.state = "interrupted"
                return
            with self._lock:
                job.attempts = attempt
                job.state = "sending"
                job.next_retry_at = None
            try:
                self._send(job)
            except Exception as exc:
                retryable, retry_after, message = error_details(exc)
                with self._lock:
                    job.error = message
                    if not retryable or attempt == self.max_attempts:
                        job.state = "failed"
                        self._failed += 1
                        log.error(f"通知发送失败: source={job.source} key={job.key} attempts={attempt} error={message}")
                        return
                    delay = max(retry_after, min(300, self.retry_base * 2 ** (attempt - 1)))
                    job.state = "retry_wait"
                    job.next_retry_at = time.time() + delay
                if self._abort.wait(delay):
                    job.state = "interrupted"
                    return
            else:
                with self._lock:
                    job.state = "sent"
                    job.error = ""
                    self._sent += 1
                    # 成功历史只保留展示信息；失败任务保留 URL/文本供当前进程重试。
                    job.payload = {}
                    job.parts = None
                return

    def retry(self, job_id):
        with self._lock:
            job = next((item for item in self._history if item.id == job_id and item.state == "failed"), None)
            if job is None:
                return False
            resumed = copy.deepcopy(job)
            resumed.id = uuid.uuid4().hex
            resumed.created_at = time.time()
            resumed.attempts = 0
            resumed.error = ""
            resumed.state = "queued"
            resumed.next_retry_at = None
            if not self.enqueue(resumed):
                return False
            job.state = "requeued"
            return True

    def snapshot(self):
        with self._lock:
            with self._queue.mutex:
                pending = [job.summary() for job in list(self._queue.queue)[:100]]
                queued = len(self._queue.queue)
            return {"queued": queued, "capacity": self.capacity,
                    "active": self._active.summary() if self._active else None,
                    "sent": self._sent, "failed": self._failed, "rejected": self._rejected,
                    "max_attempts": self.max_attempts, "closing": self._closing,
                    "pending": pending, "history": [job.summary() for job in reversed(self._history)]}

    def close(self, timeout=30):
        """正常 CLI 可传 None 等待清空；服务关闭到期后停止继续取任务。"""
        with self._lock:
            self._closing = True
            worker = self._thread
        if worker is None:
            return True
        worker.join(timeout=timeout)
        if worker.is_alive():
            self._abort.set()
            state = self.snapshot()
            log.warning(f"通知队列关闭等待超时，未完成任务将在进程退出后丢失: queued={state['queued']} active={bool(state['active'])}")
            return False
        return self._queue.unfinished_tasks == 0


_instance = None
_instance_lock = threading.Lock()


def get_notification_queue():
    global _instance
    with _instance_lock:
        if _instance is None:
            settings = get_config("sendMessage.queue", {}) or {}
            _instance = MemoryNotificationQueue(
                capacity=int(settings.get("capacity", 1000)),
                max_attempts=int(settings.get("max_attempts", 5)),
            )
        return _instance


def shutdown_notifications(drain=False):
    # 未启用通知或从未入队时，不创建队列或 Telegram 客户端。
    with _instance_lock:
        instance = _instance
    if instance is None:
        return True
    timeout = None if drain else max(0, float(get_config("sendMessage.queue.shutdown_timeout_seconds", 30)))
    return instance.close(timeout=timeout)
