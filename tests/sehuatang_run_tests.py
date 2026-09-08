"""Sehuatang 运行统计链路测试：列表、详情、保存、汇总和历史落库。"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from pymongo.errors import BulkWriteError

from scrapers.core.config import HttpSettings
from scrapers.core.contracts import CrawlTarget
from scrapers.data_manager import DataManager
from scrapers.data_processor import DataProcessor
from scrapers.page_parser import PageParser
from scrapers.registry import SourceDefinition, SourceRegistry, _run_sehuatang
from scrapers.web_scraper import WebScraper, _new_run_stats
from util.mongo import record_crawl_run


def plate(*tids, date="2026-09-08"):
    rows = "".join(f"""<tbody id="normalthread_{tid}">
        <a class="s xst">TEST-{tid} Title</a><td class="by"><em>{date}</em></td>
        <a class="showcontent y" id="content_{tid}"></a></tbody>""" for tid in tids)
    return f'<div id="threadlist"><table id="threadlisttableid">{rows}</table></div>'.encode()


class SehuatangRunTests(unittest.TestCase):
    def setUp(self):
        self.pages = {1: plate("1", "2")}
        self.missing = set()
        self.existing = set()
        self.persisted = []
        self.scraper = WebScraper.__new__(WebScraper)
        self.scraper.log = mock.Mock()
        self.scraper.workers = 2
        self.scraper.http = SimpleNamespace(settings=HttpSettings())
        self.scraper.dry_run = False
        self.scraper.target_date = "2026-09-08"
        self.scraper.failure_store = mock.Mock()
        self.scraper.data_manager = DataManager()
        self.scraper.data_manager.mongodb_enable = True
        self.scraper.notification_manager = mock.Mock()
        self.scraper.notification_manager.enqueue_notifications.return_value = {"queued": 2, "rejected": 0}
        self.scraper.page_parser = PageParser()
        self.scraper.page_parser.parse_thread_page = lambda body: {
            "post_time": "2026-09-08 12:00", "magnet": "magnet:?test", "img": []}
        self.scraper.data_processor = DataProcessor("2026-09-08")

        def fetch(urls):
            rows = []
            for url in urls:
                query = parse_qs(urlsplit(url).query)
                if query.get("mod") == ["viewthread"]:
                    rows.append(None if query["tid"][0] in self.missing else b"detail")
                else:
                    page_number = urlsplit(url).path.rsplit("-", 1)[1].split(".")[0]
                    rows.append(self.pages[int(page_number)])
            return rows
        self.scraper._fetch_many = fetch

        def compare(tids, fid, infos):
            selected = [info for info in infos if str(info["tid"]) not in self.existing]
            return [info["tid"] for info in selected], selected
        def save(rows, fid):
            self.persisted.extend(rows)
        patches = [mock.patch("scrapers.data_manager.compare_tid", side_effect=compare),
                   mock.patch("scrapers.data_manager.filter_data", side_effect=lambda rows, fid: rows),
                   mock.patch("scrapers.data_manager.save_data", side_effect=save)]
        self.compare, self.filter, self.save = [patch.start() for patch in patches]
        for patch in patches:
            self.addCleanup(patch.stop)

    def run_section(self):
        with mock.patch("scrapers.web_scraper.page_num", len(self.pages)):
            return asyncio.run(self.scraper.crawl_forum_section(103))

    def assert_counts(self, result, discovered, requested, saved, failed, status):
        self.assertEqual((discovered, requested, saved, failed, status),
                         tuple(result[key] for key in ("discovered", "requested", "saved", "failed", "status")))

    def test_normal_run_counts_discovery_before_existing_filter(self):
        self.existing = {"1"}
        result = self.run_section()
        self.assert_counts(result, 2, 1, 1, 0, "success")

    def test_duplicate_list_entries_are_counted_once(self):
        self.pages[2] = plate("2", "3")
        self.assert_counts(self.run_section(), 3, 3, 3, 0, "success")

    def test_no_new_records_is_success_with_real_zeroes(self):
        self.existing = {"1", "2"}
        self.assert_counts(self.run_section(), 2, 0, 0, 0, "success")
        self.save.assert_not_called()

    def test_all_detail_failures_are_failed_not_no_new_data(self):
        self.missing = {"1", "2"}
        result = self.run_section()
        self.assert_counts(result, 2, 2, 0, 2, "failed")
        self.assertEqual({"detail": 2}, result["stage_failures"])

    def test_partial_details_keep_successful_counts(self):
        self.missing = {"2"}
        self.assert_counts(self.run_section(), 2, 2, 1, 1, "partial_success")

    def test_existing_records_do_not_mask_all_requested_details_failing(self):
        self.existing = {"1"}
        self.missing = {"2"}
        self.assert_counts(self.run_section(), 2, 1, 0, 1, "failed")

    def test_all_list_requests_failed_are_not_empty_success(self):
        self.pages = {1: None, 2: None}
        result = self.run_section()
        self.assert_counts(result, 0, 0, 0, 2, "failed")
        self.assertEqual(2, result["list_requested"])
        self.assertEqual(0, result["list_succeeded"])

    def test_partial_lists_preserve_discovered_records(self):
        self.pages[2] = None
        self.assert_counts(self.run_section(), 2, 2, 2, 1, "partial_success")

    def test_non_forum_document_is_a_list_failure(self):
        self.pages = {1: b"<html><body>Access denied</body></html>"}
        self.assert_counts(self.run_section(), 0, 0, 0, 1, "failed")

    def test_valid_empty_list_and_date_filter_are_not_failures(self):
        for body in (plate(), plate("1", date="2020-01-01")):
            self.pages = {1: body}
            self.assert_counts(self.run_section(), 0, 0, 0, 0, "success")

    def test_unparseable_threads_are_not_empty_success(self):
        self.pages = {1: b'<div id="threadlist"><tbody id="normalthread_1">broken</tbody></div>'}
        self.assert_counts(self.run_section(), 0, 0, 0, 1, "failed")

    def test_dry_run_does_not_report_writes_or_enqueue_notifications(self):
        self.scraper.dry_run = True
        self.assert_counts(self.run_section(), 2, 2, 0, 0, "success")
        self.save.assert_not_called()
        self.scraper.notification_manager.enqueue_notifications.assert_not_called()
        self.scraper.failure_store.clear.assert_not_called()

    def test_disabled_mongodb_has_no_saved_count(self):
        self.scraper.data_manager.mongodb_enable = False
        self.assert_counts(self.run_section(), 2, 2, 0, 0, "success")
        self.save.assert_not_called()

    def test_database_comparison_failure_is_failed(self):
        self.compare.side_effect = RuntimeError("database unavailable")
        self.assert_counts(self.run_section(), 2, 0, 0, 1, "failed")

    def test_database_write_failure_preserves_requested_count(self):
        self.save.side_effect = RuntimeError("write failed")
        result = self.run_section()
        self.assert_counts(result, 2, 2, 0, 2, "failed")
        self.assertEqual({"save": 2}, result["stage_failures"])
        self.assertEqual(2, len(self.scraper.failure_store.record.call_args.args[0]))
        self.scraper.notification_manager.enqueue_notifications.assert_not_called()

    def test_partial_insert_reports_only_confirmed_writes(self):
        self.save.side_effect = BulkWriteError({"nInserted": 1, "writeErrors": [{"index": 1}],
                                                "writeConcernErrors": []})
        result = self.run_section()
        self.assert_counts(result, 2, 2, 1, 1, "partial_success")
        self.scraper.failure_store.clear.assert_called_once_with("sehuatang", ["1"])
        failed = self.scraper.failure_store.record.call_args.args[0]
        self.assertEqual(["2"], [row.key for row in failed])
        notified = self.scraper.notification_manager.enqueue_notifications.call_args.args[0]
        self.assertEqual(["1"], [row["tid"] for row in notified])

    def test_write_concern_error_does_not_claim_confirmed_saves(self):
        self.save.side_effect = BulkWriteError({"nInserted": 2, "writeErrors": [],
                                                "writeConcernErrors": [{"code": 64}]})
        self.assert_counts(self.run_section(), 2, 2, 0, 2, "failed")

    def test_retry_uses_due_targets_and_keeps_prior_group_on_save_error(self):
        def target(tid, fid):
            return CrawlTarget(tid, "url", str(fid), {"fid": fid, "tid": tid, "title": "t",
                               "number": "TEST-1", "date": "2020-01-01", "retry_stage": "fetch"})
        self.scraper.failure_store.due_targets.return_value = [target("1", 103), target("2", 104)]
        self.save.side_effect = [None, RuntimeError("failed second board")]
        result = self.scraper.retry_failed_details()
        self.assert_counts(result, 2, 2, 1, 1, "partial_success")
        self.assertEqual("fetch", self.scraper.failure_store.record.call_args.args[0][0].metadata["retry_stage"])
        self.scraper.notification_manager.enqueue_notifications.assert_not_called()

    def test_retry_dry_run_has_zero_saved(self):
        self.scraper.dry_run = True
        self.scraper.failure_store.due_targets.return_value = [CrawlTarget("1", "url", "103",
            {"fid": 103, "tid": "1", "title": "t", "number": "TEST", "date": "2020-01-01"})]
        self.assert_counts(self.scraper.retry_failed_details(), 1, 1, 0, 0, "success")
        self.save.assert_not_called()

    def test_invalid_retry_metadata_is_visible_as_failure(self):
        self.scraper.failure_store.due_targets.return_value = [CrawlTarget("1", "url", "invalid")]
        self.assert_counts(self.scraper.retry_failed_details(), 1, 0, 0, 1, "failed")
        self.assertEqual("invalid_target_metadata",
                         self.scraper.failure_store.record.call_args.args[0][0].error_type)


class AggregationTests(unittest.TestCase):
    def test_source_history_retains_structured_counters(self):
        good = {**_new_run_stats(), "status": "success", "discovered": 8, "requested": 3,
                "saved": 3, "succeeded": 3}
        bad = {**_new_run_stats(), "status": "failed", "discovered": 2, "requested": 2,
               "failed": 2, "stage_failures": {"detail": 2}}
        source = mock.MagicMock()
        source.__enter__.return_value = source
        source.crawl_forum_section = mock.AsyncMock(side_effect=[good, bad])
        registry = SourceRegistry()
        registry.register(SourceDefinition("sehuatang", _run_sehuatang, True))
        with mock.patch("scrapers.registry.SehuatangSource", return_value=source), \
             mock.patch("util.read_config.get_config", return_value=True), \
             mock.patch("util.mongo.record_crawl_run") as record:
            result = asyncio.run(registry.run("sehuatang", {"sehuatang": {"fid": {103: "a", 104: "b"}}}))
        stored = record.call_args.args[0]
        self.assertEqual(result, stored)
        self.assertEqual((10, 5, 3, 2, "partial_success"),
                         tuple(stored[key] for key in ("discovered", "requested", "saved", "failed", "status")))
        self.assertEqual(1, stored["failed_sections"])
        self.assertEqual({"detail": 2}, stored["stage_failures"])
        self.assertIn("elapsed_ms", stored)
        self.assertEqual("crawl", stored["kind"])

    def test_mongo_history_writer_does_not_drop_counters(self):
        collection = mock.Mock()
        summary = {"source": "sehuatang", "discovered": 10, "requested": 5, "saved": 3,
                   "failed": 2, "status": "partial_success"}
        record_crawl_run(summary, collection=collection)
        stored = collection.insert_one.call_args.args[0]
        self.assertTrue(all(stored[key] == value for key, value in summary.items()))
        self.assertIn("created_at", stored)

    def test_runs_api_preserves_new_counters_and_unknown_old_fields(self):
        from fastapi.testclient import TestClient
        from web.app import create_app
        rows = [
            {"source": "sehuatang", "created_at": "2026-09-08T12:00:00Z", "status": "success",
             "discovered": 10, "requested": 2, "saved": 2, "failed": 0},
            {"source": "sehuatang", "created_at": "2026-09-07T12:00:00Z", "status": "success"},
        ]
        with mock.patch("util.read_config.get_config", return_value=True), \
             mock.patch("util.mongo.find_recent_crawl_runs", return_value=rows):
            client = TestClient(create_app(token="test"))
            data = client.get("/api/runs?source=sehuatang", headers={"X-Token": "test"}).json()["runs"]
        self.assertEqual((10, 2, 2, 0), tuple(data[0][key] for key in ("discovered", "requested", "saved", "failed")))
        self.assertNotIn("saved", data[1])


if __name__ == "__main__":
    unittest.main()
