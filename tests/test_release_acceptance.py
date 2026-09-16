from __future__ import annotations

import unittest

from scripts.release_acceptance import catalog_projection


class ReleaseAcceptanceTests(unittest.TestCase):
    def test_catalog_projection_detects_category_drift(self) -> None:
        current = {
            "products": [
                {
                    "canonical_id": "demo--buds",
                    "brand": "Demo",
                    "model": "Buds",
                    "category": "全入耳式耳机",
                    "category_status": "human_reviewed",
                    "report_count": 1,
                    "video_count": 0,
                    "ignored_curated_field": "not part of the public contract",
                }
            ]
        }
        stale = {
            "products": [
                {
                    "canonical_id": "demo--buds",
                    "brand": "Demo",
                    "model": "Buds",
                    "category": "待细分耳机",
                    "category_status": "needs_review",
                    "report_count": 1,
                    "video_count": 0,
                }
            ]
        }
        self.assertNotEqual(catalog_projection(current), catalog_projection(stale))

    def test_catalog_projection_ignores_non_public_fields(self) -> None:
        left = {
            "products": [
                {
                    "canonical_id": "demo--buds",
                    "brand": "Demo",
                    "model": "Buds",
                    "category": "耳夹式耳机",
                    "category_status": "accepted",
                    "report_count": 1,
                    "video_count": 0,
                    "private": "left",
                }
            ]
        }
        right = {
            "products": [
                {
                    "canonical_id": "demo--buds",
                    "brand": "Demo",
                    "model": "Buds",
                    "category": "耳夹式耳机",
                    "category_status": "accepted",
                    "report_count": 1,
                    "video_count": 0,
                    "private": "right",
                }
            ]
        }
        self.assertEqual(catalog_projection(left), catalog_projection(right))


if __name__ == "__main__":
    unittest.main()
