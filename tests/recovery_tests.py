"""离线回归：失败生命周期、历史恢复、并发验证与调度状态。"""
import asyncio
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from scrapers.core.config import HttpSettings
from scrapers.core.contracts import CrawlFailure
from scrapers.core.models import FetchResult
from scrapers.data_processor import DataProcessor
from scrapers.http_client import HttpClient
from scrapers.infrastructure.json_failures import JsonFailureStore
from scrapers.registry import SourceDefinition, SourceRegistry
from scrapers.sources.x1080x.http_client import X1080XHttpClient, shared_http_client
from scrapers.web_scraper import WebScraper
from util.failure_policy import describe_failure, retry_minutes


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = JsonFailureStore(Path(self.tmp.name) / "failures.json")
        patch = mock.patch("util.failure_policy.get_config", return_value=5)
        patch.start()
        self.addCleanup(patch.stop)
        self.failure = CrawlFailure("sehuatang", "42", "https://example.test/42",
                                    "validate", 3, "invalid_record", metadata={"fid": 103})

    def make_due(self):
        rows = self.store._load()
        for row in rows:
            row["next_retry_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        self.store._save(rows)

    def test_limit_survives_reload_and_manual_requeue_preserves_total(self):
        delays = []
        for _ in range(5):
            self.store.record([self.failure])
            row = self.store._load()[0]
            if row["next_retry_at"]:
                delays.append(round((datetime.fromisoformat(row["next_retry_at"]) -
                                     datetime.fromisoformat(row["last_failed_at"])).total_seconds()/60))
        self.assertEqual([5, 10, 20, 40], delays)
        self.make_due()  # 旧版遗留的 next_retry_at 也不能绕过上限。
        self.store = JsonFailureStore(self.store.path)
        self.assertEqual([], self.store.due_targets("sehuatang"))
        self.assertEqual(1, self.store.snapshot()["counts"]["exhausted"])
        self.assertIsNone(self.store.snapshot()["failures"][0]["next_retry_at"])
        self.assertTrue(self.store.requeue("sehuatang", "42", "validate"))
        self.assertEqual(1, len(self.store.due_targets("sehuatang")))
        self.store.record([self.failure])
        row = self.store.snapshot()["failures"][0]
        self.assertEqual((6, 1, "waiting"), (row["failure_count"], row["retry_count"], row["state"]))

    def test_existing_over_limit_rows_and_filter_counts(self):
        self.store.record([self.failure])
        rows = self.store._load()
        rows[0]["failure_count"] = 12
        self.store._save(rows)
        self.make_due()
        data = self.store.snapshot(source="sehuatang", state="due")
        self.assertEqual([], data["failures"])
        self.assertEqual({}, data["due_counts"])
        self.assertEqual(1, data["counts"]["exhausted"])
        self.assertEqual(1440, retry_minutes(1000000))

    def test_naive_mongo_dates_have_same_state_as_json(self):
        now = datetime.now(timezone.utc)
        row = {"failure_count": 4, "next_retry_at": (now - timedelta(seconds=1)).replace(tzinfo=None)}
        self.assertEqual("due", describe_failure(row, 5, now)["state"])
        row["failure_count"] = 5
        self.assertEqual("exhausted", describe_failure(row, 5, now)["state"])

    def test_parallel_json_instances_do_not_lose_counts(self):
        stores = [JsonFailureStore(self.store.path) for _ in range(4)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda store: store.record([self.failure]), stores))
        self.assertEqual(4, self.store._load()[0]["failure_count"])

    def test_stage_change_cannot_leave_an_old_row_retrying_forever(self):
        self.store.record([self.failure])
        changed = replace(self.failure, stage="fetch", error_type="timeout",
                          metadata={"retry_stage": "validate", "fid": 103})
        for _ in range(4):
            self.store.record([changed])
        self.make_due()
        self.assertEqual([], self.store.due_targets("sehuatang"))
        row = self.store.snapshot()["failures"][0]
        self.assertEqual(("validate", "fetch", 5),
                         (row["stage"], row["last_stage"], row["retry_count"]))

    def test_multiple_legacy_stages_request_a_target_only_once(self):
        self.store.record([self.failure, replace(self.failure, stage="fetch")])
        self.make_due()
        self.assertEqual(1, len(self.store.due_targets("sehuatang")))

    def test_bulk_requeue_only_exhausted_rows_of_selected_source(self):
        for _ in range(5):
            self.store.record([self.failure, replace(self.failure, source="javbee")])
        self.store.record([replace(self.failure, key="waiting")])
        self.assertEqual(1, self.store.requeue_exhausted("sehuatang"))
        self.assertEqual(0, self.store.requeue_exhausted("sehuatang"))
        self.assertEqual(1, self.store.snapshot(source="javbee")["counts"]["exhausted"])
        self.assertEqual(1, self.store.snapshot(source="sehuatang")["counts"]["waiting"])


class HistoricalRecoveryTests(unittest.TestCase):
    def scraper(self):
        scraper = WebScraper.__new__(WebScraper)
        scraper.log = mock.Mock()
        scraper.workers = 1
        scraper.dry_run = False
        scraper.http = mock.Mock(settings=HttpSettings())
        scraper.failure_store = mock.Mock()
        scraper.data_processor = DataProcessor("2026-09-08")
        scraper._fetch_many = lambda urls: [b"body"] * len(urls)
        scraper.page_parser = mock.Mock()
        scraper.page_parser.parse_thread_page.return_value = {
            "post_time": "2020-01-01 08:00", "magnet": "magnet:?xt=urn:btih:test"}
        scraper.data_manager = mock.Mock()
        scraper.data_manager.filter_and_save_data.side_effect = lambda rows, fid, **kwargs: rows
        return scraper

    def test_old_valid_post_retries_saves_and_clears(self):
        from scrapers.core.contracts import CrawlTarget
        scraper = self.scraper()
        info = {"tid": "42", "fid": 103, "number": "TEST-42", "title": "test", "date": "2020-01-01"}
        scraper.failure_store.due_targets.return_value = [CrawlTarget("42", "url", "103", info)]
        result = scraper.retry_failed_details()
        self.assertEqual({"requested": 1, "failed": 0, "saved": 1}, result)
        scraper.failure_store.clear.assert_called_once_with("sehuatang", ["42"])
        scraper.failure_store.record.assert_not_called()

    def test_date_exclusion_is_not_a_validation_failure(self):
        scraper = self.scraper()
        rows, failed = scraper._get_thread_details_batch_result([
            {"tid": "42", "fid": 103, "number": "TEST-42", "title": "test", "date": "2020-01-01"}])
        self.assertEqual(([], 0), (rows, failed))
        scraper.failure_store.record.assert_not_called()

    def test_missing_post_time_still_records_validation_reason(self):
        scraper = self.scraper()
        scraper.page_parser.parse_thread_page.return_value = {"magnet": "test"}
        _, failed = scraper._get_thread_details_batch_result([
            {"tid": "42", "fid": 103, "number": "TEST-42", "title": "test", "date": "2020-01-01"}])
        self.assertEqual(1, failed)
        self.assertIn("post_time", scraper.failure_store.record.call_args.args[0][0].error_message)


class VerificationTests(unittest.TestCase):
    gate = b"<script> var   safeid = 'test-safe'; </script>"
    cf = b"<title>Just a moment...</title>"
    body = b"<div id='postmessage_42'>ok</div>"

    def client(self):
        return HttpClient(settings=HttpSettings(), transport=mock.Mock())

    def test_r18_then_cf_then_content(self):
        client = self.client()
        client._request = mock.Mock(side_effect=[(200, self.gate), (403, self.cf)])
        client._flaresolverr = mock.Mock()
        client._flaresolverr.solve.return_value = (self.body, [], "test-agent")
        self.assertEqual(self.body, client.get_html("https://example.test/42"))
        self.assertEqual("test-safe", client._cookie_copy()["_safe"])

    def test_cf_r18_loop_is_bounded_and_never_returns_gate(self):
        client = self.client()
        client._request = mock.Mock(return_value=(403, self.cf))
        client._flaresolverr = mock.Mock()
        client._flaresolverr.solve.return_value = (self.gate, [], "test-agent")
        self.assertIsNone(client.get_html("https://example.test/42"))
        self.assertEqual(2, client._flaresolverr.solve.call_count)
        self.assertEqual(2, client._request.call_count)

    def test_concurrent_r18_requests_share_one_validation(self):
        client = self.client()
        barrier = threading.Barrier(2)
        def request(url):
            if not client._cookie_copy().get("_safe"):
                barrier.wait(timeout=2)
                return 200, self.gate
            return 200, self.body
        client._request = request
        with mock.patch.object(client, "_update_safeid_from_body", wraps=client._update_safeid_from_body) as update:
            with ThreadPoolExecutor(max_workers=2) as pool:
                result = list(pool.map(client.get_html, ["https://example.test/a", "https://example.test/b"]))
            self.assertEqual([self.body, self.body], result)
            self.assertEqual(1, update.call_count)

    def test_x1080x_skips_redundant_request_and_measures_solver_time(self):
        transport = mock.Mock()
        transport.fetch.return_value = FetchResult("url", self.cf, 403, 1, 5)
        client = X1080XHttpClient(HttpSettings(), transport=transport)
        client._flaresolverr = mock.Mock()
        client._flaresolverr.solve.return_value = (self.body, [], "test-agent")
        with mock.patch("scrapers.sources.x1080x.http_client.time.monotonic", side_effect=[1, 1, 2, 2]):
            result = client.fetch("https://example.test/42")
        self.assertEqual(1, transport.fetch.call_count)
        self.assertEqual(2, result.attempts)
        self.assertEqual(1000, result.elapsed_ms)

    def test_waiting_x1080x_threads_reuse_clearance(self):
        barrier = threading.Barrier(2)
        client = X1080XHttpClient(HttpSettings(), transport=mock.Mock())
        def fetch(url, stage="detail"):
            if stage == "cf_retry":
                return FetchResult(url, self.body, 200, 1, 1)
            barrier.wait(timeout=2)
            return FetchResult(url, self.cf, 403, 1, 1)
        client._transport.fetch.side_effect = fetch
        client._flaresolverr = mock.Mock()
        client._flaresolverr.solve.return_value = (self.body, [], "test-agent")
        with ThreadPoolExecutor(max_workers=2) as pool:
            rows = list(pool.map(client.fetch, ["https://example.test/a", "https://example.test/b"]))
        self.assertTrue(all(row.ok for row in rows))
        self.assertEqual(1, client._flaresolverr.solve.call_count)

    def test_shared_clients_expire_and_isolate_identity(self):
        settings = HttpSettings()
        with mock.patch("scrapers.sources.x1080x.http_client.time.monotonic", return_value=100):
            first = shared_http_client(settings, "solver", "https://cache.test")
            self.assertIs(first, shared_http_client(settings, "solver", "https://cache.test"))
            self.assertIsNot(first, shared_http_client(settings, "solver", "https://other.test"))
            self.assertIsNot(first, shared_http_client(replace(settings, impersonate="firefox147"), "solver", "https://cache.test"))
        with mock.patch("scrapers.sources.x1080x.http_client.time.monotonic", return_value=3701):
            self.assertIsNot(first, shared_http_client(settings, "solver", "https://cache.test"))


class ActivityTests(unittest.TestCase):
    def test_backfill_excludes_crawl_and_always_cleans_up(self):
        registry = SourceRegistry()
        registry.register(SourceDefinition("sample", mock.AsyncMock(), True))
        with self.assertRaisesRegex(ValueError, "stop"):
            with registry.activity("sample", "backfill", "pages 1-10") as acquired:
                self.assertTrue(acquired)
                self.assertEqual("backfill", registry.active_tasks()[0]["kind"])
                result = asyncio.run(registry.run("sample", {}, force=True))
                self.assertEqual("already_running", result["status"])
                raise ValueError("stop")
        self.assertEqual([], registry.active_tasks())
        self.assertEqual((), registry.running_sources())


if __name__ == "__main__":
    unittest.main()
