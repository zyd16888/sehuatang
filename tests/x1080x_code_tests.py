import copy
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

from scripts.backfill_x1080x_codes import backfill_codes, code_patch
from util.javbee_code import normalize_code_key, resolve_javbee_code, resolve_x1080x_code
from util.resource_clock import X1080X_RESOURCE_FIELDS, fingerprint


SAMPLES = [
    ("1013259", "(杏吧傳媒)(xb-1774)(20260304)小姨子的性愛特訓班上早洩治療-aka布丁",
     "magnet:?xt=urn:btih:985a69dc473cf145a8a8313c6f22cfb194f5ffec&dn=xb-1774", "XB-1774"),
    ("1013258", "(麻豆傳媒)(m-331)(20260303)不傳之秘黑絲家教老師真人性啟蒙-亦可姐姐",
     "magnet:?xt=urn:btih:ae193a84fd86a78c17a1222ab4fc8d093a92d382&dn=m-331", "M-331"),
    ("1013257", "(JVID)(jv-78)(20260303)COS星穹鐵道黃泉一人挑戰群男-米歐",
     "magnet:?xt=urn:btih:fe81ce357a36ea7c1c6f003123f7a666182c989e&dn=jv-78", "JV-78"),
]

COMPOUND_SAMPLES = json.loads(
    (Path(__file__).parent / "fixtures" / "x1080x_compound_codes.json").read_text(encoding="utf-8")
)


def sample_documents():
    records = []
    for key, title, magnet, expected in SAMPLES:
        code = None if expected == "M-331" else expected
        record = {
            "_id": key, "source_key": key, "tid": int(key), "fid": 244,
            "title": title, "magnet": magnet, "magnets": [magnet],
            "code": code, "code_normalized": normalize_code_key(code),
            "code_source": "title" if code else None,
            "code_confidence": "high" if code else "unknown",
            "date": "2026-03-11", "section": "国内成人", "typeid": "5212",
            "img": ["https://example.invalid/off_m-331.jpg"],
            "created_at": datetime(2026, 9, 8, tzinfo=timezone.utc),
            "collected_at": datetime(2026, 9, 8, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 9, 8, tzinfo=timezone.utc),
            "resource_updated_at": datetime(2026, 9, 8, tzinfo=timezone.utc),
        }
        record["resource_fingerprint"] = fingerprint(record, X1080X_RESOURCE_FIELDS)
        records.append(record)
    return records


class CodeResolutionTests(unittest.TestCase):
    def test_user_samples(self):
        for key, title, magnet, expected in SAMPLES:
            with self.subTest(source_key=key):
                result = resolve_x1080x_code(title, [magnet])
                self.assertEqual(expected, result.code)
                self.assertEqual(expected.replace("-", ""), normalize_code_key(result.code))
                self.assertEqual(("title", "high", "bracket"),
                                 (result.source, result.confidence, result.rule))

    def test_fullwidth_and_underscore_single_letter(self):
        for title in ("（麻豆傳媒）（m-331）（20260303）标题", "(m_331)标题", "（ｍ－３３１）标题"):
            self.assertEqual("M-331", resolve_x1080x_code(title).code)

    def test_user_single_digit_sample_and_magnet_fallback(self):
        title = "(香蕉視頻)(xjx-2)(20260805)中國臺灣男優"
        result = resolve_x1080x_code(title)
        self.assertEqual(("XJX-2", "title", "high"),
                         (result.code, result.source, result.confidence))
        self.assertEqual("XJX2", normalize_code_key(result.code))
        self.assertEqual("XJX-2", code_patch({"title": title, "code": None})["code"])
        for dn, expected in (("xjx-2", "XJX-2"), ("xjx_2", "XJX-2"), ("m-1", "M-1")):
            result = resolve_x1080x_code("描述标题", [f"magnet:?dn={dn}"])
            self.assertEqual(expected, result.code)
            self.assertEqual("magnet_dn", result.source)

    def test_detail_parser_emits_corrected_code_fields(self):
        from scrapers.sources.x1080x.parser import X1080XParser
        for title, expected in ((SAMPLES[1][1], "M-331"),
                                ("(香蕉視頻)(xjx-2)(20260805)中國臺灣男優", "XJX-2")):
            body = (f'<span id="thread_subject">{title}</span>'
                    '<em id="authorposton1">发表于 2026-09-08</em>'
                    '<div id="postmessage_1">magnet:?xt=urn:btih:abc</div>').encode()
            payload = X1080XParser().parse_detail(body, "https://example.invalid/1", tid=1, fid=244)
            self.assertEqual(expected, payload["code"])
            self.assertEqual(expected.replace("-", ""), payload["code_normalized"])
            self.assertEqual("title", payload["code_source"])
            self.assertEqual("high", payload["code_confidence"])

    def test_magnet_dn_fallback_and_url_decoding(self):
        for dn in ("m-331", "m_331", "m%2D331"):
            result = resolve_x1080x_code("描述标题", [f"magnet:?xt=urn:btih:abc&dn={dn}"])
            self.assertEqual(("M-331", "magnet_dn", "high"),
                             (result.code, result.source, result.confidence))

    def test_date_brand_and_compact_single_letter_are_not_codes(self):
        for value in ("H264", "H265", "M331", "20260303", "麻豆傳媒"):
            self.assertIsNone(resolve_x1080x_code(f"({value})标题").code)
            self.assertIsNone(resolve_x1080x_code("描述标题", [f"magnet:?dn={value}"]).code)

    def test_existing_multi_letter_and_javbee_rules_remain(self):
        self.assertEqual("XB-1774", resolve_x1080x_code("(xb1774)标题").code)
        self.assertEqual("M-331", resolve_javbee_code(None, "M-331 标题").code)
        self.assertEqual("CUSTOM", resolve_javbee_code("CUSTOM", "M-331 标题").code)

    def test_all_155_compound_identifiers_from_title_and_magnet(self):
        self.assertEqual(155, len(COMPOUND_SAMPLES))
        for sample in COMPOUND_SAMPLES:
            token, expected = sample["token"], sample["expected"]
            with self.subTest(token=token):
                result = resolve_x1080x_code(f"(来源)({token})(20260908)描述")
                self.assertEqual(expected, result.code)
                self.assertEqual(("title", "high"), (result.source, result.confidence))
                result = resolve_x1080x_code("描述", [f"magnet:?xt=urn:btih:abc&dn={quote(token)}"])
                self.assertEqual(expected, result.code)
                self.assertEqual(("magnet_dn", "high"), (result.source, result.confidence))

    def test_compound_segments_and_leading_zeroes_are_preserved(self):
        cases = {
            "mdsr-0010-1": ("MDSR-0010-1", "MDSR00101"),
            "mdsr-0010-2": ("MDSR-0010-2", "MDSR00102"),
            "thxp20230329_003": ("THXP20230329-003", "THXP20230329003"),
            "zb20230329_028": ("ZB20230329-028", "ZB20230329028"),
            "an-9-046": ("AN-9-046", "AN9046"),
            "mnsc-mb-097": ("MNSC-MB-097", "MNSCMB097"),
        }
        for token, expected in cases.items():
            result = resolve_x1080x_code(f"（来源）（{token}）描述")
            self.assertEqual(expected, (result.code, normalize_code_key(result.code)))
        self.assertEqual("MDSR-0010-1", resolve_x1080x_code(
            "描述", ["magnet:?dn=mdsr%2D0010%2D1"]
        ).code)

    def test_compound_tokens_are_not_truncated_to_a_valid_prefix(self):
        for token in ("20230329_003", "MNSC-MB", "H264-1080", "MDSR--0010-1",
                      "MDSR-0010-1-extra", "MDSR-0010-1.mp4", "MDSR-0010-1 描述"):
            with self.subTest(token=token):
                self.assertIsNone(resolve_x1080x_code(f"({token})描述").code)
                self.assertIsNone(resolve_x1080x_code("描述", [f"magnet:?dn={quote(token)}"]).code)


def matches(document, query):
    for key, value in query.items():
        if key == "$or":
            if not any(matches(document, clause) for clause in value):
                return False
        elif isinstance(value, dict) and "$in" in value:
            if document.get(key) not in value["$in"]:
                return False
        elif document.get(key) != value:
            return False
    return True


class FakeCursor(list):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeCollection:
    def __init__(self, documents):
        self.documents = copy.deepcopy(documents)
        self.writes = []
        self.before_write = None
        self.server_time = datetime(2026, 9, 9, tzinfo=timezone.utc)

    def count_documents(self, query):
        return sum(matches(document, query) for document in self.documents)

    def find(self, query, projection):
        return FakeCursor([{key: copy.deepcopy(value) for key, value in document.items()
                            if key == "_id" or projection.get(key)}
                           for document in self.documents if matches(document, query)])

    def bulk_write(self, operations, ordered=False):
        if self.before_write:
            self.before_write(self.documents)
            self.before_write = None
        count = 0
        self.writes.extend(operations)
        for operation in operations:
            for document in self.documents:
                if matches(document, operation._filter):
                    document.update(operation._doc["$set"])
                    for field in operation._doc["$currentDate"]:
                        document[field] = self.server_time
                    count += 1
                    break
        return SimpleNamespace(matched_count=count, modified_count=count)


class CodeBackfillTests(unittest.TestCase):
    def test_compound_batch_dry_run_reports_all_155_but_shows_only_50(self):
        documents = [{"_id": index, "title": f"(来源)({row['token']})描述", "code": None}
                     for index, row in enumerate(COMPOUND_SAMPLES)]
        collection = FakeCollection(documents)
        output = []
        stats = backfill_codes(collection, dry_run=True, sample_limit=50, emit=output.append)
        self.assertEqual((155, 155, 0, 0),
                         (stats["total"], stats["resolved"], stats["unresolved"], stats["modified"]))
        previews = [json.loads(line) for line in output if line.startswith("{")]
        self.assertEqual(50, len(previews))
        self.assertEqual([], collection.writes)
        self.assertEqual(documents, collection.documents)

    def test_compound_batch_apply_uses_full_codes_and_is_repeatable(self):
        documents = [{"_id": index, "title": f"(来源)({row['token']})描述", "code": None}
                     for index, row in enumerate(COMPOUND_SAMPLES)]
        collection = FakeCollection(documents)
        stats = backfill_codes(collection, batch_size=17, emit=lambda _: None)
        self.assertEqual(155, stats["modified"])
        for document, sample in zip(collection.documents, COMPOUND_SAMPLES):
            self.assertEqual(sample["expected"], document["code"])
            self.assertEqual(fingerprint(document, X1080X_RESOURCE_FIELDS), document["resource_fingerprint"])
        second = backfill_codes(collection, emit=lambda _: None)
        self.assertEqual(0, second["modified"])
        self.assertEqual(155, len(collection.writes))

    def test_dry_run_shows_only_missing_sample_field_changes_without_writes(self):
        documents = sample_documents()
        collection = FakeCollection(documents)
        output = []
        result = backfill_codes(collection, dry_run=True, emit=output.append)
        self.assertEqual(1, result["resolved"])
        preview = json.loads(output[1])
        self.assertEqual("1013258", preview["source_key"])
        self.assertEqual({"before": None, "after": "M-331"}, preview["changes"]["code"])
        self.assertIn("resource_fingerprint", preview["changes"])
        self.assertIn("resource_updated_at", preview["changes"])
        self.assertEqual([], collection.writes)
        self.assertEqual(documents, collection.documents)

    def test_apply_updates_code_and_clocks_preserves_provenance_and_is_repeatable(self):
        original = sample_documents()
        collection = FakeCollection(original)
        result = backfill_codes(collection, emit=lambda _: None)
        self.assertEqual(1, result["modified"])
        fixed = collection.documents[1]
        self.assertEqual("M-331", fixed["code"])
        self.assertEqual("M331", fixed["code_normalized"])
        self.assertEqual("title", fixed["code_source"])
        self.assertEqual("high", fixed["code_confidence"])
        self.assertEqual(collection.server_time, fixed["resource_updated_at"])
        self.assertEqual(fingerprint(fixed, X1080X_RESOURCE_FIELDS), fixed["resource_fingerprint"])
        for field in ("created_at", "collected_at", "title", "magnets", "section", "img"):
            self.assertEqual(original[1][field], fixed[field])
        self.assertEqual(original[0], collection.documents[0])
        self.assertEqual(original[2], collection.documents[2])
        second = backfill_codes(collection, emit=lambda _: None)
        self.assertEqual(0, second["modified"])
        self.assertEqual(1, len(collection.writes))

    def test_concurrent_changes_are_skipped_without_stale_fingerprint(self):
        for patch in ({"code": "MANUAL-001"}, {"resource_fingerprint": "new-digest"},
                      {"updated_at": datetime(2026, 9, 10, tzinfo=timezone.utc)}):
            with self.subTest(patch=patch):
                collection = FakeCollection(sample_documents())
                collection.before_write = lambda docs: docs[1].update(patch)
                result = backfill_codes(collection, emit=lambda _: None)
                self.assertEqual(0, result["modified"])
                self.assertEqual(1, result["skipped_concurrent"])
                expected = sample_documents()[1]
                expected.update(patch)
                self.assertEqual(expected, collection.documents[1])

    def test_missing_and_empty_codes_resolve_but_unknown_stays_empty(self):
        document = sample_documents()[1]
        for code in (None, ""):
            self.assertEqual("M-331", code_patch({**document, "code": code})["code"])
        document.pop("code")
        self.assertEqual("M-331", code_patch(document)["code"])
        self.assertIsNone(code_patch({**document, "code": "CUSTOM"}))
        self.assertIsNone(code_patch({"title": "(H264)(20260303)描述"}))

    def test_scoped_preview_does_not_include_other_threads(self):
        collection = FakeCollection(sample_documents())
        result = backfill_codes(collection, dry_run=True, source_keys=["1013259"], emit=lambda _: None)
        self.assertEqual(0, result["total"])
        result = backfill_codes(collection, dry_run=True, source_keys=["1013258"], emit=lambda _: None)
        self.assertEqual(1, result["resolved"])


if __name__ == "__main__":
    unittest.main()
