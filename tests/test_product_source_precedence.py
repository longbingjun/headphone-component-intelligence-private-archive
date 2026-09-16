from __future__ import annotations

import unittest

from core.extract.consumer_claims import is_publishable_consumer_claim, validate_consumer_claims
from core.products import _aggregate_bom_facts, merge_cost_snapshot, merge_market_snapshot


class ProductSourcePrecedenceTests(unittest.TestCase):
    def test_component_fact_aggregation_keeps_specific_model_and_merges_parameters(self) -> None:
        rows = [
            {
                "component": "微控制器",
                "model": "GD32E113CBT6",
                "side": "充电盒",
                "fact_text": "芯片特写。",
                "parameters": [{"label": "型号", "value": "GD32E113CBT6"}],
            },
            {
                "component": "微控制器",
                "model": "GD32E113xx",
                "side": "充电盒",
                "fact_text": "系列支持120MHz。",
                "parameters": [{"label": "最高主频", "value": "120MHz"}],
            },
        ]
        aggregated = _aggregate_bom_facts(rows)
        self.assertEqual(len(aggregated), 1)
        self.assertEqual(aggregated[0]["model"], "GD32E113CBT6")
        self.assertEqual(len(aggregated[0]["parameters"]), 2)
        self.assertEqual(len(aggregated[0]["evidence_texts"]), 2)

    def test_report_market_wins_even_when_video_has_more_facts(self) -> None:
        report = {
            "id": "r1",
            "content_html": "<p>耳机采用亲肤硅胶结构，提供舒适稳固的佩戴体验。</p><h4>二、耳机拆解</h4>",
            "views": {"market": {"selling_points": [{"text": "报告卖点"}]}, "cost": {}},
        }
        video = {
            "id": "v1",
            "views": {"market": {"selling_points": [{"text": f"视频事实{i}"} for i in range(12)]}},
        }
        result = merge_market_snapshot(
            canonical_id="test--source-precedence",
            report_ids=["r1"],
            video_ids=["v1"],
            reports_by_id={"r1": report},
            videos_by_id={"v1": video},
        )
        self.assertEqual(result["best_report_id"], "r1")
        self.assertIsNone(result["best_video_id"])
        self.assertTrue(result["consumer_claims"])

    def test_report_cost_wins_and_unknown_marking_is_review_only(self) -> None:
        recognized = {"component": "微控制器", "model": "ABC123", "role": "core"}
        unknown = {"component": "待识别器件", "model": "5VAr", "role": "unidentified_marking"}
        report = {
            "id": "r1",
            "views": {"market": {}, "structure": {}, "cost": {"bom_table": [recognized, unknown], "teardown_inventory": [recognized, unknown]}},
        }
        video_rows = [{"component": f"视频器件{i}", "model": f"V{i}", "role": "core"} for i in range(10)]
        video = {"id": "v1", "views": {"cost": {"bom_table": video_rows, "teardown_inventory": video_rows}}}
        result = merge_cost_snapshot(
            canonical_id="test--source-precedence",
            report_ids=["r1"],
            video_ids=["v1"],
            reports_by_id={"r1": report},
            videos_by_id={"v1": video},
        )
        self.assertEqual(result["cost_snapshot"]["best_report_id"], "r1")
        self.assertIsNone(result["cost_snapshot"]["best_video_id"])
        self.assertEqual(result["cost_snapshot"]["bom_row_count"], 1)
        self.assertEqual(result["technical_facts"], [recognized])
        self.assertEqual(result["data_quality_review_queue"], [unknown])

    def test_code_validator_rejects_technical_only_and_unsupported_numbers(self) -> None:
        paragraphs = ["耳机采用亲肤硅胶结构，提供舒适稳固的佩戴体验。"]
        candidates = [
            {
                "text": "采用GD32E113CBT6微控制器",
                "category": "佩戴体验",
                "evidence_quote": paragraphs[0],
                "confidence": 0.9,
            },
            {
                "text": "提供20小时舒适佩戴",
                "category": "佩戴体验",
                "evidence_quote": paragraphs[0],
                "confidence": 0.9,
            },
        ]
        accepted, rejected = validate_consumer_claims(candidates, paragraphs, [])
        self.assertEqual(accepted, [])
        self.assertEqual({item["reason"] for item in rejected}, {"pure_technical_fact", "unsupported_number"})

    def test_cached_consumer_claim_publication_gate_rejects_packaging_and_bom(self) -> None:
        self.assertFalse(is_publishable_consumer_claim("包装盒背面印有公司信息", "外观设计"))
        self.assertFalse(is_publishable_consumer_claim("采用GD32微控制器", "连接与智能"))
        self.assertTrue(is_publishable_consumer_claim("半入耳设计带来轻巧舒适佩戴", "佩戴体验"))

    def test_sparse_fine_inventory_keeps_unseen_summary_bom(self) -> None:
        detailed = {"component": "蓝牙音频SoC", "model": "CSR8675", "role": "core"}
        summary_duplicate = {
            "component": "芯片/模组",
            "model": "CSR8675",
            "role": "core",
            "evidence": {"source_text": "主控芯片为高通CSR8675。"},
        }
        summary_battery = {
            "component": "电池",
            "model": "1200mAh",
            "side": "耳机",
            "role": "PMIC/充电仓管理",
            "evidence": {"source_text": "耳机内置电池容量1200mAh。"},
        }
        noisy_capability = {
            "component": "降噪系统",
            "model": "",
            "role": "core",
            "evidence": {"source_text": "产品支持主动降噪，但这里描述的是功能，不是一个物理 BOM 器件。"},
        }
        packaging_shell = {
            "component": "外壳结构",
            "model": "",
            "role": "structure",
            "evidence": {"source_text": "包装盒外壳为灰色，印有产品图片。"},
        }
        duplicate_driver_alias = {
            "component": "喇叭单元",
            "model": "",
            "role": "core",
            "evidence": {"source_text": "首先看到的是一个动圈单元。"},
        }
        detailed_driver = {
            "component": "动圈单元",
            "model": "",
            "role": "core",
            "fact_text": "首先看到的是一个动圈单元。",
        }
        detailed_case_battery = {
            "component": "电池",
            "model": "772130",
            "side": "充电盒",
            "role": "core",
            "fact_text": "充电盒电池型号772130。",
        }
        duplicate_case_battery = {
            "component": "电池",
            "model": "3.85V/500mAh锂电池",
            "side": "充电盒",
            "role": "core",
            "evidence": {"source_text": "充电盒内置3.85V/500mAh锂电池。"},
        }
        duplicate_soc_family = {
            "component": "芯片/模组",
            "model": "CSR8675A",
            "side": "",
            "role": "core",
            "evidence": {"source_text": "主控系列为CSR8675A。"},
        }
        report = {
            "id": "r1",
            "views": {
                "market": {},
                "structure": {},
                "cost": {
                    "teardown_inventory": [detailed, detailed_driver, detailed_case_battery],
                    "bom_table": [summary_duplicate, summary_battery, noisy_capability, packaging_shell, duplicate_driver_alias, duplicate_case_battery, duplicate_soc_family],
                },
            },
        }
        result = merge_cost_snapshot(
            canonical_id="test--sparse-inventory",
            report_ids=["r1"],
            video_ids=[],
            reports_by_id={"r1": report},
        )
        self.assertEqual(len(result["technical_facts"]), 4)
        batteries = [row for row in result["technical_facts"] if row["component"] == "电池"]
        self.assertEqual(len(batteries), 2)
        summary_only = next(row for row in batteries if row.get("side") == "耳机")
        self.assertEqual(summary_only["fact_text"], "耳机内置电池容量1200mAh。")
        self.assertEqual(summary_only["classification"], "core")
        self.assertNotIn("降噪系统", {row["component"] for row in result["technical_facts"]})
        self.assertNotIn("外壳结构", {row["component"] for row in result["technical_facts"]})
        self.assertNotIn("喇叭单元", {row["component"] for row in result["technical_facts"]})
        self.assertNotIn("CSR8675A", {row.get("model") for row in result["technical_facts"]})


if __name__ == "__main__":
    unittest.main()
