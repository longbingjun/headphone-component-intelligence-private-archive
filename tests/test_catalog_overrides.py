from __future__ import annotations

import unittest

from core.catalog_overrides import (
    combined_brand_aliases,
    load_catalog_manual_overrides,
    source_is_catalog_excluded,
    source_override,
)


class CatalogOverridesTests(unittest.TestCase):
    def test_source_override_is_keyed_by_type_and_source_id(self) -> None:
        overrides = {
            "records": {
                "report:10": {"action": "exclude_catalog"},
                "video:10": {"identity": {"brand": "Demo", "model": "V1"}},
            }
        }
        record = {"id": "10"}
        self.assertTrue(source_is_catalog_excluded("report", record, overrides))
        self.assertFalse(source_is_catalog_excluded("video", record, overrides))
        self.assertEqual(
            source_override("video", record, overrides)["identity"]["model"],
            "V1",
        )

    def test_reviewed_brand_alias_has_one_owner(self) -> None:
        base = [("Old", ["Shared", "Old only"]), ("Other", ["Other"])]
        overrides = {
            "brand_aliases": [
                {"canonical": "Accepted", "aliases": ["Shared", "Accepted short"]}
            ]
        }
        combined = combined_brand_aliases(base, overrides)
        owners = [canonical for canonical, aliases in combined if "Shared" in aliases]
        self.assertEqual(owners, ["Accepted"])
        self.assertIn(("Old", ["Old only"]), combined)

    def test_related_intelligence_is_not_a_delete(self) -> None:
        overrides = {"records": {"report:88": {"action": "related_intelligence_only"}}}
        self.assertFalse(source_is_catalog_excluded("report", {"id": "88"}, overrides))

    def test_missing_optional_review_file_loads_safe_defaults(self) -> None:
        load_catalog_manual_overrides.cache_clear()
        overrides = load_catalog_manual_overrides()
        self.assertEqual(overrides.get("records", {}), {})
        self.assertEqual(overrides.get("brand_aliases", []), [])


if __name__ == "__main__":
    unittest.main()
