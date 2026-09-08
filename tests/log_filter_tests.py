import logging
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bs4 import BeautifulSoup

from util.log_reader import LOG_LEVELS, read_log_tail
from util.log_util import LOG_MODULES, log


def entry(number, module="x1080x", level="INFO", message=None):
    marker = f"[module={module}] " if module else ""
    return f"2026-09-08 16:00:00,000 - {level} - {marker}{message or f'event-{number}'}\n"


class LogModuleTests(unittest.TestCase):
    def test_caller_components_get_fixed_module_labels(self):
        callers = {
            "scrapers.web_scraper": "sehuatang",
            "scrapers.sources.javbee.source": "javbee",
            "scrapers.sources.x1080x.rate_limit": "x1080x",
            "notifications.memory_queue": "telegram",
            "util.sendTelegram": "telegram",
            "util.mongo": "database",
            "scrapers.infrastructure.json_failures": "database",
            "util.scheduler_manager": "scheduler",
            "web.app": "system",
        }
        for caller, expected in callers.items():
            with self.subTest(caller=caller), self.assertLogs("crawler", level="INFO") as captured:
                exec(compile('log.info("x1080x mentioned in message")', "caller_fixture.py", "exec"),
                     {"__name__": caller, "log": log})
            self.assertEqual(expected, captured.records[0].component)
            self.assertTrue(captured.records[0].pathname.endswith("caller_fixture.py"))

    def test_bind_is_thread_safe_and_does_not_change_global_logger(self):
        with self.assertLogs("crawler", level="INFO") as captured:
            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(lambda source: log.bind(module=source).info(source),
                              ("sehuatang", "javbee", "x1080x")))
            log.info("system")
        self.assertEqual({("sehuatang", "sehuatang"), ("javbee", "javbee"),
                          ("x1080x", "x1080x"), ("system", "system")},
                         {(record.component, record.getMessage()) for record in captured.records})

    def test_exception_keeps_context_and_traceback(self):
        formatter = log._logger.handlers[0].formatter
        with self.assertLogs("crawler", level="ERROR") as captured:
            try:
                raise ValueError("example failure")
            except ValueError:
                log.bind(module="telegram", source="x1080x").exception("send failed")
        rendered = formatter.format(captured.records[0])
        self.assertIn(" - ERROR - [module=telegram] send failed source=x1080x", rendered)
        self.assertIn("Traceback", rendered)
        self.assertIn("ValueError: example failure", rendered)

    def test_shared_http_retries_use_the_source_module(self):
        from types import SimpleNamespace
        from scrapers.core.config import HttpSettings, RetrySettings
        from scrapers.core.http import CrawlerHttpClient
        responses = iter([503, 200])
        client = CrawlerHttpClient("javbee", HttpSettings(retry=RetrySettings(attempts=2)),
                                   request_func=lambda *a, **k: SimpleNamespace(
                                       status_code=next(responses), content=b"ok", headers={}),
                                   sleeper=lambda _: None)
        with self.assertLogs("crawler", level="WARNING") as captured:
            self.assertTrue(client.fetch("https://example.invalid").ok)
        self.assertEqual("javbee", captured.records[0].component)


class LogReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "crawler.log"

    def write(self, content):
        self.path.write_text(content, encoding="utf-8")

    def test_combined_filters_find_older_matches_beyond_recent_200_lines(self):
        self.write(entry(1, level="ERROR") + entry(2, module="telegram", level="ERROR")
                   + "".join(entry(n, module="sehuatang") for n in range(3, 4003)))
        result = read_log_tail(self.path, limit=200, level="ERROR", module="x1080x")
        self.assertEqual([entry(1, level="ERROR").strip()], result["lines"])
        self.assertEqual(1, result["matched_count"])

    def test_tracebacks_and_continuations_stay_with_their_parent(self):
        self.write(entry(1, module="telegram", level="ERROR")
                   + "Traceback (most recent call last):\n  stack frame\nValueError: fail\n"
                   + entry(2))
        result = read_log_tail(self.path, limit=1, module="telegram")
        self.assertEqual(1, result["matched_count"])
        self.assertEqual(4, len(result["lines"]))
        self.assertEqual("ValueError: fail", result["lines"][-1])
        self.assertEqual([entry(2).strip()], read_log_tail(self.path, 1, module="x1080x")["lines"])

    def test_old_logs_are_unclassified_even_if_message_mentions_a_source(self):
        self.write(entry(1, module=None, level="ERROR", message="source=x1080x") + entry(2))
        result = read_log_tail(self.path, level="ERROR", module="unclassified")
        self.assertEqual(1, result["matched_count"])
        self.assertEqual([], read_log_tail(self.path, level="ERROR", module="x1080x")["lines"])

    def test_requested_count_is_matching_entries_in_chronological_order(self):
        self.write("".join(entry(n, module="x1080x" if n % 2 else "javbee") for n in range(1, 11)))
        self.assertEqual([entry(7).strip(), entry(9).strip()],
                         read_log_tail(self.path, 2, module="x1080x")["lines"])

    def test_scan_cap_drops_partial_entry_and_reports_limit(self):
        self.write(entry(1, level="ERROR") + "x" * 300 + "\n" + entry(2))
        result = read_log_tail(self.path, level="ERROR", max_bytes=150)
        self.assertEqual([], result["lines"])
        self.assertTrue(result["scan_limited"])
        self.assertLessEqual(result["scanned_bytes"], 150)

    def test_multibyte_long_record_crosses_read_blocks_without_corruption(self):
        body = entry(1, message="中文" * 14000)
        self.write(body + entry(2, module="javbee"))
        result = read_log_tail(self.path, 1, module="x1080x")
        self.assertEqual([body.strip()], result["lines"])
        self.assertNotIn("\ufffd", result["lines"][0])

    def test_plain_legacy_lines_and_missing_file_remain_readable(self):
        self.assertEqual([], read_log_tail(self.path)["lines"])
        self.write("old-one\nold-two\nold-three\n")
        self.assertEqual(["old-two", "old-three"], read_log_tail(self.path, 2)["lines"])

    def test_page_filter_choices_match_server_contract(self):
        html = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual({"", *LOG_MODULES}, {opt["value"] for opt in soup.select("#logModule option")})
        self.assertEqual({"", *LOG_LEVELS}, {opt["value"] for opt in soup.select("#logLevel option")})


if __name__ == "__main__":
    unittest.main()
