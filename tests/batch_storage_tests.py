"""批量写入、独立刷新时限、反压及页确认的离线行为测试。"""
import asyncio
import threading
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pymongo.errors import AutoReconnect, BulkWriteError

from scrapers.core.config import StorageSettings, load_storage_settings, HttpSettings, ProxySettings
from scrapers.core.contracts import CrawlTarget, CrawlRecord, DiscoveryResult, SaveResult
from scrapers.core.engine import CrawlEngine
from scrapers.core.models import FetchResult
from scrapers.core.pool import SessionPool
from scrapers.core.rate_limit import BackfillHttpClient, CrawlStopped
from scrapers.core.storage import BatchWriter, PendingWrite


def item(key):
    return PendingWrite(str(key), record={"key": str(key)})


class BatchWriterTests(unittest.TestCase):
    def test_size_flush_and_final_partial_batch(self):
        batches = []
        with BatchWriter("test", lambda rows: batches.append([row.key for row in rows])) as writer:
            for key in range(23):
                writer.submit(item(key))
        self.assertEqual([10, 10, 3], [len(rows) for rows in batches])
        self.assertEqual(list(map(str, range(23))), sum(batches, []))
        self.assertEqual(3, writer.batches)

    def test_interval_flush_without_another_producer_result(self):
        persisted = threading.Event()
        batches = []
        def write(rows):
            batches.append([row.key for row in rows])
            persisted.set()
        with BatchWriter("test", write, settings=StorageSettings(flush_interval_seconds=0.05)) as writer:
            writer.submit(item("only"))
            self.assertTrue(persisted.wait(1), "抓取线程不再产出时也应落库")
            self.assertEqual([["only"]], batches)

    def test_slow_writer_backpressures_bounded_queue_and_deduplicates(self):
        entered, release, third_done = threading.Event(), threading.Event(), threading.Event()
        saved = []
        def write(rows):
            entered.set()
            if not release.wait(3):
                raise AssertionError("test writer timeout")
            saved.extend(row.key for row in rows)
        writer = BatchWriter("test", write, settings=StorageSettings(batch_size=1, queue_capacity=1))
        with ThreadPoolExecutor(max_workers=1) as executor:
            try:
                writer.submit(item(1))
                self.assertTrue(entered.wait(1))
                self.assertFalse(writer.submit(item(1)))
                writer.submit(item(2))
                self.assertFalse(writer.submit(item(2)))
                def third():
                    writer.submit(item(3))
                    third_done.set()
                pending = executor.submit(third)
                self.assertFalse(third_done.wait(0.08))
                self.assertEqual(1, writer.queue.qsize())
            finally:
                release.set()
            pending.result(2)
            writer.close()
        self.assertEqual(["1", "2", "3"], saved)

    def test_writer_failure_unblocks_producer_and_keeps_unconfirmed_batch(self):
        entered, release = threading.Event(), threading.Event()
        def write(rows):
            entered.set()
            release.wait(3)
            raise OSError("write unavailable")
        writer = BatchWriter("test", write, settings=StorageSettings(batch_size=1, queue_capacity=1))
        with ThreadPoolExecutor(max_workers=1) as executor:
            try:
                writer.submit(item(1))
                self.assertTrue(entered.wait(1))
                writer.submit(item(2))
                pending = executor.submit(writer.submit, item(3))
            finally:
                release.set()
            with self.assertRaisesRegex(OSError, "write unavailable"):
                pending.result(2)
        with self.assertRaises(OSError):
            writer.close()
        self.assertEqual(["1"], [row.key for row in writer.unconfirmed_batch])
        self.assertEqual(0, writer.batches)

    def test_stop_flushes_admitted_tail_before_propagating(self):
        saved = []
        with self.assertRaises(CrawlStopped):
            with BatchWriter("test", lambda rows: saved.extend(row.key for row in rows)) as writer:
                writer.submit(item(1))
                raise CrawlStopped()
        self.assertEqual(["1"], saved)

    def test_shutdown_drains_registered_writer_without_waiting_for_producer(self):
        from scrapers.core.storage import drain_writers
        saved = []
        writer = BatchWriter("test", lambda rows: saved.extend(row.key for row in rows))
        writer.submit(item(1))
        drain_writers()
        self.assertEqual(["1"], saved)
        with self.assertRaises(RuntimeError):
            writer.submit(item(2))


class EngineStorageTests(unittest.TestCase):
    def source(self, keys):
        return SimpleNamespace(name="test", discover=lambda *args: DiscoveryResult([
            CrawlTarget(key, "https://site.test/" + key) for key in keys]),
            parse_detail=lambda target, result: CrawlRecord(target, {"key": target.key}))

    def http(self):
        return SimpleNamespace(fetch_many=lambda urls, stage: [FetchResult(url, b"ok", 200, 1, 0) for url in urls])

    def repository(self, save):
        return SimpleNamespace(select_targets=lambda rows: rows, save_many=save)

    def test_default_twenty_results_use_two_writes_and_two_ledger_clears(self):
        batches = []
        def save(rows):
            batches.append([row.target.key for row in rows])
            return SaveResult(processed=len(rows), saved=len(rows))
        ledger = Mock()
        result = CrawlEngine(self.http(), ledger).run(self.source(list(map(str, range(20)))), self.repository(save))
        self.assertEqual([10, 10], [len(batch) for batch in batches])
        self.assertEqual(2, ledger.clear.call_count)
        self.assertEqual(20, result.saved)
        self.assertEqual(2, result.details["write_batches"])

    def test_fetching_next_group_continues_while_writer_is_busy(self):
        writing, next_requested = threading.Event(), threading.Event()
        class Http:
            def iter_completed(self, urls):
                if urls[0].endswith("b"):
                    next_requested.set()
                yield 0, FetchResult(urls[0], b"ok", 200, 1, 0)
                if urls[0].endswith("a"):
                    assert writing.wait(2)
        def save(rows):
            if rows[0].target.key == "a":
                writing.set()
                assert next_requested.wait(2), "写库不能阻塞下一组抓取"
            return SaveResult(saved=len(rows))
        result = CrawlEngine(Http(), storage_settings=StorageSettings(batch_size=1)).run(
            self.source(["a", "b"]), self.repository(save), batch_size=1)
        self.assertEqual(2, result.saved)

    def test_connection_retry_replays_same_idempotent_batch_and_clears_once(self):
        persisted, calls = {}, []
        def save(rows):
            calls.append([row.target.key for row in rows])
            for row in rows:
                persisted[row.target.key] = row.payload
            if len(calls) == 1:
                raise AutoReconnect("ack lost")
            return SaveResult(processed=len(rows), saved=0, updated=len(rows))
        ledger = Mock()
        result = CrawlEngine(self.http(), ledger, StorageSettings(retry_delay_seconds=0)).run(
            self.source(["a", "b"]), self.repository(save))
        self.assertEqual([["a", "b"], ["a", "b"]], calls)
        self.assertEqual(2, len(persisted))
        ledger.clear.assert_called_once_with("test", ["a", "b"])
        self.assertEqual(2, result.updated)

    def test_partial_bulk_error_does_not_ack_page_or_replay_failure_counts(self):
        save = Mock(side_effect=BulkWriteError({"nUpserted": 1, "writeErrors": [{"index": 1}]}))
        with self.assertRaises(BulkWriteError):
            CrawlEngine(self.http()).run(self.source(["a", "b"]), self.repository(save))
        save.assert_called_once()
        ledger = Mock()
        ledger.record.side_effect = AutoReconnect("ledger ack lost")
        http = SimpleNamespace(fetch_many=lambda urls, stage: [FetchResult(url, None, 500, 1, 0, "http_status") for url in urls])
        with self.assertRaises(AutoReconnect):
            CrawlEngine(http, ledger, StorageSettings(retry_delay_seconds=0)).run(
                self.source(["failed"]), self.repository(Mock()))
        ledger.record.assert_called_once()

    def test_dry_run_has_no_storage_effects(self):
        save, ledger = Mock(), Mock()
        result = CrawlEngine(self.http(), ledger).run(self.source(["a", "b"]), self.repository(save), dry_run=True)
        save.assert_not_called()
        ledger.record.assert_not_called()
        ledger.clear.assert_not_called()
        self.assertEqual((2, 0, 0), (result.succeeded, result.saved, result.details["write_batches"]))

    def test_storage_failure_interrupts_long_cooldown_without_closing_shared_pool(self):
        from curl_cffi import requests
        entered = threading.Event()
        cfg = HttpSettings(concurrency=2, proxy=ProxySettings(True, urls=("http://proxy:1001", "http://proxy:1002")),
                           cooldown_seconds=60)
        class Session:
            cookies = requests.Cookies()
            def get(self, url, **kwargs):
                if url.endswith("blocked"):
                    entered.set()
                    return SimpleNamespace(status_code=429, content=b"Too Many Requests", cookies=requests.Cookies(), headers={})
                assert entered.wait(2)
                return SimpleNamespace(status_code=200, content=b"ok", cookies=requests.Cookies(), headers={})
            def close(self): pass
        with patch("scrapers.core.session.requests.Session", side_effect=Session):
            pool = SessionPool("test", cfg)
            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    engine = CrawlEngine(BackfillHttpClient(pool), storage_settings=StorageSettings(batch_size=1))
                    future = executor.submit(engine.run, self.source(["blocked", "good"]),
                                             self.repository(Mock(side_effect=ValueError("cannot save"))))
                    with self.assertRaisesRegex(ValueError, "cannot save"):
                        future.result(3)
                self.assertFalse(pool._closed)
            finally:
                pool.close()


class StorageConfigTests(unittest.TestCase):
    def test_defaults_source_override_and_invalid_values(self):
        self.assertEqual(StorageSettings(), load_storage_settings({}, "test"))
        cfg = {"crawler": {"defaults": {"storage": {"batch_size": 5, "queue_capacity": 20}},
                           "sources": {"x1080x": {"storage": {"batch_size": 8}}}}}
        self.assertEqual(8, load_storage_settings(cfg, "x1080x").batch_size)
        self.assertEqual(5, load_storage_settings(cfg, "javbee").batch_size)
        self.assertEqual(20, load_storage_settings(cfg, "sehuatang").queue_capacity)
        for raw in ({"batch_size": 0}, {"queue_capacity": -1}, {"retry_attempts": 0},
                    {"flush_interval_seconds": 0}, {"flush_interval_seconds": float("inf")},
                    {"retry_delay_seconds": -1}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                load_storage_settings({"crawler": {"defaults": {"storage": raw}}}, "test")


class SourcePipelineTests(unittest.TestCase):
    def sehuatang(self, *, failure=False):
        from scrapers.web_scraper import WebScraper
        from scrapers.data_processor import DataProcessor
        scraper = WebScraper.__new__(WebScraper)
        scraper.log = Mock()
        scraper.workers = 4
        scraper.dry_run = False
        scraper.storage_settings = StorageSettings(retry_delay_seconds=0)
        scraper.failure_store = Mock()
        scraper.data_processor = DataProcessor(date_filter=False)
        scraper.page_parser = SimpleNamespace(parse_thread_page=lambda body: {
            "post_time": "2026-09-09 12:00", "magnet": "magnet:?test", "img": []})
        self.saved, self.save_batches = [], []
        def save(rows, fid, strict=False, stats=None):
            self.save_batches.append([row["tid"] for row in rows])
            if failure:
                raise OSError("database unavailable")
            self.saved.extend(rows)
            if stats is not None:
                stats.update(saved=len(rows), existing=0)
            return rows
        scraper.data_manager = SimpleNamespace(filter_and_save_data=save)
        class Http:
            settings = HttpSettings()
            def iter_completed(self, urls):
                for index, url in enumerate(urls):
                    yield index, FetchResult(url, b"ok", 200, 1, 0)
        scraper.http = Http()
        return scraper

    def infos(self, size):
        return [{"tid": str(i), "fid": 103, "number": "TEST", "title": "title", "date": "2026-09-09"}
                for i in range(size)]

    def test_sehuatang_incremental_and_backfill_share_batched_writer(self):
        from scrapers.web_scraper import _new_run_stats
        scraper = self.sehuatang()
        summary = _new_run_stats()
        saved = scraper._process_detail_targets(self.infos(20), 103, summary)
        self.assertEqual([10, 10], [len(batch) for batch in self.save_batches])
        self.assertEqual((20, 20), (len(saved), summary["saved"]))
        self.assertEqual(2, scraper.failure_store.clear.call_count)
        self.save_batches.clear()
        saved, failed = scraper._save_backfill_details(self.infos(13), 103)
        self.assertEqual([10, 3], [len(batch) for batch in self.save_batches])
        self.assertEqual((13, 0), (len(saved), failed))

    def test_sehuatang_failed_writer_prevents_page_return_and_dry_run_writes_nothing(self):
        scraper = self.sehuatang(failure=True)
        with self.assertRaises(OSError):
            scraper._save_backfill_details(self.infos(2), 103)
        scraper.dry_run = True
        self.save_batches.clear()
        saved, failed = scraper._save_backfill_details(self.infos(2), 103)
        self.assertEqual(([], 0), (saved, failed))
        self.assertEqual([], self.save_batches)
        scraper.failure_store.record.assert_not_called()

    def test_x1080x_checkpoint_waits_for_resource_and_failure_confirmation(self):
        from scrapers.x1080x_scraper import X1080XScraper
        from scrapers.page_backfill import PageCheckpointStore
        from tests.x1080x_tests import LIST_HTML, DETAIL_HTML, EMPTY_LIST_HTML
        entered, release = threading.Event(), threading.Event()
        scraper = X1080XScraper.__new__(X1080XScraper)
        scraper.config = {"base_url": "https://site.test", "fid": 244, "typeids": {"5479": "test"}}
        scraper.storage_settings = StorageSettings()
        scraper.failure_store = Mock()
        class Http:
            def fetch(self, url, stage="detail"):
                body = (EMPTY_LIST_HTML if "page=2" in url else LIST_HTML) if stage == "list" else DETAIL_HTML
                return FetchResult(url, body, 200, 1, 0)
            def fetch_many(self, urls, stage="detail"):
                return [self.fetch(url, stage) for url in urls]
        scraper.http = Http()
        def save(rows):
            entered.set()
            assert release.wait(3)
            return {"processed": len(rows), "upserted": len(rows), "modified": 0}
        with tempfile.TemporaryDirectory() as temp:
            store = PageCheckpointStore(Path(temp) / "progress.json")
            with patch("scrapers.x1080x_scraper.find_existing_x1080x_keys", return_value=set()), \
                 patch("scrapers.x1080x_scraper.save_x1080x_items", side_effect=save), \
                 ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(scraper.backfill_pages, 1, 2, checkpoint_store=store)
                try:
                    self.assertTrue(entered.wait(2))
                    self.assertEqual(0, store.load("x1080x", "5479"))
                    self.assertFalse(future.done())
                finally:
                    release.set()
                future.result(3)
                self.assertEqual(1, store.load("x1080x", "5479"))
            store.clear("x1080x", "5479")
            scraper.failure_store.record.side_effect = OSError("ledger unavailable")
            scraper.http.fetch_many = lambda urls, stage="detail": [
                FetchResult(url, None, 500, 1, 0, "http_status") for url in urls]
            with patch("scrapers.x1080x_scraper.find_existing_x1080x_keys", return_value=set()):
                with self.assertRaises(OSError):
                    scraper.backfill_pages(1, 1, checkpoint_store=store)
            self.assertEqual(0, store.load("x1080x", "5479"))


if __name__ == "__main__":
    unittest.main()
