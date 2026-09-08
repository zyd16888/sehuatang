"""采集端通知入口：只构造任务并入队，不导入 Telegram 或执行网络请求。"""
import hashlib
from typing import List, Dict, Any

from notifications.memory_queue import NotificationJob, get_notification_queue
from util.log_util import log
from util.read_config import get_config


_FIELDS = ("title", "number", "code", "magnet", "magnet_115", "post_time", "date", "img", "section")


class NotificationManager:
    def __init__(self, notification_queue=None):
        self._queue = notification_queue

    def is_notification_enabled(self):
        return bool(get_config("sendMessage.send_telegram_enable", False))

    def _enqueue(self, source, data_list, fid=None):
        if not data_list or not self.is_notification_enabled():
            return {"queued": 0, "rejected": 0}
        accepted = rejected = 0
        try:
            queue = self._queue or get_notification_queue()
            for data in data_list:
                key = str(data.get("source_key") or data.get("tid"))
                job = NotificationJob(source, key, str(data.get("title") or key),
                    {"record": {field: data.get(field) for field in _FIELDS}, "fid": fid})
                if queue.enqueue(job):
                    accepted += 1
                else:
                    rejected += 1
            if source == "sehuatang":
                keys = ",".join(str(data.get("tid")) for data in data_list)
                key = f"{fid}:" + hashlib.sha256(keys.encode()).hexdigest()[:20]
                job = NotificationJob(source, key, f"板块 {fid} 采集汇总",
                    {"fid": fid, "names": [f"{data.get('number') or ''} {data.get('title') or ''}".strip()
                                            for data in data_list]}, kind="summary")
                if queue.enqueue(job):
                    accepted += 1
                else:
                    rejected += 1
        except Exception as exc:
            log.error(f"通知入队失败（不影响采集结果）: source={source} error={type(exc).__name__}")
            rejected = max(rejected, len(data_list) - accepted)
        log.info(f"通知入队: source={source} queued={accepted} rejected={rejected}")
        return {"queued": accepted, "rejected": rejected}

    def enqueue_notifications(self, data_list: List[Dict[str, Any]], fid: int):
        return self._enqueue("sehuatang", data_list, fid)

    def enqueue_x1080x_notifications(self, data_list: List[Dict[str, Any]]):
        return self._enqueue("x1080x", data_list)
