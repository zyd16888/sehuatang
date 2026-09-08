import threading
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from scrapers.core.cf_challenge import FlareSolverrClient, SiteRateLimited
from scrapers.core.config import HttpSettings, RetrySettings
from scrapers.core.contracts import CrawlTarget, DetailValidationError
from scrapers.core.models import FetchResult
from scrapers.sources.x1080x.http_client import X1080XHttpClient
from scrapers.sources.x1080x.rate_limit import (
    BackfillHttpClient, CrawlStopped, RateLimitSettings, RequestGate,
)
from scrapers.sources.x1080x.source import X1080XSource
from tests.x1080x_tests import DETAIL_HTML, DETAIL_HTML_NO_MAGNET


RATE_HTML = '<html><title>访问受限</title><body>请求过于频繁</body></html>'.encode()


class FakeClock:
    def __init__(self):
        self.time = 0.0
        self.waits = []
        self.lock = threading.Lock()

    def now(self):
        with self.lock:
            return self.time

    def wait(self, seconds):
        with self.lock:
            self.waits.append(seconds)
            self.time += seconds


def scripted_client(pages):
    clock = FakeClock()
    client = X1080XHttpClient(HttpSettings(concurrency=1))
    client.gate = RequestGate(RateLimitSettings(0, 60, 120),
                              monotonic=clock.now, waiter=clock.wait)
    calls = []

    class Transport:
        def fetch(self, url, stage="detail"):
            client.gate.acquire()
            calls.append((url, stage, clock.now()))
            values = pages[url]
            value = values.pop(0) if len(values) > 1 else values[0]
            status, body = value if isinstance(value, tuple) else (200, value)
            return FetchResult(url, body, status, 1, 0,
                               error_type=None if status == 200 else "http_status")

    client._transport = Transport()
    return client, clock, calls


class RateLimitTests(unittest.TestCase):
    def test_shared_gate_spaces_concurrent_requests(self):
        clock = FakeClock()
        gate = RequestGate(RateLimitSettings(2, 60, 120),
                           monotonic=clock.now, waiter=clock.wait)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: gate.acquire(), range(8)))
        self.assertGreaterEqual(clock.now(), 14)

    def test_cooldown_doubles_caps_and_success_resets(self):
        client, clock, calls = scripted_client({"u": [RATE_HTML] * 4 + [DETAIL_HTML]})
        result = BackfillHttpClient(client).fetch("u")
        self.assertTrue(result.ok)
        self.assertEqual([0, 60, 180, 300, 420], [t for _, _, t in calls])
        self.assertEqual(5, result.attempts)
        client.gate.limit()
        client.wait_for_retry()
        self.assertEqual(480, clock.now())

    def test_incremental_does_not_send_during_shared_cooldown(self):
        client, clock, calls = scripted_client({"u": [RATE_HTML], "v": [DETAIL_HTML]})
        first = client.fetch("u")
        blocked = client.fetch("v")
        self.assertEqual("rate_limited", first.error_type)
        self.assertFalse(first.ok)
        self.assertEqual(0, blocked.attempts)
        self.assertEqual(1, len(calls))
        self.assertEqual([], clock.waits)
        client.gate.success()
        self.assertEqual("rate_limited", client.fetch("v").error_type)

    def test_plain_429_does_not_invoke_solver(self):
        client, _, _ = scripted_client({"u": [(429, b"Too Many Requests")]})
        with patch.object(client, "_bypass") as bypass:
            self.assertEqual("rate_limited", client.fetch("u").error_type)
        bypass.assert_not_called()

    def test_solver_limit_is_not_invalid_document(self):
        client, _, _ = scripted_client({"u": [(403, b"<title>Just a moment</title>")]})
        client._flaresolverr = SimpleNamespace(solve=lambda *a, **k: (RATE_HTML, [], "UA"))
        result = client.fetch("u")
        self.assertEqual("rate_limited", result.error_type)
        self.assertFalse(result.ok)

    def test_flaresolverr_429_and_200_limit_responses(self):
        for status in (200, 429):
            response = SimpleNamespace(json=lambda: {"solution": {
                "status": status, "response": RATE_HTML.decode(),
            }})
            with patch("scrapers.core.cf_challenge.requests.post", return_value=response):
                with self.assertRaises(SiteRateLimited):
                    FlareSolverrClient("http://unused/v1", raise_on_rate_limit=True).solve("u")

    def test_recovery_keeps_successful_details_and_result_order(self):
        client, _, calls = scripted_client({"a": [DETAIL_HTML], "b": [RATE_HTML, DETAIL_HTML]})
        results = BackfillHttpClient(client).fetch_many(["a", "b"])
        self.assertTrue(all(result.ok for result in results))
        self.assertEqual(["a", "b"], [result.url for result in results])
        self.assertEqual(["a", "b", "b"], [url for url, _, _ in calls])

    def test_stop_interrupts_cooldown(self):
        client, _, _ = scripted_client({"u": [RATE_HTML]})
        client.gate._wait = lambda _: client.gate.stop_event.set()
        with self.assertRaises(CrawlStopped):
            BackfillHttpClient(client).fetch("u")

    def test_transport_retries_also_use_request_interval(self):
        clock = FakeClock()
        client = X1080XHttpClient(HttpSettings(retry=RetrySettings(
            attempts=2, base_delay=0, max_delay=0, statuses=(500,))))
        client.gate = RequestGate(RateLimitSettings(2, 60, 120),
                                  monotonic=clock.now, waiter=clock.wait)
        times = []
        cookies = SimpleNamespace(clear=lambda: None, get_dict=lambda: {})

        def get(*args, **kwargs):
            times.append(clock.now())
            return SimpleNamespace(status_code=500 if len(times) == 1 else 200,
                                   content=DETAIL_HTML, headers={}, cookies=cookies)

        client._local.session = SimpleNamespace(cookies=cookies, get=get)
        self.assertTrue(client.fetch("u").ok)
        self.assertEqual([0, 2], times)

    def test_retry_after_sets_shared_cooldown(self):
        clock = FakeClock()
        client = X1080XHttpClient(HttpSettings())
        client.gate = RequestGate(RateLimitSettings(0, 60, 120),
                                  monotonic=clock.now, waiter=clock.wait)
        cookies = SimpleNamespace(clear=lambda: None, get_dict=lambda: {})
        response = SimpleNamespace(status_code=429, content=b"Too Many Requests",
                                   headers={"Retry-After": "180"}, cookies=cookies)
        client._local.session = SimpleNamespace(cookies=cookies, get=lambda *a, **k: response)
        self.assertEqual("rate_limited", client.fetch("u").error_type)
        client.wait_for_retry()
        self.assertEqual(180, clock.now())

    def test_invalid_config_is_rejected(self):
        for raw in ({"cooldown_seconds": 0}, {"max_cooldown_seconds": 10},
                    {"min_interval_seconds": -1}, {"max_cooldown_seconds": float("inf")}):
            with self.assertRaises(ValueError):
                RateLimitSettings.from_config({"rate_limit": raw})

    def test_engine_stops_following_batches_without_recording_site_failures(self):
        from scrapers.core.engine import CrawlEngine
        from scrapers.page_backfill import FixedTargetSource
        from tests.crawler_core_tests import CrawlEngineTests
        client, _, calls = scripted_client({"a": [RATE_HTML], "b": [DETAIL_HTML]})
        source = FixedTargetSource(X1080XSource({}, diagnostics=False), [
            CrawlTarget("1", "a"), CrawlTarget("2", "b"),
        ])
        failures = CrawlEngineTests.FakeFailureStore()
        result = CrawlEngine(client, failures).run(
            source, CrawlEngineTests.FakeRepository(), batch_size=1,
        )
        self.assertTrue(result.details["rate_limited"])
        self.assertEqual([], failures.failures)
        self.assertEqual(1, len(calls))

    def test_incremental_detects_limit_on_empty_list_retry(self):
        from scrapers.core.contracts import CrawlContext
        source = X1080XSource({"typeids": {"5479": "中文字幕"}}, diagnostics=False)
        client, _, calls = scripted_client({source.list_url("5479", 1): [
            b'<html><div id="content"></div></html>', RATE_HTML,
        ]})
        with self.assertRaisesRegex(RuntimeError, "限流"):
            source.discover(CrawlContext("x1080x", "test"), client)
        self.assertEqual(2, len(calls))


class DetailDiagnosticsTests(unittest.TestCase):
    def test_engine_keeps_specific_failure_reason(self):
        from scrapers.core.engine import CrawlEngine
        from scrapers.page_backfill import FixedTargetSource
        from tests.crawler_core_tests import CrawlEngineTests
        client, _, _ = scripted_client({"a": [DETAIL_HTML_NO_MAGNET]})
        source = FixedTargetSource(X1080XSource({}, diagnostics=False), [CrawlTarget("1", "a")])
        failures = CrawlEngineTests.FakeFailureStore()
        CrawlEngine(client, failures).run(source, CrawlEngineTests.FakeRepository())
        self.assertEqual("missing_magnet", failures.failures[0].error_type)

    def test_snapshots_have_count_and_size_limits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            module_path = root / "scrapers" / "sources" / "x1080x" / "source.py"
            with patch("scrapers.sources.x1080x.source.__file__", str(module_path)):
                source = X1080XSource({})
                for tid in range(22):
                    source._diagnose_detail(tid, FetchResult("u", b"x" * 600000, 200, 1, 0),
                                            "missing_title")
            snapshots = list((root / "data" / "debug").glob("x1080x_detail_*.html"))
            self.assertEqual(20, len(snapshots))
            self.assertTrue(all(path.stat().st_size <= 512 * 1024 for path in snapshots))

    def test_specific_reasons_are_reported_without_dump_in_dry_run(self):
        source = X1080XSource({}, diagnostics=False)
        target = CrawlTarget("1", "https://example.invalid/1")
        cases = {
            "missing_title": b"<html></html>",
            "missing_date": b'<span id="thread_subject">title</span>',
            "missing_content": ('<span id="thread_subject">title</span>'
                                '<em id="authorposton1">发表于 2026-09-08</em>').encode(),
            "missing_magnet": DETAIL_HTML_NO_MAGNET,
            "page_unavailable": b"<title>Database Error</title>",
        }
        with patch("pathlib.Path.write_bytes") as write:
            for reason, body in cases.items():
                with self.assertRaises(DetailValidationError) as caught:
                    source.parse_detail(target, FetchResult(target.url, body, 200, 1, 0))
                self.assertEqual(reason, caught.exception.reason)
            write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
