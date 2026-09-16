from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts import enrich_commerce


class PriceEnrichmentTests(unittest.TestCase):
    def test_official_price_skips_third_party_search(self) -> None:
        official = {"msrp_cny": 1299, "official_url": "https://brand.example/pods"}
        with patch.object(
            enrich_commerce, "_load_product", return_value={"brand": "Acme", "model": "Pods X"}
        ), patch.object(enrich_commerce, "is_identity_searchable", return_value=True), patch.object(
            enrich_commerce, "identity_review_reason", return_value=""
        ), patch.object(enrich_commerce, "enrich_official", return_value=official), patch.object(
            enrich_commerce, "enrich_channel"
        ) as channel_search, patch.object(enrich_commerce, "write_enrich"):
            result = enrich_commerce.enrich_one("acme--pods-x", {})

        channel_search.assert_not_called()
        self.assertEqual(result["channel"]["price_source"], "official_price_available")
        self.assertEqual(result["official"]["msrp_cny"], 1299)

    def test_missing_official_price_uses_labeled_zol_reference(self) -> None:
        zol = SimpleNamespace(
            product_name="Acme Pods X",
            fetch_error="",
            reference_price_cny=899,
            channel_quotes=[],
            to_dict=lambda: {"reference_price_cny": 899},
        )
        with patch.object(enrich_commerce, "fetch_zol_prices", return_value=zol), patch.object(
            enrich_commerce,
            "best_channel_price",
            return_value=(899, "zol_reference", "https://zol.example/pods-x"),
        ), patch.object(enrich_commerce, "union_configured", return_value=False), patch.object(
            enrich_commerce, "search_jd", return_value=[]
        ):
            result = enrich_commerce.enrich_channel("acme--pods-x", "Acme", "Pods X", {})

        self.assertEqual(result["price_kind"], "reference")
        self.assertEqual(result["price_label"], "中关村在线参考价")
        self.assertIn("仅作参考", result["price_disclaimer"])
        self.assertEqual(result["channel_url"], "https://zol.example/pods-x")


if __name__ == "__main__":
    unittest.main()
