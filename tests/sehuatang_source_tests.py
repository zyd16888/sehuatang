import unittest

from scrapers.core.contracts import CrawlTarget
from scrapers.web_scraper import WebScraper


class FakeFailureStore:
    def __init__(self):
        self.recorded = []
        self.cleared = []
        self.targets = []

    def record(self, failures):
        self.recorded.extend(failures)

    def clear(self, source, keys):
        self.cleared.extend((source, key) for key in keys)

    def due_targets(self, source):
        return list(self.targets)


class SehuatangFailureTests(unittest.TestCase):
    def test_detail_fetch_failure_keeps_source_metadata(self):
        scraper = WebScraper.__new__(WebScraper)
        scraper.log = __import__("util.log_util", fromlist=["log"]).log
        scraper.dry_run = False
        scraper.workers = 1
        scraper.http = type(
            "Http",
            (),
            {
                "settings": type(
                    "Settings",
                    (),
                    {"retry": type("Retry", (), {"attempts": 3})()},
                )()
            },
        )()
        scraper.failure_store = FakeFailureStore()
        scraper.data_processor = type(
            "Processor",
            (),
            {
                "merge_thread_data": lambda self, results, info: [],
                "clean_data": lambda self, rows: rows,
            },
        )()
        scraper._fetch_many = lambda urls: [None]

        rows, failures = scraper._get_thread_details_batch_result(
            [
                {
                    "tid": "123",
                    "fid": 103,
                    "title": "ABP-123",
                    "number": "ABP-123",
                    "date": "2026-09-04",
                }
            ]
        )

        self.assertEqual([], rows)
        self.assertEqual(1, failures)
        failure = scraper.failure_store.recorded[0]
        self.assertEqual("123", failure.key)
        self.assertEqual(103, failure.metadata["fid"])

    def test_validation_failure_is_counted_for_checkpoint_safety(self):
        scraper = WebScraper.__new__(WebScraper)
        scraper.log = __import__("util.log_util", fromlist=["log"]).log
        scraper.dry_run = False
        scraper.workers = 1
        scraper.http = type(
            "Http",
            (),
            {
                "settings": type(
                    "Settings",
                    (),
                    {"retry": type("Retry", (), {"attempts": 3})()},
                )()
            },
        )()
        scraper.failure_store = FakeFailureStore()
        scraper.page_parser = type(
            "Parser",
            (),
            {"parse_thread_page": lambda self, body: {"post_time": "2026-09-04"}},
        )()
        scraper.data_processor = type(
            "Processor",
            (),
            {
                "merge_thread_data": lambda self, results, info: [{"tid": "123"}],
                "clean_data": lambda self, rows: [],
            },
        )()
        scraper._fetch_many = lambda urls: [b"html"]

        rows, failures = scraper._get_thread_details_batch_result(
            [{"tid": "123", "fid": 103}]
        )

        self.assertEqual([], rows)
        self.assertEqual(1, failures)
        self.assertEqual("validate", scraper.failure_store.recorded[0].stage)

    def test_retry_failed_groups_by_fid_saves_and_clears(self):
        scraper = WebScraper.__new__(WebScraper)
        scraper.http = object()  # 该测试注入详情结果，不启动 HTTP 流水线。
        scraper.log = __import__("util.log_util", fromlist=["log"]).log
        scraper.dry_run = False
        scraper.failure_store = FakeFailureStore()
        scraper.failure_store.targets = [
            CrawlTarget(
                key="123",
                url="https://example.test/123",
                partition="103",
                metadata={
                    "tid": "123",
                    "fid": 103,
                    "title": "ABP-123",
                    "number": "ABP-123",
                    "date": "2026-09-04",
                },
            )
        ]
        record = {"tid": "123", "post_time": "2026-09-04", "magnet": "m"}
        scraper._get_thread_details_batch_result = lambda info: ([record], 0)
        def save(manager, rows, fid, **kwargs):
            kwargs["stats"].update(saved=len(rows), existing=0)
            return rows
        scraper.data_manager = type(
            "Manager",
            (),
            {"filter_and_save_data": save},
        )()

        from scrapers.data_processor import DataProcessor
        scraper.data_processor = DataProcessor()
        summary = scraper.retry_failed_details()

        self.assertEqual({"discovered": 1, "requested": 1, "failed": 0, "saved": 1},
                         {key: summary[key] for key in ("discovered", "requested", "failed", "saved")})
        self.assertEqual("success", summary["status"])
        self.assertEqual([("sehuatang", "123")], scraper.failure_store.cleared)


if __name__ == "__main__":
    unittest.main()
