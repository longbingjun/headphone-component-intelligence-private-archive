from __future__ import annotations

import unittest

from core.extract.unboxing_summary import validate_unboxing_summary


class UnboxingSummaryTests(unittest.TestCase):
    def test_requires_module_evidence_and_rejects_unsupported_numbers(self) -> None:
        source = {
            "packaging": ["包装盒标注9h+28h续航，并附产品参考指南。"],
            "charging_case": ["充电盒背面设置Type-C接口和配对按键。"],
            "earbuds": ["单只耳机重量约为6.1g。"],
        }
        candidates = {
            "packaging": [
                {"text": "包装标注综合37小时续航", "evidence_quote": source["packaging"][0]},
                {"text": "包装附产品参考指南", "evidence_quote": source["packaging"][0]},
            ],
            "charging_case": [
                {"text": "背面设Type-C接口和配对按键", "evidence_quote": source["charging_case"][0]},
            ],
            "earbuds": [
                {"text": "主板芯片位于屏蔽罩下", "evidence_quote": source["earbuds"][0]},
            ],
        }
        accepted, rejected = validate_unboxing_summary(candidates, source)
        self.assertEqual(len(accepted["packaging"]), 1)
        self.assertEqual(len(accepted["charging_case"]), 1)
        self.assertFalse(accepted["earbuds"])
        self.assertIn("unsupported_number", {row["reason"] for row in rejected})
        self.assertIn("teardown_fact_in_appearance_summary", {row["reason"] for row in rejected})


if __name__ == "__main__":
    unittest.main()
