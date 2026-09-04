import unittest

from scripts.extract_and_query import (
    COLLECTIONS,
    generate_priority_query,
)


class MultiCollectionQueryTests(unittest.TestCase):
    def test_configures_eu_us_collection_as_uncensored(self):
        self.assertIn(("EU_US_no_mosaic", "uncensored"), COLLECTIONS)

    def test_generates_union_pipeline_and_dual_preferred_selection(self):
        collections = [
            ("subtitle_collection", "subtitle"),
            ("uncensored_collection", "uncensored"),
            ("regular_collection", "regular"),
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
                    ("duplicate", "subtitle"),
                    ("duplicate", "uncensored"),
                ],
            )

    def test_rejects_unknown_source_type(self):
        with self.assertRaisesRegex(ValueError, "不受支持"):
            generate_priority_query(
                ["ABP-123"],
                [("collection", "unknown")],
            )


if __name__ == "__main__":
    unittest.main()
