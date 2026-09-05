import json
import unittest
from contextlib import redirect_stdout
from datetime import date
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from scrapers.javbee_parser import JavbeeParser
from scrapers.javbee_scraper import JavbeeScraper
from scripts.backfill_javbee_codes import backfill_codes
from scripts.migrate_javbee_mysql_to_mongodb import migrate, row_to_document
from util.javbee_code import normalize_code_key, resolve_javbee_code
from util.mongo import JAVBEE_COLLECTION_NAME, save_javbee_items


LIST_HTML = b"""
<html><body>
  <h5 class="title is-4 is-spaced"><a href="/detail/abc123">ABC123</a></h5>
  <h5 class="title is-4 is-spaced"><a href="https://other.example/detail/bad">bad</a></h5>
  <a class="pagination-link" href="/new?page=7">7</a>
</body></html>
"""

DETAIL_HTML = b"""
<html><body>
  <h1 class="title is-4 is-spaced"><a>ABC123</a><span>1.2 GB</span></h1>
  <p class="subtitle is-6"><a href="/date/20260904">date</a></p>
  <div class="column"><img data-src="/images/abc123.jpg"></div>
  <a href="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567">magnet</a>
  <a href="/download/torrent/42">torrent</a>
</body></html>
"""


class JavbeeParserTests(unittest.TestCase):
    def setUp(self):
        self.parser = JavbeeParser()

    def test_parses_list_and_last_page(self):
        self.assertEqual(
            ["https://javbee.co/detail/abc123"],
            self.parser.parse_list(LIST_HTML, "https://javbee.co"),
        )
        self.assertEqual(7, self.parser.parse_last_page(LIST_HTML))

    def test_parses_complete_detail_contract(self):
        item = self.parser.parse_detail(
            DETAIL_HTML,
            "https://javbee.co/detail/abc123",
        )
        self.assertEqual("abc123", item["source_key"])
        self.assertEqual("2026-09-04", item["date"])
        self.assertEqual("ABC123", item["code"])
        self.assertEqual("ABC123", item["code_normalized"])
        self.assertEqual("high", item["code_confidence"])
        self.assertEqual("https://javbee.co/images/abc123.jpg", item["img"])
        self.assertEqual("https://javbee.co/download/torrent/42", item["torrent"])
        self.assertTrue(item["magnet"].startswith("magnet:?xt=urn:btih:"))


class JavbeeScraperTests(unittest.TestCase):
    def test_refreshes_discovered_urls_and_saves_items(self):
        pages = {
            "https://javbee.co/new": LIST_HTML,
            "https://javbee.co/new?page=2": LIST_HTML,
            "https://javbee.co/new?page=3": LIST_HTML,
            "https://javbee.co/new?page=4": LIST_HTML,
            "https://javbee.co/new?page=5": LIST_HTML,
            "https://javbee.co/new?page=6": LIST_HTML,
            "https://javbee.co/new?page=7": LIST_HTML,
            "https://javbee.co/detail/abc123": DETAIL_HTML,
        }
        scraper = JavbeeScraper(
            config={
                "base_url": "https://javbee.co",
                "start_path": "/new",
                "page_limit": 7,
                "concurrent_workers": 2,
            },
            http_get=pages.get,
        )

        with patch(
            "scrapers.javbee_scraper.find_existing_javbee_urls",
            return_value={"https://javbee.co/detail/abc123"},
        ), patch(
            "scrapers.javbee_scraper.save_javbee_items",
            return_value={"upserted": 0, "modified": 1},
        ) as save_mock:
            summary = scraper.crawl()

        self.assertEqual(1, summary["discovered"])
        self.assertEqual(1, summary["existing"])
        self.assertEqual(1, summary["requested"])
        self.assertEqual(1, summary["updated"])
        self.assertEqual("abc123", save_mock.call_args.args[0][0]["source_key"])


class JavbeeMigrationTests(unittest.TestCase):
    def test_maps_legacy_mysql_row_without_losing_status_fields(self):
        document = row_to_document(
            {
                "id": 42,
                "date": date(2020, 1, 2),
                "url": "https://javbee.co/detail/abc123",
                "title": "ABC123",
                "code": "",
                "img": "https://img.example/abc.jpg",
                "size": "1.2 GB",
                "magnet": None,
                "torrent": "https://javbee.co/download/torrent/42",
                "complete": 1,
                "ised2k": 0,
                "publish": 1,
            }
        )

        self.assertEqual(42, document["legacy_mysql_id"])
        self.assertEqual("2020-01-02", document["date"])
        self.assertIsNone(document["code"])
        self.assertEqual("unknown", document["code_confidence"])
        self.assertEqual(1, document["complete"])
        self.assertEqual(1, document["publish"])

    def test_preserves_existing_legacy_code(self):
        document = row_to_document(
            {
                "id": 43,
                "date": "2022-04-18",
                "url": "https://javbee.co/detail/fhdc-midv-086",
                "title": "[FHDC] MIDV-086 title",
                "code": "MIDV-086",
            }
        )

        self.assertEqual("MIDV-086", document["code"])
        self.assertEqual("legacy_mysql", document["code_source"])
        self.assertEqual("MIDV086", document["code_normalized"])

    def test_dry_run_pages_by_mysql_id_without_writing_mongodb(self):
        rows = [
            {
                "id": 42,
                "date": "2020-01-02",
                "url": "https://javbee.co/detail/abc123",
                "title": "ABC123",
            }
        ]
        with patch(
            "scripts.migrate_javbee_mysql_to_mongodb.fetch_batch",
            side_effect=[rows, []],
        ) as fetch_mock, patch(
            "scripts.migrate_javbee_mysql_to_mongodb.save_javbee_items"
        ) as save_mock:
            summary = migrate(
                connection=object(),
                batch_size=1000,
                resume_after_id=0,
                limit=0,
                dry_run=True,
            )

        self.assertEqual(1, summary["read"])
        self.assertEqual(42, summary["last_id"])
        self.assertEqual(2, fetch_mock.call_count)
        save_mock.assert_not_called()


class JavbeeMongoRepositoryTests(unittest.TestCase):
    def test_uses_named_collection_contract_and_bulk_upsert(self):
        class FakeCollection:
            def __init__(self):
                self.indexes = []
                self.operations = []
                self.ordered = None

            def create_index(self, keys, **options):
                self.indexes.append((keys, options))

            def bulk_write(self, operations, ordered):
                self.operations = operations
                self.ordered = ordered
                return SimpleNamespace(
                    matched_count=0,
                    modified_count=0,
                    upserted_count=1,
                )

        collection = FakeCollection()
        summary = save_javbee_items(
            [
                {
                    "source_key": "abc123",
                    "date": "2026-09-04",
                    "url": "https://javbee.co/detail/abc123",
                    "title": "ABC123",
                }
            ],
            collection=collection,
        )

        self.assertEqual("javbee_items", JAVBEE_COLLECTION_NAME)
        self.assertIn("uniq_source_key", [options["name"] for _, options in collection.indexes])
        self.assertTrue(collection.ordered)
        self.assertEqual(2, len(collection.operations))
        self.assertTrue(collection.operations[0]._doc["$setOnInsert"]["resource_collection_pending"])
        self.assertIsInstance(collection.operations[1]._doc, list)
        self.assertEqual(0, collection.operations[0]._doc["$setOnInsert"]["publish"])
        self.assertEqual(1, summary["upserted"])

    def test_migration_preserves_existing_source_fields(self):
        class FakeCollection:
            def create_index(self, keys, **options):
                return None

            def bulk_write(self, operations, ordered):
                self.operation = operations[0]
                return SimpleNamespace(
                    matched_count=1,
                    modified_count=1,
                    upserted_count=0,
                )

        collection = FakeCollection()
        save_javbee_items(
            [
                {
                    "source_key": "abc123",
                    "legacy_mysql_id": 42,
                    "date": "2020-01-02",
                    "url": "https://javbee.co/detail/abc123",
                    "title": "old title",
                    "complete": 1,
                    "ised2k": 0,
                    "publish": 1,
                }
            ],
            collection=collection,
            preserve_existing=True,
        )

        update = collection.operation._doc
        self.assertNotIn("title", update["$set"])
        self.assertEqual("old title", update["$setOnInsert"]["title"])
        self.assertEqual(1, update["$set"]["complete"])
        self.assertNotIn("collected_at", update["$setOnInsert"])
        self.assertNotIn("resource_updated_at", update["$set"])


class ResourceClockTests(unittest.TestCase):
    def test_collection_clock_is_separate_from_publication(self):
        from datetime import datetime, timezone
        from util.resource_clock import collected_document
        now = datetime(2026, 9, 6, tzinfo=timezone.utc)
        result = collected_document({"tid": "1", "post_time": "2020-01-01"}, now)
        self.assertEqual(now, result["collected_at"])
        self.assertEqual("2020-01-01", result["post_time"])

    def test_fingerprint_excludes_refresh_and_operational_metadata(self):
        from util.resource_clock import fingerprint
        item = {"title": "old", "magnet": None}
        self.assertEqual(fingerprint(item), fingerprint({**item, "updated_at": "later", "publish": 1}))
        self.assertNotEqual(fingerprint(item), fingerprint({**item, "magnet": "magnet:?new"}))

    def test_payload_update_preserves_collection_and_uses_atomic_change_clock(self):
        from util.resource_clock import resource_update_pipeline
        stage = resource_update_pipeline({"title": "$literal-title"})[0]["$set"]
        self.assertEqual("$$NOW", stage["collected_at"]["$cond"][1])
        self.assertEqual({"$literal": "$literal-title"}, stage["title"])
        self.assertEqual("$$NOW", stage["resource_updated_at"]["$cond"][1])
        self.assertEqual("$resource_updated_at", stage["resource_updated_at"]["$cond"][2])


class JavbeeCodeResolutionTests(unittest.TestCase):
    def test_extracts_high_confidence_codes(self):
        quality = resolve_javbee_code(None, "[FHDC] MIDV-086 title")
        fc2 = resolve_javbee_code(None, "FC2-PPV-4971063 title")
        compact = resolve_javbee_code(None, "ARM778")

        self.assertEqual(("MIDV-086", "high"), (quality.code, quality.confidence))
        self.assertEqual(("FC2-PPV-4971063", "high"), (fc2.code, fc2.confidence))
        self.assertEqual("catalog_only", compact.title_kind)

    def test_marks_ambiguous_formats_as_medium_confidence(self):
        prefixed = resolve_javbee_code(None, "[FHD] 300MAAN-774 title")
        dated = resolve_javbee_code(None, "090326_001-1PON title")

        self.assertEqual(("MAAN-774", "medium"), (prefixed.code, prefixed.confidence))
        self.assertEqual("medium", dated.confidence)
        self.assertEqual("0903260011PON", normalize_code_key(dated.code))


class FakeMongoCollection:
    def __init__(self, documents):
        self.documents = documents
        self.operations = []

    def find(self, query, projection):
        ids = set(query["legacy_mysql_id"]["$in"])
        return [
            document
            for document in self.documents
            if document["legacy_mysql_id"] in ids
        ]

    def bulk_write(self, operations, ordered):
        self.operations.extend(operations)
        return SimpleNamespace(
            matched_count=len(operations),
            modified_count=len(operations),
        )


class JavbeeCodeBackfillTests(unittest.TestCase):
    def test_dry_run_reports_confidence_without_writing(self):
        collection = FakeMongoCollection(
            [
                {"legacy_mysql_id": 1, "source_key": "fhdc-midv-086", "code": "FHDC-MIDV-086"},
                {"legacy_mysql_id": 2, "source_key": "fhd-300maan-774", "code": "FHD-300MAAN-774"},
                {"legacy_mysql_id": 3, "source_key": "unknown", "code": "UNKNOWN"},
            ]
        )

        rows = [
            {"id": 1, "title": "[FHDC] MIDV-086 title"},
            {"id": 2, "title": "[FHD] 300MAAN-774 title"},
            {"id": 3, "title": "无法识别的标题"},
        ]
        with patch(
            "scripts.backfill_javbee_codes.fetch_missing_code_batch",
            side_effect=[rows, []],
        ):
            output = StringIO()
            with redirect_stdout(output):
                summary = backfill_codes(object(), collection, apply=False)

        self.assertEqual(3, summary["scanned"])
        self.assertEqual(1, summary["eligible"])
        self.assertEqual(2, summary["skipped"])
        self.assertEqual(2, summary["would_clear"])
        changes = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(
            ["replace_code", "clear_to_candidate", "clear_unresolved"],
            [change["action"] for change in changes],
        )
        self.assertEqual("title", changes[0]["changes"]["code_source"]["to"])
        self.assertEqual([], collection.operations)

    def test_apply_can_include_medium_confidence(self):
        collection = FakeMongoCollection(
            [
                {"legacy_mysql_id": 1, "source_key": "fhdc-midv-086", "code": "FHDC-MIDV-086"},
                {"legacy_mysql_id": 2, "source_key": "fhd-300maan-774", "code": "FHD-300MAAN-774"},
            ]
        )

        rows = [
            {"id": 1, "title": "[FHDC] MIDV-086 title"},
            {"id": 2, "title": "[FHD] 300MAAN-774 title"},
        ]
        with patch(
            "scripts.backfill_javbee_codes.fetch_missing_code_batch",
            side_effect=[rows, []],
        ):
            summary = backfill_codes(
                object(),
                collection,
                apply=True,
                include_medium=True,
            )

        self.assertEqual(2, summary["eligible"])
        self.assertEqual(2, summary["updated"])
        self.assertEqual(2, len(collection.operations))

    def test_apply_clears_old_slug_fallback_for_unresolved_rows(self):
        collection = FakeMongoCollection(
            [{"legacy_mysql_id": 3, "source_key": "unknown", "code": "UNKNOWN"}]
        )
        with patch(
            "scripts.backfill_javbee_codes.fetch_missing_code_batch",
            side_effect=[[{"id": 3, "title": "无法识别的标题"}], []],
        ):
            summary = backfill_codes(object(), collection, apply=True)

        update = collection.operations[0]._doc["$set"]
        self.assertIsNone(update["code"])
        self.assertIsNone(update["code_normalized"])
        self.assertEqual("unknown", update["code_confidence"])
        self.assertEqual(1, summary["would_clear"])


if __name__ == "__main__":
    unittest.main()
