from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.build_products import _identity_candidates
from scripts.repair_product_identities import model_config, resolve_title_batch, validate


class ProductIdentityRepairTests(unittest.TestCase):
    def test_title_identity_requires_verbatim_brand_model_and_quote(self) -> None:
        valid = validate(
            {
                "decision": "single_headphone",
                "products": [
                    {
                        "brand": "HYUNDAI",
                        "model": "HY-T19",
                        "category": "开放式耳机",
                        "confidence": 0.98,
                        "evidence_quote": "HYUNDAI现代HY-T19开放式耳机",
                    }
                ],
            },
            "HYUNDAI现代HY-T19开放式耳机",
        )
        self.assertEqual(valid["decision"], "single_headphone")
        self.assertEqual(valid["products"][0]["brand"], "HYUNDAI")

        hallucinated_brand = validate(
            {
                "decision": "single_headphone",
                "products": [
                    {
                        "brand": "Samsung",
                        "model": "Galaxy Buds2",
                        "category": "真无线耳机TWS",
                        "confidence": 0.99,
                        "evidence_quote": "Galaxy Buds2真无线耳机",
                    }
                ],
            },
            "Galaxy Buds2真无线耳机",
        )
        self.assertEqual(hallucinated_brand["decision"], "needs_review")
        self.assertEqual(hallucinated_brand["products"], [])

    def test_model_config_uses_shared_deployment_variables(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "DEFAULT_MODEL_API_KEY": "secret",
                "DEFAULT_MODEL_API_URL": "https://gateway.example/v1/",
                "DEFAULT_MODEL_NAME": "deepseek-v4-flash",
            },
            clear=True,
        ):
            self.assertEqual(
                model_config(),
                (
                    "secret",
                    "https://gateway.example/v1",
                    "deepseek-v4-flash",
                ),
            )

    @patch("scripts.repair_product_identities.deepseek_title_batch")
    def test_title_batch_validates_each_title_independently(self, mocked_batch) -> None:
        mocked_batch.return_value = {
            "report-1": {
                "report_id": "report-1",
                "decision": "single_headphone",
                "reason": "标题同时披露品牌和型号",
                "products": [
                    {
                        "brand": "HYUNDAI",
                        "model": "HY-T19",
                        "category": "开放式耳机",
                        "confidence": 0.98,
                        "evidence_quote": "HYUNDAI现代HY-T19开放式耳机",
                    }
                ],
            },
            "report-2": {
                "report_id": "report-2",
                "decision": "single_headphone",
                "reason": "错误地借用了另一标题中的品牌",
                "products": [
                    {
                        "brand": "HYUNDAI",
                        "model": "Galaxy Buds2",
                        "category": "真无线耳机TWS",
                        "confidence": 0.99,
                        "evidence_quote": "Galaxy Buds2真无线耳机",
                    }
                ],
            },
        }
        records = [
            {"id": "report-1", "title": "HYUNDAI现代HY-T19开放式耳机", "url": "https://example/1"},
            {"id": "report-2", "title": "Galaxy Buds2真无线耳机", "url": "https://example/2"},
        ]

        results = resolve_title_batch("secret", "https://gateway.example/v1", "deepseek-v4-flash", records)

        self.assertEqual(results[0]["decision"], "single_headphone")
        self.assertEqual(results[1]["decision"], "needs_review")
        self.assertTrue(all(item["cacheable"] for item in results))

    def test_product_builder_keeps_normalized_source_category(self) -> None:
        record = {
            "id": "10084",
            "title": "DYPLAY ANC 30 主动降噪耳机",
            "brand": "",
            "model": "DYPLAY ANC 30 主动降噪",
            "category": "真无线耳机TWS",
        }
        overrides = {
            "10084": {
                "decision": "single_headphone",
                "products": [
                    {
                        "brand": "DYPLAY",
                        "model": "ANC 30",
                        "category": "主动降噪耳机",
                        "confidence": 0.9,
                    }
                ],
            }
        }

        self.assertEqual(
            _identity_candidates(record, overrides),
            [("DYPLAY", "ANC 30", "真无线耳机TWS")],
        )


if __name__ == "__main__":
    unittest.main()
