from __future__ import annotations

import unittest

from core.earphone_categories import PENDING_CATEGORY, product_category_fields
from scripts.classify_earphone_categories import direct_record
from scripts.pilot_earphone_category_v2 import legacy_category_of


class EarphoneCategoryTests(unittest.TestCase):
    def test_rerun_uses_legacy_provenance_after_fine_category_build(self) -> None:
        self.assertEqual(
            legacy_category_of(
                {"category": "耳夹式耳机", "category_raw": "开放式耳机"}
            ),
            "开放式耳机",
        )

    def test_legacy_category_is_not_exposed_as_business_category_without_evidence(self) -> None:
        fields = product_category_fields("example--buds", "真无线耳机TWS", {})
        self.assertEqual(fields["category"], PENDING_CATEGORY)
        self.assertEqual(fields["category_raw"], "真无线耳机TWS")
        self.assertEqual(fields["category_status"], "needs_review")

    def test_reviewed_override_keeps_legacy_traceability(self) -> None:
        fields = product_category_fields(
            "example--clip",
            "开放式耳机",
            {
                "example--clip": {
                    "category_raw": "开放式耳机",
                    "category_current": "耳夹式耳机",
                    "status": "accepted",
                    "basis": "llm_direct_evidence",
                    "confidence": "high",
                    "evidence_quote": "采用耳夹式设计",
                    "report_id": "report-1",
                    "classifier_version": "test-v1",
                }
            },
        )
        self.assertEqual(fields["category"], "耳夹式耳机")
        self.assertEqual(fields["category_raw"], "开放式耳机")
        self.assertEqual(fields["category_evidence_quote"], "采用耳夹式设计")

    def test_explicit_source_wording_can_be_applied_without_model(self) -> None:
        source = {
            "product_id": "example--clip",
            "legacy_category": "开放式耳机",
            "report_id": "report-1",
            "source_url": "https://example.test/report-1",
            "segments": [
                {
                    "segment_id": "intro_1",
                    "text": "这款产品采用耳夹式设计，通过 C 形桥固定在耳廓。",
                    "target_match": "true",
                }
            ],
        }
        record = direct_record(source)
        self.assertIsNotNone(record)
        self.assertEqual(record["category_current"], "耳夹式耳机")
        self.assertEqual(record["evidence_segment_id"], "intro_1")


if __name__ == "__main__":
    unittest.main()
