"""通知验证仅使用模拟发送器，不访问 Telegram 或下载远程图片。"""
import subprocess
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from notifications.memory_queue import MemoryNotificationQueue, NotificationJob, error_details
from scrapers.notification_manager import NotificationManager
from util.sendTelegram import TelegramSender


def job(key="42", source="sehuatang", images=0):
    return NotificationJob(source, key, "test title", {"fid": 103, "record": {
        "title": "test title", "number": "TEST-42", "code": "TEST-42",
        "post_time": "2026-09-08", "date": "2026-09-08", "magnet": "magnet:?xt=test",
        "img": [f"https://example.test/{i}.jpg" for i in range(images)],
    }})


def wait_for(predicate, timeout=2):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("通知线程未在限定时间达到预期状态")


class MemoryQueueTests(unittest.TestCase):
    def test_enqueue_does_not_wait_for_sender_and_copies_payload(self):
        started, release = threading.Event(), threading.Event()
        captured = []
        def sender(task):
            started.set()
            release.wait(2)
            captured.append((threading.current_thread().name, task.payload["record"]["title"]))
        queue = MemoryNotificationQueue(sender=sender)
        self.addCleanup(release.set)
        self.addCleanup(lambda: queue.close(0.1))
        task = job()
        self.assertTrue(queue.enqueue(task))
        self.assertTrue(started.wait(1))
        self.assertEqual([], captured)
        task.payload["record"]["title"] = "changed after enqueue"
        release.set()
        self.assertTrue(queue.close(2))
        self.assertEqual([("telegram-sender", "test title")], captured)

    def test_capacity_and_pending_deduplication(self):
        started, release = threading.Event(), threading.Event()
        def sender(task):
            started.set()
            release.wait(2)
        queue = MemoryNotificationQueue(capacity=1, sender=sender)
        queue.enqueue(job("a"))
        self.assertTrue(started.wait(1))
        self.assertTrue(queue.enqueue(job("a")))
        self.assertTrue(queue.enqueue(job("b")))
        self.assertFalse(queue.enqueue(job("c")))
        self.assertEqual((1, 1), (queue.snapshot()["queued"], queue.snapshot()["rejected"]))
        release.set()
        self.assertTrue(queue.close(2))
        self.assertEqual(2, queue.snapshot()["sent"])

    def test_retry_after_is_respected_and_attempts_are_bounded(self):
        error = RuntimeError("limited")
        error.error_code = 429
        error.result_json = {"parameters": {"retry_after": 3}}
        sender = mock.Mock(side_effect=[error, error, None])
        queue = MemoryNotificationQueue(sender=sender, max_attempts=3, retry_base=0)
        with mock.patch.object(queue._abort, "wait", return_value=False) as delay:
            queue.enqueue(job())
            self.assertTrue(queue.close(2))
        self.assertEqual([mock.call(3), mock.call(3)], delay.call_args_list)
        self.assertEqual("sent", queue.snapshot()["history"][0]["state"])
        self.assertEqual(3, sender.call_count)

    def test_failed_notification_can_requeue_with_progress(self):
        def sender(task):
            task.parts = [{}, {}]
            task.next_part = 1
            raise TimeoutError("network")
        queue = MemoryNotificationQueue(sender=sender, max_attempts=2, retry_base=0)
        queue.enqueue(job())
        wait_for(lambda: queue.snapshot()["failed"] == 1 and not queue.snapshot()["active"])
        failed = queue.snapshot()["history"][0]
        self.assertEqual(2, failed["attempts"])
        progress = []
        queue._sender = lambda task: progress.append(task.next_part)
        self.assertTrue(queue.retry(failed["id"]))
        self.assertTrue(queue.close(2))
        self.assertEqual([1], progress)
        self.assertEqual("requeued", queue.snapshot()["history"][1]["state"])

    def test_permanent_error_does_not_retry_or_expose_token(self):
        error = RuntimeError("https://api.telegram.org/bot123456:abcDEF_123/sendMessage denied")
        error.error_code = 403
        queue = MemoryNotificationQueue(sender=mock.Mock(side_effect=error), max_attempts=5)
        queue.enqueue(job())
        self.assertTrue(queue.close(2))
        result = queue.snapshot()["history"][0]
        self.assertEqual(1, result["attempts"])
        self.assertNotIn("123456:abcDEF_123", result["error"])

    def test_shutdown_interrupts_retry_wait_and_rejects_new_jobs(self):
        queue = MemoryNotificationQueue(sender=mock.Mock(side_effect=TimeoutError("offline")), retry_base=100)
        queue.enqueue(job())
        wait_for(lambda: (queue.snapshot()["active"] or {}).get("state") == "retry_wait")
        self.assertFalse(queue.close(0.01))
        self.assertFalse(queue.enqueue(job("new")))
        self.assertTrue(queue.close(2))
        self.assertEqual("interrupted", queue.snapshot()["history"][0]["state"])


class TelegramSenderTests(unittest.TestCase):
    def test_retry_resumes_at_failed_media_part(self):
        bot = mock.Mock()
        bot.send_media_group.return_value = [SimpleNamespace(message_id=1)]
        bot.send_photo.side_effect = [TimeoutError("temporary"), SimpleNamespace(message_id=2)]
        sender = TelegramSender(bot=bot, chat_id="test", min_interval=0)
        queue = MemoryNotificationQueue(sender=sender, retry_base=0)
        queue.enqueue(job(images=11))
        self.assertTrue(queue.close(2))
        self.assertEqual(1, bot.send_media_group.call_count)
        self.assertEqual(2, bot.send_photo.call_count)
        self.assertEqual(1, queue.snapshot()["sent"])

    def test_single_image_uses_photo_and_no_images_use_text(self):
        bot = mock.Mock()
        sender = TelegramSender(bot=bot, chat_id="test", min_interval=0)
        sender(job(images=1))
        sender(job(images=0))
        self.assertEqual(1, bot.send_photo.call_count)
        self.assertEqual(1, bot.send_message.call_count)
        bot.send_media_group.assert_not_called()

    def test_hotlinked_images_download_only_in_consumer(self):
        bot = mock.Mock()
        sender = TelegramSender(bot=bot, chat_id="test", min_interval=0)
        with mock.patch("util.sendTelegram._fetch_hotlinked_image", return_value=b"image") as fetch:
            sender(job(source="x1080x", images=1))
        fetch.assert_called_once_with("https://example.test/0.jpg")
        self.assertEqual(b"image", bot.send_photo.call_args.kwargs["photo"])

    def test_long_caption_is_sent_as_separate_text(self):
        bot = mock.Mock()
        sender = TelegramSender(bot=bot, chat_id="test", min_interval=0)
        task = job(images=1)
        task.payload["record"]["title"] = "long title " * 150
        sender(task)
        self.assertIsNone(bot.send_photo.call_args.kwargs["caption"])
        self.assertTrue(bot.send_message.called)

    def test_summary_sends_all_text_parts(self):
        bot = mock.Mock()
        sender = TelegramSender(bot=bot, chat_id="test", min_interval=0)
        task = NotificationJob("sehuatang", "summary", "summary",
                               {"fid": 103, "names": ["title" * 1000]}, kind="summary")
        sender(task)
        self.assertEqual(3, bot.send_message.call_count)

    def test_stopped_sender_does_not_start_another_part(self):
        event = threading.Event()
        event.set()
        bot = mock.Mock()
        sender = TelegramSender(bot=bot, chat_id="test", min_interval=0, stop_event=event)
        with self.assertRaises(InterruptedError):
            sender(job(images=1))
        bot.send_photo.assert_not_called()


class ProducerTests(unittest.TestCase):
    def test_disabled_notifications_do_not_construct_queue_or_bot(self):
        with mock.patch("scrapers.notification_manager.get_config", return_value=False), \
             mock.patch("scrapers.notification_manager.get_notification_queue") as factory:
            result = NotificationManager().enqueue_notifications([{"tid": "1"}], 103)
        self.assertEqual({"queued": 0, "rejected": 0}, result)
        factory.assert_not_called()

    def test_producer_only_enqueues_resources_and_summary(self):
        queue = mock.Mock()
        queue.enqueue.return_value = True
        with mock.patch("scrapers.notification_manager.get_config", return_value=True):
            result = NotificationManager(queue).enqueue_notifications([
                {"tid": "1", "title": "t1"}, {"tid": "2", "title": "t2"}], 103)
        self.assertEqual({"queued": 3, "rejected": 0}, result)
        self.assertEqual(["resource", "resource", "summary"],
                         [call.args[0].kind for call in queue.enqueue.call_args_list])

    def test_crawler_import_does_not_load_telegram_without_token(self):
        script = """
import yaml,sys
from pathlib import Path
from util.read_config import _config_manager
c=yaml.safe_load(Path('config/config.example.yaml').read_text(encoding='utf-8'))
c['mongodb'].update(enable=False,use_conn_str=False,db_host='127.0.0.1',db_port=27017)
c['sendMessage'].update(send_telegram_enable=False,tg_bot_token='')
_config_manager._config_cache=c
import scrapers.registry
assert 'telebot' not in sys.modules
import util.mongo
util.mongo.client.close()
"""
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_x1080x_enqueues_each_saved_batch_and_not_failed_writes(self):
        from scrapers.core.contracts import CrawlRecord, CrawlTarget
        from scrapers.sources.x1080x.repository import X1080XRepository
        order = []
        def save(payloads):
            order.append("saved")
            return {"upserted": len(payloads)}
        repository = X1080XRepository(lambda keys: set(), save,
                                      on_saved=lambda payloads: order.append("queued"))
        record = CrawlRecord(CrawlTarget("1", "https://example.test/1"), {"source_key": "1"})
        repository.save_many([record])
        repository.save_many([record])
        self.assertEqual(["saved", "queued", "saved", "queued"], order)
        repository._save_func = mock.Mock(side_effect=RuntimeError("db failed"))
        with self.assertRaises(RuntimeError):
            repository.save_many([record])
        self.assertEqual(4, len(order))


class RunnerLifecycleTests(unittest.TestCase):
    def test_single_command_drains_queue_before_exit(self):
        import run
        for mode, drain in [("once", True), ("web", False), ("health", False)]:
            runner = mock.Mock()
            with self.subTest(mode=mode), mock.patch.object(run, "ApplicationRunner", return_value=runner), \
                 mock.patch.object(sys, "argv", ["run.py", "--mode", mode]), \
                 self.assertRaises(SystemExit):
                run.main()
            runner.stop.assert_called_once_with(drain_notifications=drain)

    def test_legacy_main_entry_also_drains_on_normal_completion(self):
        import main
        for exception, drain in [(None, True), (RuntimeError("stopped"), False)]:
            with mock.patch.object(main, "main", mock.AsyncMock(side_effect=exception)), \
                 mock.patch("notifications.memory_queue.shutdown_notifications") as shutdown:
                if exception:
                    with self.assertRaises(RuntimeError):
                        main._run_standalone()
                else:
                    main._run_standalone()
                shutdown.assert_called_once_with(drain=drain)


if __name__ == "__main__":
    unittest.main()
