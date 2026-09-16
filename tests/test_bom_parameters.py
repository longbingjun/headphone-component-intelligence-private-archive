from __future__ import annotations

import unittest

from core.extract.bom_parameters import bom_row_key, deterministic_candidates, validate_bom_parameters


class BomParameterTests(unittest.TestCase):
    def test_semantic_labels_survive_and_unsupported_values_are_rejected(self) -> None:
        row = {
            "role": "core",
            "component": "微控制器",
            "model": "GD32E113CBT6",
            "side": "充电盒",
            "fact_text": "基于Arm Cortex-M4 RISC内核，最高主频为120MHz，提供高达128KB的片上闪存和32KB SRAM存储器。",
        }
        key = bom_row_key(row)
        candidates = {"items": [{"key": key, "parameters": [
            {"label": "最高主频", "value": "120MHz", "evidence_quote": row["fact_text"]},
            {"label": "片上闪存", "value": "256KB", "evidence_quote": row["fact_text"]},
        ]}]}
        accepted, rejected = validate_bom_parameters(candidates, [row])
        self.assertEqual(accepted[key][0]["display"], "最高主频：120MHz")
        self.assertIn("value_not_in_evidence", {item["reason"] for item in rejected})

        fallback, _ = validate_bom_parameters(deterministic_candidates([row]), [row])
        displays = {item["display"] for item in fallback[key]}
        self.assertIn("处理器内核：Arm Cortex-M4 RISC内核", displays)
        self.assertIn("片上闪存：128KB", displays)
        self.assertIn("SRAM：32KB", displays)

    def test_aggregated_evidence_texts_remain_parameter_sources(self) -> None:
        row = {
            "role": "core",
            "component": "微控制器",
            "model": "GD32E113CBT6",
            "side": "充电盒",
            "fact_text": "GD32E113CBT6微控制器特写。",
            "evidence_texts": ["GD32E113xx系列最高主频为120MHz。"],
        }
        key = bom_row_key(row)
        fallback, rejected = validate_bom_parameters(deterministic_candidates([row]), [row])
        self.assertEqual(rejected, [])
        self.assertIn("最高主频：120MHz", {item["display"] for item in fallback[key]})


if __name__ == "__main__":
    unittest.main()
