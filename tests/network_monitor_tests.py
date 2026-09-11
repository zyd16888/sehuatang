"""真实公共会话链路 + 确定性时间：无外网、无数据库。"""
import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from curl_cffi import requests
from curl_cffi.requests.exceptions import RequestException
from fastapi.testclient import TestClient

from scrapers.core.config import HttpSettings, ProxySettings, RetrySettings
from scrapers.core.http import CrawlerHttpClient
from scrapers.core.network import NetworkMonitor, exception_info
from scrapers.core.pool import SessionPool, shared_pool, stop_shared_clients
from web.app import create_app


URL = "https://site.test/list?token=secret"


def response(status=200, body=b"page"):
    return SimpleNamespace(status_code=status, content=body, cookies=requests.Cookies(), headers={})


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.log = Mock()
        self.monitor = NetworkMonitor("x1080x", "http://user:password@proxy.test:17891", self.log,
                                      monotonic=lambda: self.now, wall=lambda: self.now)

    def record(self, *, error=None, status=200, body=b"page", retry=False, started=None, url=URL):
        self.monitor.attempt(url, started=self.now if started is None else started,
                             elapsed_ms=50, status=status, body=body, retry=retry, error=error)
        self.now += 1

    def failures(self):
        for _ in range(3):
            self.record(error=exception_info(TimeoutError()), status=None, body=None)

    def row(self):
        return self.monitor.snapshot()[0]

    def test_retry_success_is_not_final_failure_and_window_expires(self):
        self.record(error=exception_info(TimeoutError()), status=None, body=None)
        self.record(retry=True)
        self.monitor.completed(URL, True)
        row = self.row()
        self.assertEqual((2, 1, 0, .5), (row["attempts"], row["retries"], row["failed"], row["timeout_rate"]))
        self.now += 300
        row = self.row()
        self.assertEqual("stale", row["state"])
        self.assertEqual(0, row["attempts"])
        self.assertIsNone(row["timeout_rate"])

    def test_single_probe_idle_interval_and_recovery_events(self):
        self.failures()
        self.assertEqual("suspect", self.row()["state"])
        self.assertIsNone(self.monitor.claim_probe())
        self.now += 5
        claim = self.monitor.claim_probe()
        self.assertIsNotNone(claim)
        self.assertEqual("diagnosing", self.row()["state"])
        self.assertIsNone(self.monitor.claim_probe())
        self.monitor.finish_probe(*claim, error=exception_info(TimeoutError()))
        self.assertIsNone(self.monitor.claim_probe())
        self.now += 60
        claim = self.monitor.claim_probe()
        self.monitor.finish_probe(*claim, result=response())
        self.assertEqual("recovered", self.row()["state"])
        self.assertEqual(3, self.row()["attempts"])
        self.assertEqual(0, self.row()["completed"])
        self.assertEqual(1, self.log.warning.call_count)
        self.assertEqual(1, self.log.info.call_count)
        self.assertIsNone(self.monitor.claim_probe())

    def test_late_success_and_probe_do_not_clear_newer_failure(self):
        old_start = self.now - 10
        self.failures()
        self.record(started=old_start)
        self.assertEqual("suspect", self.row()["state"])
        self.now += 5
        claim = self.monitor.claim_probe()
        self.record(error=exception_info(TimeoutError()), status=None, body=None)
        self.monitor.finish_probe(*claim, result=response())
        self.assertEqual("suspect", self.row()["state"])
        self.assertIsNone(self.row()["diagnostic"])

    def test_access_restrictions_are_not_proxy_network_failures(self):
        for status, body, state in [(403, b"forbidden", "restricted"),
                                    (429, b"slow down", "restricted"),
                                    (200, b"cf-challenge", "challenge"),
                                    (407, b"auth", "proxy_auth"),
                                    (503, b"unavailable", "response_error")]:
            self.record(status=status, body=body)
            self.assertEqual(state, self.row()["state"])
            self.assertEqual(0, self.row()["consecutive_failures"])
            self.assertIsNone(self.monitor.claim_probe())

    def test_out_of_order_parallel_failures_still_trigger_threshold(self):
        for start in (1000, 999, 998):
            self.record(started=start, error=exception_info(TimeoutError()), status=None, body=None)
        self.assertEqual("suspect", self.row()["state"])
        self.assertEqual(3, self.row()["consecutive_failures"])
        self.record()
        self.record(started=999, error=exception_info(TimeoutError()), status=None, body=None)
        self.assertEqual("recovered", self.row()["state"])

    def test_probe_rate_limit_is_per_lane_across_domains(self):
        self.failures()
        for _ in range(3):
            self.record(url="https://other.test/", error=exception_info(TimeoutError()), status=None, body=None)
        self.now += 5
        claim = self.monitor.claim_probe()
        self.monitor.finish_probe(*claim, result=response())
        self.assertIsNone(self.monitor.claim_probe())
        self.now += 60
        self.assertEqual("https://other.test/", self.monitor.claim_probe()[0])

    def test_solved_challenge_final_result_updates_state_not_attempts(self):
        start = self.now
        self.record(status=403, body=b"cf-challenge")
        self.monitor.completed(URL, True, start)
        self.assertEqual("healthy", self.row()["state"])
        self.assertEqual(1, self.row()["attempts"])

    def test_r18_validation_failure_is_not_displayed_as_healthy(self):
        start = self.now
        self.record()
        self.monitor.completed(URL, False, start, "r18_challenge")
        self.assertEqual("challenge", self.row()["state"])
        self.assertEqual(1, self.row()["failed"])

    def test_domains_separate_and_memory_is_bounded_and_redacted(self):
        self.failures()
        self.record(url="https://other.test/")
        rows = self.monitor.snapshot()
        self.assertEqual(["suspect", "healthy"], [r["state"] for r in rows])
        for index in range(25):
            self.record(url=f"https://{index}.test/")
        rows = self.monitor.snapshot()
        self.assertEqual(16, len(rows))
        serialized = json.dumps(rows)
        self.assertNotIn("password", serialized)
        self.assertNotIn("secret", serialized)
        self.assertIn("proxy.test:17891", serialized)

    def test_error_codes_are_classified_without_raw_credentials(self):
        for code, kind, phase in [(5, "dns", "proxy_dns"), (6, "dns", "dns"),
                                  (7, "connection", "connect"), (28, "timeout", "unknown"),
                                  (35, "tls", "tls"), (97, "proxy", "proxy_handshake")]:
            info = exception_info(RequestException("http://user:password@proxy.test/?token=secret", code=code))
            self.assertEqual((kind, phase), (info["error_type"], info["phase"]))
            self.assertNotIn("password", json.dumps(info))

    def test_no_active_probes_after_observation_window_expires(self):
        self.failures()
        self.now += 301
        self.assertIsNone(self.monitor.claim_probe())
        self.assertEqual("stale", self.row()["state"])


class NetworkIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(stop_shared_clients)
        self.settings = HttpSettings(concurrency=1, proxy=ProxySettings(True, "http://user:password@proxy.test:17891"),
                                     retry=RetrySettings(attempts=3, base_delay=0, max_delay=0))

    def fake_sessions(self, get):
        session = SimpleNamespace(cookies=requests.Cookies(), get=get, close=Mock())
        patcher = patch("scrapers.core.session.requests.Session", return_value=session)
        patcher.start()
        self.addCleanup(patcher.stop)
        return session

    def wait_idle(self, pool):
        deadline = time.monotonic() + 3
        with pool._condition:
            while pool._busy and time.monotonic() < deadline:
                pool._condition.wait(.05)
        self.assertFalse(pool._busy)

    def test_all_three_sources_monitor_actual_retry_and_logical_outcome(self):
        get = Mock(side_effect=[TimeoutError(), response()] * 3)
        self.fake_sessions(get)
        for source in ("sehuatang", "javbee", "x1080x"):
            pool = shared_pool(source, self.settings, URL)
            self.assertTrue(pool.fetch(URL).ok)
            row = pool.lanes[0].network.snapshot()[0]
            self.assertEqual(source, row["source"])
            self.assertEqual((2, 1, 1, 0), tuple(row[k] for k in ("attempts", "retries", "completed", "failed")))

    def test_probe_uses_same_proxy_and_session_and_not_crawl_counters(self):
        get = Mock(side_effect=[TimeoutError()] * 3 + [response()])
        session = self.fake_sessions(get)
        pool = shared_pool("x1080x", self.settings, URL)
        self.assertFalse(pool.fetch(URL).ok)
        self.wait_idle(pool)
        monitor = pool.lanes[0].network
        monitor._domains["https://site.test"]["last_real"] -= 6
        pool._dispatch_diagnostics()
        self.wait_idle(pool)
        self.assertEqual("recovered", monitor.snapshot()[0]["state"])
        self.assertEqual(4, get.call_count)
        self.assertEqual(URL, get.call_args.args[0])
        self.assertEqual(self.settings.proxy.url, get.call_args.kwargs["proxies"]["https"])
        self.assertEqual(10, get.call_args.kwargs["timeout"])
        self.assertEqual((3, 1), tuple(monitor.snapshot()[0][k] for k in ("attempts", "failed")))
        pool.close()
        session.close.assert_called_once()
        self.assertFalse(pool._probe_thread.is_alive())

    def test_busy_waiting_or_rate_limited_lane_is_not_probed(self):
        get = Mock(side_effect=[TimeoutError()] * 3)
        self.fake_sessions(get)
        pool = shared_pool("x1080x", self.settings, URL)
        pool.fetch(URL)
        self.wait_idle(pool)
        pool.lanes[0].network._domains["https://site.test"]["last_real"] -= 6
        with pool._condition:
            pool._busy.add(0)
            pool._dispatch_diagnostics()
            pool._busy.clear()
            pool._waiting = 1
            pool._dispatch_diagnostics()
            pool._waiting = 0
            pool.lanes[0].gate.limit()
            pool._dispatch_diagnostics()
        self.assertEqual(3, get.call_count)

    def test_diagnostic_control_checks_original_proxy_and_reports_evidence(self):
        get = Mock(side_effect=[TimeoutError()] * 3 + [TimeoutError(), response(204, b"")])
        self.fake_sessions(get)
        pool = shared_pool("x1080x", self.settings, URL)
        pool.fetch(URL)
        self.wait_idle(pool)
        monitor = pool.lanes[0].network
        monitor._domains["https://site.test"]["last_real"] -= 6
        with patch("scrapers.core.session.socket.create_connection") as tcp:
            pool._dispatch_diagnostics()
            self.wait_idle(pool)
            tcp.assert_called_once_with(("proxy.test", 17891), timeout=3)
        self.assertEqual("https://www.gstatic.com/generate_204", get.call_args.args[0])
        self.assertEqual(self.settings.proxy.url, get.call_args.kwargs["proxies"]["https"])
        row = monitor.snapshot()[0]
        self.assertEqual("target_path", row["diagnostic"]["evidence"])
        self.assertEqual("suspect", row["state"])
        self.assertEqual(3, row["attempts"])
        self.assertEqual(3, row["consecutive_failures"])

    def test_proxy_unreachable_does_not_fall_back_to_direct(self):
        get = Mock(side_effect=[TimeoutError()] * 4)
        self.fake_sessions(get)
        pool = shared_pool("x1080x", self.settings, URL)
        pool.fetch(URL)
        self.wait_idle(pool)
        monitor = pool.lanes[0].network
        monitor._domains["https://site.test"]["last_real"] -= 6
        with patch("scrapers.core.session.socket.create_connection", side_effect=ConnectionRefusedError()):
            pool._dispatch_diagnostics()
            self.wait_idle(pool)
        self.assertEqual("proxy_unreachable", monitor.snapshot()[0]["diagnostic"]["evidence"])
        self.assertEqual(4, get.call_count)

    def test_api_is_authenticated_and_never_returns_credentials_or_raw_url(self):
        self.fake_sessions(Mock(return_value=response()))
        pool = shared_pool("x1080x", self.settings, URL)
        pool.fetch(URL)
        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(token="test", config_path=tmp + "/config.yaml"))
            self.assertEqual(401, client.get("/api/network").status_code)
            result = client.get("/api/network", headers={"X-Token": "test"})
        self.assertEqual(200, result.status_code)
        self.assertEqual(300, result.json()["window_seconds"])
        for secret in ("user", "password", "token=secret"):
            self.assertNotIn(secret, result.text)

    def test_last_response_is_not_reused_after_timeout(self):
        get = Mock(side_effect=[response(503, b"old"), TimeoutError(), TimeoutError()])
        result = CrawlerHttpClient("test", self.settings, request_func=get, sleeper=lambda _: None).fetch(URL)
        self.assertIsNone(result.status_code)
        self.assertIsNone(result.body)
