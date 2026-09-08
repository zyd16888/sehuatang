import asyncio
import tempfile
import unittest
from pathlib import Path

from scrapers.core.contracts import NullFailureStore
from scrapers.core.models import FetchResult
from scrapers.data_manager import DataManager
from scrapers.data_processor import DataProcessor
from scrapers.page_backfill import PageCheckpointStore
from scrapers.page_parser import PageParser
from scrapers.sources.x1080x import X1080XRepository
from scrapers.web_scraper import WebScraper
from scrapers.x1080x_scraper import X1080XScraper
from tests.x1080x_tests import DETAIL_HTML, EMPTY_LIST_HTML, LIST_HTML, TYPE_MAP


class PageCheckpointStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = PageCheckpointStore(Path(self.tmp.name) / "progress.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip_and_partition_isolation(self):
        self.assertEqual(0, self.store.load("x1080x", "5479"))
        self.store.save("x1080x", "5479", 12)
        self.store.save("sehuatang", 103, 7)
        self.assertEqual(12, self.store.load("x1080x", "5479"))
        self.assertEqual(7, self.store.load("sehuatang", 103))

        self.store.clear("x1080x", "5479")
        self.assertEqual(0, self.store.load("x1080x", "5479"))
        self.assertEqual(7, self.store.load("sehuatang", 103))

    def test_corrupted_file_treated_as_empty(self):
        path = Path(self.tmp.name) / "progress.json"
        path.write_text("{broken", encoding="utf-8")
        self.assertEqual(0, self.store.load("x1080x", "5479"))


class FakeHttp:
    def __init__(self, pages):
        self.pages = pages
        self.fetched = []

    def fetch(self, url, stage="detail"):
        self.fetched.append(url)
        body = self.pages.get(url)
        return FetchResult(
            url=url,
            body=body,
            status_code=200 if body else 500,
            attempts=1,
            elapsed_ms=0,
            error_type=None if body else "http_status",
        )

    def fetch_many(self, urls, stage="detail"):
        return [self.fetch(url, stage) for url in urls]


class X1080XBackfillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.checkpoints = PageCheckpointStore(Path(self.tmp.name) / "p.json")
        self.saved_items = []

    def tearDown(self):
        self.tmp.cleanup()

    def _scraper(self, pages, existing=frozenset()):
        scraper = X1080XScraper.__new__(X1080XScraper)
        scraper.config = {
            "base_url": "https://agaghhh.cc",
            "fid": 244,
            "typeids": {"5479": "中文字幕"},
            "page_limit": 3,
        }
        scraper.http = FakeHttp(pages)
        scraper.failure_store = NullFailureStore()

        def fake_save(items):
            self.saved_items.extend(items)
            return {"processed": len(items), "upserted": len(items), "modified": 0}

        import scrapers.x1080x_scraper as module
        self._orig_lookup = module.find_existing_x1080x_keys
        self._orig_save = module.save_x1080x_items
        module.find_existing_x1080x_keys = lambda keys: set(existing) & set(keys)
        module.save_x1080x_items = fake_save
        self.addCleanup(self._restore, module)
        return scraper

    def _restore(self, module):
        module.find_existing_x1080x_keys = self._orig_lookup
        module.save_x1080x_items = self._orig_save

    def _urls(self):
        base = "https://agaghhh.cc/forum.php"
        return {
            "list1": f"{base}?mod=forumdisplay&fid=244&archiver=1&page=1&filter=typeid&typeid=5479",
            "list2": f"{base}?mod=forumdisplay&fid=244&archiver=1&page=2&filter=typeid&typeid=5479",
            "detail1001": f"{base}?mod=viewthread&tid=1001&archiver=1",
            "detail1002": f"{base}?mod=viewthread&tid=1002&archiver=1",
        }

    def test_backfills_page_range_and_advances_checkpoint(self):
        urls = self._urls()
        scraper = self._scraper({
            urls["list1"]: LIST_HTML,
            urls["list2"]: EMPTY_LIST_HTML,
            urls["detail1001"]: DETAIL_HTML,
            urls["detail1002"]: DETAIL_HTML,
        })

        summary = scraper.backfill_pages(
            1, 5, checkpoint_store=self.checkpoints
        )

        self.assertEqual(1, summary["pages_scanned"])
        self.assertEqual(2, summary["discovered"])
        self.assertEqual(2, summary["saved"])
        self.assertEqual("exhausted@2", summary["partitions"]["5479"]["stopped"])
        self.assertEqual(1, self.checkpoints.load("x1080x", "5479"))
        self.assertEqual(2, len(self.saved_items))

    def test_skips_existing_and_resumes_from_checkpoint(self):
        urls = self._urls()
        self.checkpoints.save("x1080x", "5479", 1)
        scraper = self._scraper(
            {
                urls["list2"]: EMPTY_LIST_HTML,
            },
            existing={"1001"},
        )

        summary = scraper.backfill_pages(
            1, 5, resume=True, checkpoint_store=self.checkpoints
        )

        # 检查点为 1，续跑从第 2 页开始且不再请求第 1 页
        self.assertNotIn(urls["list1"], scraper.http.fetched)
        self.assertEqual("exhausted@2", summary["partitions"]["5479"]["stopped"])

    def test_list_failure_stops_partition_without_checkpoint(self):
        urls = self._urls()
        scraper = self._scraper({
            urls["list1"]: LIST_HTML,
            urls["detail1001"]: DETAIL_HTML,
            urls["detail1002"]: DETAIL_HTML,
            # list2 缺失 -> 请求失败
        })

        summary = scraper.backfill_pages(
            1, 5, checkpoint_store=self.checkpoints
        )

        self.assertEqual("list_failed@2", summary["partitions"]["5479"]["stopped"])
        # 第 1 页完成检查点推进到 1，失败页未推进
        self.assertEqual(1, self.checkpoints.load("x1080x", "5479"))

    def test_unknown_typeid_raises(self):
        scraper = self._scraper({})
        with self.assertRaises(ValueError):
            scraper.backfill_pages(
                1, 2, typeids=["9999"], checkpoint_store=self.checkpoints
            )

    def test_rate_limited_list_and_detail_recover_before_checkpoint(self):
        from tests.x1080x_rate_limit_tests import RATE_HTML, scripted_client
        urls = self._urls()
        scraper = self._scraper({})
        scraper.http, clock, calls = scripted_client({
            urls["list1"]: [RATE_HTML, RATE_HTML, LIST_HTML],
            urls["detail1001"]: [DETAIL_HTML],
            urls["detail1002"]: [RATE_HTML, DETAIL_HTML],
            urls["list2"]: [EMPTY_LIST_HTML],
        })
        waited = []

        def wait(seconds):
            self.assertEqual(0, self.checkpoints.load("x1080x", "5479"))
            waited.append(seconds)
            clock.wait(seconds)

        scraper.http.gate._wait = wait
        from unittest.mock import Mock
        scraper.failure_store = Mock()
        summary = scraper.backfill_pages(1, 2, checkpoint_store=self.checkpoints)
        self.assertEqual(2, summary["saved"])
        self.assertEqual(0, summary["failed"])
        self.assertEqual(1, self.checkpoints.load("x1080x", "5479"))
        self.assertEqual(240, sum(waited))
        self.assertEqual(1, sum(url == urls["detail1001"] for url, _, _ in calls))
        scraper.failure_store.record.assert_not_called()

    def test_stop_during_detail_cooldown_keeps_previous_checkpoint(self):
        from tests.x1080x_rate_limit_tests import RATE_HTML, scripted_client
        from scrapers.sources.x1080x.rate_limit import CrawlStopped
        urls = self._urls()
        scraper = self._scraper({})
        scraper.http, _, _ = scripted_client({
            urls["list1"]: [LIST_HTML],
            urls["detail1001"]: [DETAIL_HTML],
            urls["detail1002"]: [RATE_HTML],
        })
        scraper.http.gate._wait = lambda _: scraper.http.gate.stop_event.set()
        with self.assertRaises(CrawlStopped):
            scraper.backfill_pages(1, 2, checkpoint_store=self.checkpoints)
        self.assertEqual(0, self.checkpoints.load("x1080x", "5479"))

    def test_dry_run_rate_limit_recovery_does_not_write(self):
        from tests.x1080x_rate_limit_tests import RATE_HTML, scripted_client
        urls = self._urls()
        scraper = self._scraper({})
        scraper.http, _, _ = scripted_client({
            urls["list1"]: [RATE_HTML, LIST_HTML],
            urls["detail1001"]: [DETAIL_HTML],
            urls["detail1002"]: [RATE_HTML, DETAIL_HTML],
        })
        summary = scraper.backfill_pages(1, 1, dry_run=True, checkpoint_store=self.checkpoints)
        self.assertEqual(0, summary["failed"])
        self.assertEqual([], self.saved_items)
        self.assertEqual(0, self.checkpoints.load("x1080x", "5479"))


def _plate_html(tids):
    rows = "".join(
        f"""
        <tbody id="normalthread_{tid}">
          <tr>
            <th><a class="s xst">ABP-{tid} title</a></th>
            <td class="by"><em><span title="2020-05-0{i + 1}">2020-05-0{i + 1}</span></em></td>
            <td><a class="showcontent y" id="content_{tid}"></a></td>
          </tr>
        </tbody>
        """
        for i, tid in enumerate(tids)
    )
    return f"<html><body><table>{rows}</table></body></html>"


class SehuatangBackfillPagesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.checkpoints = PageCheckpointStore(Path(self.tmp.name) / "p.json")

    def tearDown(self):
        self.tmp.cleanup()

    def _scraper(self, page_bodies, detail_results):
        scraper = WebScraper.__new__(WebScraper)
        scraper.log = __import__("util.log_util", fromlist=["log"]).log
        scraper.dry_run = False
        scraper.page_parser = PageParser()
        scraper.data_processor = DataProcessor()
        scraper.data_manager = DataManager.__new__(DataManager)
        scraper.data_manager.log = scraper.log
        scraper.data_manager.mongodb_enable = False
        scraper.failure_store = NullFailureStore()

        class FakeInnerHttp:
            def get_html(self, url):
                return page_bodies.get(url)

        scraper.http = FakeInnerHttp()

        saved = []
        scraper._saved = saved

        def fake_details(info_list):
            data = [
                detail_results[str(info["tid"])]
                for info in info_list
                if str(info["tid"]) in detail_results
            ]
            failures = len(info_list) - len(data)
            return data, failures

        scraper._get_thread_details_batch_result = fake_details

        def fake_save(data_list, fid, strict=False, dry_run=False):
            saved.extend(data_list)
            return data_list

        scraper.data_manager.filter_and_save_data = fake_save
        scraper.data_manager.compare_existing_data = (
            lambda tids, fid, infos: (tids, infos)
        )
        scraper._clear_detail_failures = lambda data_list: None
        return scraper

    def _url(self, fid, page):
        return WebScraper._build_plate_url(fid, page, ordered_by_dateline=True)

    def test_backfills_range_without_date_filter_and_checkpoints(self):
        detail = {"tid": "100", "post_time": "2020-05-01 10:00", "magnet": "magnet:?a"}
        scraper = self._scraper(
            {
                self._url(103, 1): _plate_html(["100"]),
                self._url(103, 2): "<html><body></body></html>",
            },
            {"100": detail},
        )

        summary = asyncio.run(
            scraper.backfill_pages(
                103, 1, 5, checkpoint_store=self.checkpoints
            )
        )

        self.assertFalse(scraper.data_processor.date_filter)
        self.assertEqual(1, summary["pages_scanned"])
        self.assertEqual(1, summary["saved"])
        self.assertEqual("exhausted@2", summary["stopped"])
        self.assertEqual(1, self.checkpoints.load("sehuatang", "103"))
        self.assertEqual([detail], scraper._saved)

    def test_detail_failures_do_not_block_checkpoint(self):
        scraper = self._scraper(
            {
                self._url(103, 1): _plate_html(["100", "101"]),
                self._url(103, 2): "<html><body></body></html>",
            },
            # 101 详情失败
            {"100": {"tid": "100", "post_time": "2020-05-01", "magnet": "magnet:?a"}},
        )

        summary = asyncio.run(
            scraper.backfill_pages(
                103, 1, 5, checkpoint_store=self.checkpoints
            )
        )

        self.assertEqual(1, summary["failed"])
        self.assertEqual(1, summary["saved"])
        # 详情失败由失败台账负责，页检查点照常推进
        self.assertEqual(1, self.checkpoints.load("sehuatang", "103"))

    def test_list_failure_stops_and_preserves_checkpoint(self):
        scraper = self._scraper(
            {
                self._url(103, 1): _plate_html(["100"]),
                # 第 2 页请求失败
            },
            {"100": {"tid": "100", "post_time": "2020-05-01", "magnet": "magnet:?a"}},
        )

        summary = asyncio.run(
            scraper.backfill_pages(
                103, 1, 5, checkpoint_store=self.checkpoints
            )
        )

        self.assertEqual("list_failed@2", summary["stopped"])
        self.assertEqual(1, self.checkpoints.load("sehuatang", "103"))

        # 续跑从第 3 页继续（第 2 页恢复后需要 --start-page 覆盖或表示已完成）
        # 此处验证 resume 语义：从检查点 +1 开始
        scraper2 = self._scraper(
            {
                self._url(103, 2): _plate_html(["102"]),
                self._url(103, 3): "<html><body></body></html>",
            },
            {"102": {"tid": "102", "post_time": "2020-05-02", "magnet": "magnet:?b"}},
        )
        summary2 = asyncio.run(
            scraper2.backfill_pages(
                103, 1, 5, resume=True, checkpoint_store=self.checkpoints
            )
        )
        self.assertEqual(1, summary2["saved"])


class DateFilterToggleTests(unittest.TestCase):
    def test_merge_keeps_all_dates_when_filter_disabled(self):
        processor = DataProcessor(target_date="2026-09-07")
        results = [
            ({"post_time": "2019-01-01 08:00", "magnet": "magnet:?x"}, {
                "number": "ABP-1", "title": "t", "date": "2019-01-01", "tid": "1",
            }),
        ]

        self.assertEqual(0, len(processor.merge_thread_data(results, [])))

        processor.date_filter = False
        merged = processor.merge_thread_data(results, [])
        self.assertEqual(1, len(merged))
        self.assertEqual("1", merged[0]["tid"])


if __name__ == "__main__":
    unittest.main()
