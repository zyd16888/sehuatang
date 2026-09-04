import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from scrapers.core.config import load_source_settings
from scrapers.core.contracts import (
    CrawlFailure,
    CrawlRecord,
    CrawlTarget,
    DiscoveryResult,
    SaveResult,
)
from scrapers.core.engine import CrawlEngine
from scrapers.core.http import CrawlerHttpClient, redact_url
from scrapers.core.models import FetchResult, RunStatus, RunSummary
from scrapers.infrastructure.json_failures import JsonFailureStore
from scrapers.sources.javbee.repository import JavbeeRepository
from util.read_config import ConfigManager


class SourceConfigTests(unittest.TestCase):
    def test_legacy_sources_keep_independent_http_settings(self):
        config = {
            "http_client": {
                "concurrent_workers": 6,
                "request_timeout": 15,
            },
            "proxy": {
                "proxy_enable": True,
                "proxy_url": "http://sehuatang-proxy:7890",
            },
            "javbee": {
                "concurrent_workers": 3,
                "request_timeout": 60,
                "retry_attempts": 4,
                "proxy_enable": False,
            },
        }

        sehuatang = load_source_settings(config, "sehuatang")
        javbee = load_source_settings(config, "javbee")

        self.assertEqual(15, sehuatang.timeout)
        self.assertEqual(6, sehuatang.concurrency)
        self.assertTrue(sehuatang.proxy.enabled)
        self.assertEqual(60, javbee.timeout)
        self.assertEqual(3, javbee.concurrency)
        self.assertEqual(4, javbee.retry.attempts)
        self.assertFalse(javbee.proxy.enabled)

    def test_new_source_override_wins_over_defaults_and_legacy(self):
        config = {
            "javbee": {"request_timeout": 20},
            "crawler": {
                "defaults": {
                    "concurrency": 4,
                    "http": {"timeout": 30, "retry": {"attempts": 3}},
                },
                "sources": {
                    "javbee": {
                        "concurrency": 2,
                        "http": {"timeout": 55},
                    }
                },
            },
        }

        settings = load_source_settings(config, "javbee")

        self.assertEqual(2, settings.concurrency)
        self.assertEqual(55, settings.timeout)
        self.assertEqual(3, settings.retry.attempts)

    def test_invalid_enabled_proxy_is_rejected(self):
        config = {
            "crawler": {
                "sources": {
                    "example": {
                        "http": {
                            "proxy": {"enabled": True, "url": "not-a-url"}
                        }
                    }
                }
            }
        }

        with self.assertRaises(ValueError):
            load_source_settings(config, "example")

    def test_source_environment_overrides_are_explicit_and_isolated(self):
        config = {
            "crawler": {
                "sources": {
                    "javbee": {"http": {"timeout": 30}},
                    "sehuatang": {"http": {"timeout": 20}},
                }
            }
        }
        environment = {
            "CRAWLER_JAVBEE_TIMEOUT": "60",
            "CRAWLER_JAVBEE_PROXY_ENABLED": "true",
            "CRAWLER_JAVBEE_PROXY_URL": "socks5h://proxy.example:1080",
        }

        javbee = load_source_settings(config, "javbee", environment)
        sehuatang = load_source_settings(config, "sehuatang", environment)

        self.assertEqual(60, javbee.timeout)
        self.assertTrue(javbee.proxy.enabled)
        self.assertEqual(20, sehuatang.timeout)
        self.assertFalse(sehuatang.proxy.enabled)

    def test_legacy_alias_is_explicit_and_does_not_scan_other_sources(self):
        manager = ConfigManager.__new__(ConfigManager)
        manager._config_path = "unused"
        manager._config_cache = {
            "sehuatang": {"domain_name": "example.test"},
            "javbee": {"request_timeout": 60},
        }

        self.assertEqual("example.test", manager.get_config("domain_name"))
        self.assertEqual("missing", manager.get_config("request_timeout", "missing"))


class RunSummaryTests(unittest.TestCase):
    def test_status_distinguishes_success_partial_and_failure(self):
        success = RunSummary(source="a", run_id="1", succeeded=2)
        partial = RunSummary(source="a", run_id="2", succeeded=1, failed=1)
        failed = RunSummary(source="a", run_id="3", failed=1)

        self.assertEqual(RunStatus.SUCCESS, success.status)
        self.assertEqual(RunStatus.PARTIAL_SUCCESS, partial.status)
        self.assertEqual(RunStatus.FAILED, failed.status)
        self.assertEqual("partial_success", partial.as_dict()["status"])


class HttpClientTests(unittest.TestCase):
    def _settings(self, **retry_overrides):
        config = {
            "crawler": {
                "sources": {
                    "test": {
                        "concurrency": 2,
                        "http": {
                            "timeout": 12,
                            "retry": {
                                "attempts": 3,
                                "base_delay": 2,
                                "max_delay": 10,
                                "jitter": 0,
                                **retry_overrides,
                            },
                        },
                    }
                }
            }
        }
        return load_source_settings(config, "test")

    @staticmethod
    def _response(status, body=b"", headers=None):
        return SimpleNamespace(
            status_code=status,
            content=body,
            headers=headers or {},
        )

    def test_retries_transient_status_then_returns_success(self):
        responses = iter(
            [
                self._response(503, headers={"Retry-After": "3"}),
                self._response(200, b"ok"),
            ]
        )
        delays = []
        client = CrawlerHttpClient(
            "test",
            self._settings(),
            request_func=lambda *args, **kwargs: next(responses),
            sleeper=delays.append,
        )

        result = client.fetch("https://example.test/detail/1")

        self.assertTrue(result.ok)
        self.assertEqual(2, result.attempts)
        self.assertEqual([3.0], delays)

    def test_does_not_retry_non_transient_status(self):
        calls = []

        def request(*args, **kwargs):
            calls.append(args[0])
            return self._response(404, b"missing")

        client = CrawlerHttpClient(
            "test",
            self._settings(),
            request_func=request,
            sleeper=lambda _: self.fail("404 不应进入重试退避"),
        )

        result = client.fetch("https://example.test/missing")

        self.assertFalse(result.ok)
        self.assertEqual(1, result.attempts)
        self.assertEqual(1, len(calls))

    def test_batch_results_keep_input_order(self):
        def request(url, **kwargs):
            return self._response(200, url.encode("utf-8"))

        client = CrawlerHttpClient(
            "test",
            self._settings(),
            request_func=request,
        )
        urls = ["https://example.test/2", "https://example.test/1"]

        results = client.fetch_many(urls)

        self.assertEqual(urls, [result.url for result in results])

    def test_redacts_credentials_and_sensitive_query_values(self):
        redacted = redact_url(
            "https://user:pass@example.test/path?token=secret&page=2"
        )

        self.assertEqual(
            "https://example.test/path?token=%2A%2A%2A&page=2",
            redacted,
        )


class CrawlEngineTests(unittest.TestCase):
    class FakeSource:
        name = "fake"

        def discover(self, context, http):
            return DiscoveryResult(
                targets=[
                    CrawlTarget("good", "https://example.test/good"),
                    CrawlTarget("bad", "https://example.test/bad"),
                ],
                details={"pages": 1},
            )

        def parse_detail(self, target, result):
            return CrawlRecord(target=target, payload={"key": target.key})

    class FakeRepository:
        def __init__(self):
            self.saved_records = []

        def select_targets(self, targets):
            return list(targets)

        def save_many(self, records):
            self.saved_records = list(records)
            return SaveResult(processed=len(records), saved=len(records))

    class FakeFailureStore:
        def __init__(self):
            self.failures = []
            self.cleared = []

        def record(self, failures):
            self.failures.extend(failures)

        def clear(self, source, keys):
            self.cleared.extend((source, key) for key in keys)

        def due_targets(self, source):
            return [CrawlTarget("retry", "https://example.test/retry")]

    class FakeHttp:
        def fetch_many(self, urls, stage="detail"):
            return [
                FetchResult(url=urls[0], body=b"ok", status_code=200, attempts=1, elapsed_ms=1),
                FetchResult(
                    url=urls[1],
                    body=None,
                    status_code=None,
                    attempts=3,
                    elapsed_ms=20,
                    error_type="timeout",
                    error_message="slow",
                ),
            ]

    def test_partial_run_saves_success_and_records_terminal_failure(self):
        repository = self.FakeRepository()
        failure_store = self.FakeFailureStore()
        engine = CrawlEngine(self.FakeHttp(), failure_store)

        summary = engine.run(
            self.FakeSource(),
            repository,
            run_id="run-1",
        )

        self.assertEqual(RunStatus.PARTIAL_SUCCESS, summary.status)
        self.assertEqual(1, summary.succeeded)
        self.assertEqual(1, summary.failed)
        self.assertEqual(2, summary.retries)
        self.assertEqual(["good"], [record.target.key for record in repository.saved_records])
        self.assertEqual("bad", failure_store.failures[0].key)
        self.assertEqual([("fake", "good")], failure_store.cleared)

    def test_dry_run_does_not_write_or_clear_failures(self):
        repository = self.FakeRepository()
        failure_store = self.FakeFailureStore()
        engine = CrawlEngine(self.FakeHttp(), failure_store)

        engine.run(
            self.FakeSource(),
            repository,
            dry_run=True,
            run_id="run-2",
        )

        self.assertEqual([], repository.saved_records)
        self.assertEqual([], failure_store.cleared)
        self.assertEqual([], failure_store.failures)

    def test_retry_failed_uses_failure_store_instead_of_discovery(self):
        class RetryHttp:
            def fetch_many(self, urls, stage="detail"):
                return [
                    FetchResult(
                        url=urls[0],
                        body=b"ok",
                        status_code=200,
                        attempts=1,
                        elapsed_ms=1,
                    )
                ]

        class NoDiscoverySource(self.FakeSource):
            def discover(self, context, http):
                raise AssertionError("retry-failed 不应重新执行列表发现")

        repository = self.FakeRepository()
        repository.select_targets = lambda targets: self.fail(
            "retry-failed 不应再经过常规刷新筛选"
        )
        failure_store = self.FakeFailureStore()
        summary = CrawlEngine(RetryHttp(), failure_store).run(
            NoDiscoverySource(),
            repository,
            retry_failed=True,
            run_id="run-3",
        )

        self.assertEqual(1, summary.requested)
        self.assertEqual("retry", repository.saved_records[0].target.key)


class JsonFailureStoreTests(unittest.TestCase):
    def test_failure_round_trip_and_clear(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "failures.json"
            store = JsonFailureStore(path)
            store.record(
                [
                    CrawlFailure(
                        source="sehuatang",
                        key="123",
                        url="https://example.test/123",
                        stage="fetch",
                        attempts=1,
                        error_type="timeout",
                        metadata={"fid": 103, "title": "sample"},
                    )
                ]
            )

            rows = __import__("json").loads(path.read_text(encoding="utf-8"))
            rows[0]["next_retry_at"] = datetime.now(timezone.utc).isoformat()
            path.write_text(
                __import__("json").dumps(rows, ensure_ascii=False),
                encoding="utf-8",
            )
            targets = store.due_targets("sehuatang")

            self.assertEqual("123", targets[0].key)
            self.assertEqual(103, targets[0].metadata["fid"])
            store.clear("sehuatang", ["123"])
            self.assertEqual([], store.due_targets("sehuatang"))


class JavbeeRefreshPolicyTests(unittest.TestCase):
    targets = [
        CrawlTarget("new", "https://example.test/new"),
        CrawlTarget("old", "https://example.test/old"),
        CrawlTarget("fresh", "https://example.test/fresh"),
    ]

    def _repository(self, mode, stale_lookup=None):
        return JavbeeRepository(
            {"refresh": {"mode": mode, "days": 7}},
            existing_lookup=lambda urls: {
                "https://example.test/old",
                "https://example.test/fresh",
            },
            stale_lookup=stale_lookup,
            save_func=lambda rows: {},
        )

    def test_new_only_skips_all_existing_targets(self):
        selected = self._repository("new_only").select_targets(self.targets)

        self.assertEqual(["new"], [target.key for target in selected])

    def test_stale_after_keeps_new_and_expired_targets(self):
        selected = self._repository(
            "stale_after",
            stale_lookup=lambda urls, cutoff: {"https://example.test/old"},
        ).select_targets(self.targets)

        self.assertEqual(["new", "old"], [target.key for target in selected])


if __name__ == "__main__":
    unittest.main()
