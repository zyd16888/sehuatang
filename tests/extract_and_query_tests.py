import unittest

from scripts.extract_and_query import (
    COLLECTIONS,
    CollectionSpec,
    generate_priority_query,
)


class MultiCollectionQueryTests(unittest.TestCase):
    def test_configures_eu_us_collection_as_uncensored(self):
        self.assertIn(
            CollectionSpec("EU_US_no_mosaic", "uncensored"),
            COLLECTIONS,
        )

    def test_configures_javbee_collection_fields(self):
        self.assertIn(
            CollectionSpec(
                "javbee_items",
                "regular",
                number_field="code",
                date_field="date",
                normalized_number_field="code_normalized",
            ),
            COLLECTIONS,
        )

    def test_generates_union_pipeline_and_dual_preferred_selection(self):
        collections = [
            CollectionSpec("subtitle_collection", "subtitle"),
            CollectionSpec("uncensored_collection", "uncensored"),
            CollectionSpec("regular_collection", "regular"),
        ]

        query = generate_priority_query(["ABP-123"], collections)

        self.assertEqual(2, query.count("    $unionWith: {"))
        self.assertEqual(1, query.count("const numberMatch"))
        self.assertIn('db.getCollection("subtitle_collection")', query)
        self.assertIn('coll: "uncensored_collection"', query)
        self.assertIn('source_type: "uncensored"', query)
        self.assertIn('["subtitle", "uncensored"]', query)
        self.assertIn('preferred_versions', query)
        self.assertIn('cracked_versions', query)
        self.assertIn('regular_versions', query)
        self.assertIn('$unwind: "$selected_versions"', query)
        self.assertIn('allowDiskUse: true', query)

    def test_rejects_duplicate_collections(self):
        with self.assertRaisesRegex(ValueError, "重复"):
            generate_priority_query(
                ["ABP-123"],
                [
                    CollectionSpec("duplicate", "subtitle"),
                    CollectionSpec("duplicate", "uncensored"),
                ],
            )

    def test_rejects_unknown_source_type(self):
        with self.assertRaisesRegex(ValueError, "不受支持"):
            generate_priority_query(
                ["ABP-123"],
                [CollectionSpec("collection", "unknown")],
            )

    def test_generates_javbee_match_and_canonical_field_mapping(self):
        query = generate_priority_query(
            ["MIDV-086"],
            [
                CollectionSpec("forum", "regular"),
                CollectionSpec(
                    "javbee_items",
                    "regular",
                    number_field="code",
                    date_field="date",
                    normalized_number_field="code_normalized",
                ),
            ],
        )

        self.assertIn('"code_normalized": { $in: ["MIDV086"] }', query)
        self.assertIn('"code": { $regex: "^MIDV[- ]?086"', query)
        self.assertIn("{ $match: numberMatch2 }", query)
        self.assertIn('number: { $ifNull: ["$code", "$code_normalized"] }', query)
        self.assertIn('post_time: "$date"', query)
        self.assertIn('source_collection: "javbee_items"', query)

    def test_maps_fields_when_nonstandard_collection_is_first(self):
        query = generate_priority_query(
            ["MIDV-086"],
            [
                CollectionSpec(
                    "javbee_items",
                    "regular",
                    number_field="code",
                    date_field="date",
                    normalized_number_field="code_normalized",
                ),
            ],
        )

        self.assertIn('db.getCollection("javbee_items")', query)
        self.assertIn('number: { $ifNull: ["$code", "$code_normalized"] }', query)
        self.assertIn('post_time: "$date"', query)


if __name__ == "__main__":
    unittest.main()
