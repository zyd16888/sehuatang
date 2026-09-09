"""公共会话池与持久化并发合同；只使用假 HTTP 和临时本地文件。"""
import asyncio
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from curl_cffi import requests
from scrapers.core.cf_challenge import FlareSolverrClient, is_cf_challenge
from scrapers.core.config import HttpSettings, ProxySettings, RetrySettings, load_source_settings
from scrapers.core.contracts import CrawlTarget, CrawlRecord, DiscoveryResult, SaveResult
from scrapers.core.engine import CrawlEngine
from scrapers.core.models import FetchResult
from scrapers.core.pool import SessionPool, shared_pool, stop_shared_clients
from scrapers.core.rate_limit import BackfillHttpClient, CrawlStopped, RequestGate, RateLimitSettings
from scrapers.infrastructure.file_lock import FileLock
from scrapers.page_backfill import PageCheckpointStore, MongoPageCheckpointStore
from scrapers.registry import SourceRegistry, SourceDefinition


def settings(**kwargs):
    return replace(HttpSettings(concurrency=2, proxy=ProxySettings(True, urls=(
        "http://proxy.test:17891", "http://proxy.test:17892")),
        retry=RetrySettings(attempts=1), cooldown_seconds=0.04,
        max_cooldown_seconds=0.08), **kwargs)


def response(body=b"ok", status=200, cookies=None, headers=None):
    return SimpleNamespace(content=body, status_code=status,
                           cookies=cookies or requests.Cookies(), headers=headers or {})


class PoolTests(unittest.TestCase):
    def tearDown(self):
        stop_shared_clients()

    def fake_sessions(self, getter):
        sessions = []
        def create():
            session = SimpleNamespace(cookies=requests.Cookies(), closed=False)
            session.get = lambda url, **kw: getter(session, url, **kw)
            def close():
                session.closed = True
                session.closed_thread = threading.get_ident()
            session.close = close
            session.thread = threading.get_ident()
            sessions.append(session)
            return session
        patcher = patch("scrapers.core.session.requests.Session", side_effect=create)
        patcher.start()
        self.addCleanup(patcher.stop)
        return sessions

    def pool(self, source="test", **kwargs):
        pool = SessionPool(source, settings(**kwargs))
        self.addCleanup(pool.close)
        return pool

    def test_ports_keep_cookie_ua_and_sessions_across_batches(self):
        barrier = threading.Barrier(2)
        seen = []
        def get(session, url, **kw):
            port = kw["proxies"]["http"]
            seen.append((port, dict(kw["cookies"]), kw["headers"]["User-Agent"]))
            barrier.wait(2)
            cookies = requests.Cookies()
            cookies.set("visitor", port, domain="site.test", path="/")
            return response(cookies=cookies)
        sessions = self.fake_sessions(get)
        pool = self.pool()
        pool.fetch_many(["https://site.test/a", "https://site.test/b"])
        pool.lanes[0]._user_agent = "UA-A"
        pool.lanes[1]._user_agent = "UA-B"
        pool.fetch_many(["https://site.test/c", "https://site.test/d"])
        self.assertEqual(2, len(sessions))
        for port, cookies, ua in seen[2:]:
            self.assertEqual(port, cookies["visitor"])
            self.assertEqual("UA-A" if port.endswith("17891") else "UA-B", ua)
        pool.close()
        self.assertTrue(all(s.closed and s.thread == s.closed_thread for s in sessions))

    def test_duplicate_urls_share_one_request_and_keep_result_positions(self):
        calls = []
        self.fake_sessions(lambda session, url, **kw: (calls.append(url), response(url.encode()))[1])
        pool = self.pool()
        urls = ["https://site.test/a", "https://site.test/a", "https://site.test/b"]
        rows = pool.fetch_many(urls)
        self.assertEqual(urls, [r.url for r in rows])
        self.assertCountEqual(set(urls), calls)

    def test_separate_callers_share_inflight_request(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def get(session, url, **kw):
            calls.append(url)
            entered.set()
            self.assertTrue(release.wait(2))
            return response()
        self.fake_sessions(get)
        pool = self.pool()
        one = pool._submit("https://site.test/a", "detail", False)
        self.assertTrue(entered.wait(1))
        two = pool._submit("https://site.test/a", "detail", False)
        self.assertIs(one, two)
        release.set()
        self.assertTrue(one.result(2).ok)
        self.assertEqual(1, len(calls))

    def test_backfill_rate_limit_keeps_port_and_emits_other_success_first(self):
        calls = []
        def get(session, url, **kw):
            calls.append((url, kw["proxies"]["http"]))
            count = sum(u == url for u, _ in calls)
            return response(b"Too Many Requests", 429) if url.endswith("slow") and count == 1 else response()
        self.fake_sessions(get)
        pool = self.pool()
        rows = list(pool.iter_completed(["https://site.test/slow", "https://site.test/fast"], recover=True))
        self.assertEqual(1, rows[0][0])
        slow = [port for url, port in calls if url.endswith("slow")]
        self.assertEqual(2, len(slow))
        self.assertEqual(slow[0], slow[1])
        self.assertTrue(all(row.ok for _, row in rows))

    def test_one_cooling_lane_does_not_block_incremental_healthy_lane(self):
        used = []
        self.fake_sessions(lambda session, url, **kw: (used.append(kw["proxies"]["http"]), response())[1])
        pool = self.pool(cooldown_seconds=60, max_cooldown_seconds=900)
        pool.lanes[0].gate.limit()
        self.assertTrue(pool.fetch("https://site.test/a").ok)
        self.assertEqual(["http://proxy.test:17892"], used)
        pool.lanes[1].gate.limit()
        self.assertEqual("rate_limited", pool.fetch("https://site.test/b").error_type)
        self.assertEqual(1, len(used))

    def test_stop_interrupts_original_target_cooldown(self):
        self.fake_sessions(lambda *a, **kw: response(b"Too Many Requests", 429))
        pool = self.pool(cooldown_seconds=60, max_cooldown_seconds=900)
        pending = pool._submit("https://site.test/a", "detail", True)
        pool.close()
        with self.assertRaises(CrawlStopped):
            pending.result(1)

    def test_site_concurrency_is_shared_by_all_ports(self):
        active = maximum = 0
        guard = threading.Lock()
        def get(*a, **kw):
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.01)
            with guard:
                active -= 1
            return response()
        self.fake_sessions(get)
        pool = self.pool(concurrency=1)
        self.assertTrue(all(row.ok for row in pool.fetch_many(["https://site.test/a", "https://site.test/b"])))
        self.assertEqual(1, maximum)

    def test_sources_and_domains_do_not_share_cookie_state(self):
        cfg = settings()
        a = shared_pool("a", cfg, "https://site.test")
        self.assertIs(a, shared_pool("a", cfg, "https://site.test"))
        self.assertIsNot(a, shared_pool("b", cfg, "https://site.test"))
        self.assertIsNot(a, shared_pool("a", cfg, "https://other.test"))

    def test_plain_403_is_not_a_challenge_and_503_uses_normal_retry(self):
        calls = []
        def get(*a, **kw):
            calls.append(1)
            return response(status=503 if len(calls) == 1 else 200)
        self.fake_sessions(get)
        pool = self.pool(retry=RetrySettings(attempts=2, base_delay=0, max_delay=0))
        self.assertTrue(pool.fetch("https://site.test/a").ok)
        self.assertEqual(2, len(calls))
        self.assertFalse(is_cf_challenge(b"Forbidden", 403))


class SolverAndConfigTests(unittest.TestCase):
    def test_scraper_factories_honor_common_defaults(self):
        from scrapers.javbee_scraper import JavbeeScraper
        from scrapers.x1080x_scraper import X1080XScraper
        defaults = {"concurrency": 2, "rate_limit": {"min_interval_seconds": 7},
                    "challenge": {"flaresolverr_url": "http://solver/v1", "provider": "byparr"},
                    "http": {"proxy": {"enabled": True, "urls": ["http://proxy:1001", "http://proxy:1002"]}}}
        def config(key=None, default=None):
            return defaults if key == "crawler.defaults" else default
        with patch("scrapers.javbee_scraper.get_config", side_effect=config), \
             patch("scrapers.x1080x_scraper.get_config", side_effect=config):
            try:
                for scraper in (JavbeeScraper(config={"base_url": "https://jav.test"}, failure_store=Mock()),
                                X1080XScraper(config={"base_url": "https://x.test"}, failure_store=Mock())):
                    self.assertEqual(7, scraper.http.settings.min_interval_seconds)
                    self.assertEqual("http://solver/v1", scraper.http.settings.solver_url)
                    self.assertEqual(2, len(scraper.http.lanes))
            finally:
                stop_shared_clients()

    def test_retry_after_accepts_http_date(self):
        from datetime import datetime, timedelta, timezone
        from email.utils import format_datetime
        from scrapers.core.http import CrawlerHttpClient
        value = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=180), usegmt=True)
        delay = CrawlerHttpClient._retry_after(response(headers={"Retry-After": value}))
        self.assertTrue(178 <= delay <= 180)

    def test_provider_specific_proxy_transport(self):
        solved = SimpleNamespace(json=lambda: {"solution": {
            "status": 200, "response": "ok", "cookies": [], "userAgent": "ua"}})
        for provider in ("byparr", "flaresolverr"):
            with self.subTest(provider=provider), patch("scrapers.core.cf_challenge.requests.post", return_value=solved) as post:
                solver = FlareSolverrClient("http://solver.test/v1", provider=provider,
                                            proxy_url="http://proxy.test:17891")
                self.assertIsNotNone(solver.solve("https://site.test/a"))
                kw = post.call_args.kwargs
                if provider == "byparr":
                    self.assertEqual("http://proxy.test:17891", kw["headers"]["X-Proxy-Server"])
                    self.assertNotIn("proxy", kw["json"])
                else:
                    self.assertEqual({"url": "http://proxy.test:17891"}, kw["json"]["proxy"])

    def test_all_sources_support_same_configuration(self):
        config = {"crawler": {"defaults": {"http": {"proxy": {"enabled": True,
            "urls": ["http://proxy.test:17891", "http://proxy.test:17892"]}},
            "rate_limit": {"min_interval_seconds": 3},
            "challenge": {"provider": "byparr", "flaresolverr_url": "http://solver/v1"}}}}
        for source in ("sehuatang", "javbee", "x1080x"):
            cfg = load_source_settings(config, source, {})
            self.assertEqual(2, len(cfg.proxy.addresses))
            self.assertEqual(3, cfg.min_interval_seconds)
            self.assertEqual("http://solver/v1", cfg.solver_url)

    def test_invalid_proxy_lists_rejected_and_env_single_url_overrides_list(self):
        for urls in ("http://proxy:1234", ["http://proxy:1234"] * 2, [42]):
            with self.assertRaises(ValueError):
                load_source_settings({"crawler": {"defaults": {"http": {
                    "proxy": {"enabled": True, "urls": urls}}}}}, "test", {})
        cfg = load_source_settings({"crawler": {"defaults": {"http": {"proxy": {
            "enabled": True, "urls": ["http://proxy:1234"]}}}}}, "test",
            {"CRAWLER_TEST_PROXY_URL": "http://other:4321"})
        self.assertEqual(("http://other:4321",), cfg.proxy.addresses)


class PersistenceTests(unittest.TestCase):
    def test_progress_snapshot_uses_active_checkpoint_backend(self):
        from scrapers.page_backfill import checkpoint_snapshot
        from util import mongo
        collection = Mock()
        collection.find.return_value = [{"_id": "range:test:partition", "page": 9}]
        with patch("util.read_config.get_config", return_value=True), \
             patch.object(mongo, "db", {"crawl_checkpoints": collection}):
            snapshot = checkpoint_snapshot()
        self.assertEqual("MongoDB:crawl_checkpoints", snapshot["path"])
        self.assertEqual({"range:test:partition": 9}, snapshot["progress"])
        with patch("util.read_config.get_config", return_value=False), \
             patch.object(PageCheckpointStore, "snapshot", return_value={"progress": {}}) as read:
            self.assertEqual({"progress": {}}, checkpoint_snapshot())
        read.assert_called_once()

    def test_partial_upsert_distinguishes_matched_inserted_and_failed(self):
        from pymongo.errors import BulkWriteError
        from scrapers.data_manager import DataManager
        manager = DataManager()
        manager.mongodb_enable = True
        rows = [{"tid": str(i)} for i in range(3)]
        error = BulkWriteError({"nUpserted": 1, "nMatched": 1,
                                "upserted": [{"index": 1, "_id": "new"}],
                                "writeErrors": [{"index": 2}], "writeConcernErrors": []})
        progress = {}
        with patch("scrapers.data_manager.filter_data", return_value=rows), \
             patch("scrapers.data_manager.save_data", side_effect=error):
            with self.assertRaises(BulkWriteError):
                manager.filter_and_save_data(rows, 103, strict=True, stats=progress)
        self.assertEqual(1, progress["saved"])
        self.assertEqual(1, progress["existing"])
        self.assertEqual(rows[:2], progress["completed_records"])

    def test_sehuatang_upsert_only_inserts_and_reports_actual_new_records(self):
        from util import mongo
        collection = Mock()
        collection.bulk_write.return_value = SimpleNamespace(upserted_ids={1: "new"})
        old = {"tid": "10", "title": "old", "magnet": "magnet:a"}
        new = {"tid": "20", "title": "new", "magnet": "magnet:b"}
        with patch.object(mongo, "db", {"hd_chinese_subtitles": collection}), \
             patch.object(mongo, "send_context") as notify:
            inserted = mongo.save_data([old, new], 103)
        self.assertEqual([new], inserted)
        operations = collection.bulk_write.call_args.args[0]
        self.assertEqual({"tid": "10"}, operations[0]._filter)
        self.assertEqual({"$setOnInsert"}, set(operations[0]._doc))
        self.assertEqual([new], notify.call_args.args[0])
        self.assertTrue(any(call.kwargs.get("unique") for call in collection.create_index.call_args_list))

    def test_engine_deduplicates_post_keys_before_fetch(self):
        targets = [CrawlTarget("same", "a"), CrawlTarget("same", "a?alias=1")]
        http = Mock()
        http.fetch_many.return_value = [FetchResult("a?alias=1", b"ok", 200, 1, 0)]
        source = SimpleNamespace(name="test", discover=lambda *a: DiscoveryResult(targets),
                                 parse_detail=lambda target, result: CrawlRecord(target, {}))
        repo = SimpleNamespace(select_targets=lambda rows: rows, save_many=lambda rows: SaveResult(saved=len(rows)))
        result = CrawlEngine(http).run(source, repo)
        self.assertEqual(1, result.requested)
        self.assertEqual(1, len(http.fetch_many.call_args.args[0]))

    def test_mongo_checkpoint_scope_and_write_errors(self):
        collection = Mock()
        store = MongoPageCheckpointStore(collection, "range-one")
        store.save("test", "partition", 5)
        self.assertEqual({"_id": "range-one:test:partition"}, collection.update_one.call_args.args[0])
        collection.update_one.side_effect = OSError("database unavailable")
        with self.assertRaises(OSError):
            store.save("test", "partition", 6)

    def test_source_lock_excludes_separate_registry_instances(self):
        one, two = SourceRegistry(), SourceRegistry()
        definition = SourceDefinition("offline-multi-proxy", Mock(), True)
        one.register(definition)
        two.register(definition)
        with one.activity(definition.name, "crawl", "test") as acquired:
            self.assertTrue(acquired)
            with two.activity(definition.name, "backfill", "test") as second:
                self.assertFalse(second)
        with two.activity(definition.name, "retry", "test") as second:
            self.assertTrue(second)

    def test_os_lock_excludes_subprocess_and_releases_after_close(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "task.lock"
            script = "from scrapers.infrastructure.file_lock import FileLock; import sys; lock=FileLock(sys.argv[1],blocking=False); print(lock.acquire()); lock.release()"
            with FileLock(path):
                result = subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True, text=True, timeout=10)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual("False", result.stdout.strip())
            result = subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True, text=True, timeout=10)
            self.assertEqual("True", result.stdout.strip())

    def test_json_checkpoint_read_modify_write_preserves_parallel_partitions(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pages.json"
            with ThreadPoolExecutor(max_workers=4) as workers:
                list(workers.map(lambda i: PageCheckpointStore(path).save("source", i, i + 1), range(12)))
            self.assertEqual(list(range(1, 13)), [PageCheckpointStore(path).load("source", i) for i in range(12)])
            scoped = PageCheckpointStore(path, scope="different-range")
            self.assertEqual(0, scoped.load("source", 1))

    def test_ledger_failure_propagates_before_page_can_commit(self):
        http = SimpleNamespace(fetch_many=lambda urls, stage: [FetchResult(url, None, 500, 1, 0, "http_status") for url in urls])
        source = SimpleNamespace(name="test", discover=lambda *a: DiscoveryResult([CrawlTarget("1", "url")]))
        repository = SimpleNamespace(select_targets=lambda targets: targets)
        ledger = Mock()
        ledger.record.side_effect = OSError("disk unavailable")
        with self.assertRaisesRegex(OSError, "disk unavailable"):
            CrawlEngine(http, ledger).run(source, repository)

    def test_completed_result_is_saved_before_waiting_for_next_result(self):
        saved = []
        class Http:
            def iter_completed(self, urls):
                yield 1, FetchResult(urls[1], b"ok", 200, 1, 0)
                assert saved == ["2"], "成功结果必须在继续等待前保存"
                yield 0, FetchResult(urls[0], b"ok", 200, 1, 0)
        targets = [CrawlTarget("1", "a"), CrawlTarget("2", "b")]
        source = SimpleNamespace(name="test", discover=lambda *a: DiscoveryResult(targets),
                                 parse_detail=lambda target, result: CrawlRecord(target, {}))
        repository = SimpleNamespace(select_targets=lambda rows: rows,
            save_many=lambda rows: (saved.extend(row.target.key for row in rows), SaveResult(saved=len(rows)))[1])
        result = CrawlEngine(Http()).run(source, repository)
        self.assertEqual(2, result.saved)


if __name__ == "__main__":
    unittest.main()
