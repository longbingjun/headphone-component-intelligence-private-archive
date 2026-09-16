from __future__ import annotations

import unittest

from core.bom_taxonomy import component_key
from core.component_analytics import (
    brand_supply_key,
    brand_supply_manifest,
    brand_supply_payload,
    canonical_manufacturer,
    manufacturer_is_rejected,
    component_evidence_supported,
    component_payload,
    component_fact_key,
    manufacturer_from_evidence,
    normalized_component_model,
    rows_from_products,
    structural_features,
    supplier_supply_key,
    supplier_supply_manifest,
    supplier_supply_payload,
    validated_manufacturer_enrichment,
)


class ComponentAnalyticsTests(unittest.TestCase):
    def test_battery_model_metrics_exclude_parameter_text_and_merge_safe_prefix_spacing(self) -> None:
        self.assertEqual(
            normalized_component_model(
                "58mAh锂电池", component="battery", manufacturer="紫建电子"
            ),
            ("58mAh锂电池", "", "invalid_parameter_value"),
        )
        self.assertEqual(
            normalized_component_model(
                "3.86V/60mAh钢壳扣式", component="battery", manufacturer=""
            ),
            ("3.86V/60mAh钢壳扣式", "", "invalid_parameter_value"),
        )
        self.assertEqual(
            normalized_component_model(
                "MEMS麦克风", component="microphone", manufacturer=""
            ),
            ("MEMS麦克风", "", "invalid_parameter_value"),
        )
        self.assertEqual(
            normalized_component_model(
                "VDL 672040PN3A", component="battery", manufacturer="紫建电子"
            ),
            ("VDL 672040PN3A", "672040pn3a", "reported"),
        )
        self.assertEqual(
            normalized_component_model(
                "672040PN3A", component="battery", manufacturer="紫建电子"
            ),
            ("672040PN3A", "672040pn3a", "reported"),
        )
        # No separator means the VDL prefix remains part of the reported model.
        self.assertEqual(
            normalized_component_model(
                "VDL1150M2", component="battery", manufacturer="紫建电子"
            )[1],
            "vdl1150m2",
        )

    def test_battery_protection_ic_is_not_counted_as_battery(self) -> None:
        self.assertEqual(component_key("电池", "内置锂离子电池"), "battery")
        self.assertEqual(component_key("电池保护IC", "负责过充保护"), "battery_protection_ic")

    def test_manufacturer_requires_report_wording_or_reported_brand(self) -> None:
        self.assertEqual(
            manufacturer_from_evidence(
                "内置锂离子电池组，额定容量500mAh，生产厂：重庆市紫建电子股份有限公司。",
                "",
                component_key="battery",
            ),
            ("重庆市紫建电子股份有限公司", "explicit_source_text", "生产厂：重庆市紫建电子股份有限公司"),
        )
        name, basis, quote = manufacturer_from_evidence(
            "耳机内置扣式电池，额定容量54mAh，来自ZeniPower至力。",
            "",
            component_key="battery",
        )
        self.assertEqual(name, "ZeniPower至力")
        self.assertEqual(basis, "explicit_source_text")
        self.assertIn("来自ZeniPower至力", quote)
        self.assertEqual(
            manufacturer_from_evidence("Acme Battery型号A1。", "Acme Battery")[:2],
            ("Acme Battery", "reported_component_brand"),
        )
        self.assertEqual(
            manufacturer_from_evidence("型号A1。", "Finished Product Brand")[:2],
            ("", "unknown"),
        )
        self.assertEqual(manufacturer_from_evidence("型号A1。")[:2], ("", "unknown"))
        self.assertEqual(canonical_manufacturer("VDL紫建电子"), "紫建电子")
        self.assertEqual(canonical_manufacturer("重庆市紫建电子股份有限公司"), "紫建电子")
        self.assertEqual(canonical_manufacturer("微源"), "微源半导体")
        self.assertEqual(canonical_manufacturer("金宇宙"), "JYZ金宇宙")
        self.assertEqual(canonical_manufacturer("JYZ 金宇宙能源有限公司"), "JYZ金宇宙")
        self.assertEqual(canonical_manufacturer("金宇宙的30mAh软包电池"), "JYZ金宇宙")
        self.assertEqual(
            canonical_manufacturer("“KRCONN”深圳精睿兴业科技有限公司"),
            "KRCONN深圳精睿兴业科技有限公司",
        )
        self.assertEqual(canonical_manufacturer("敏芯微"), "敏芯微电子")
        self.assertTrue(manufacturer_is_rejected("用于语音通话功能拾音"))
        self.assertEqual(canonical_manufacturer("用于语音通话功能拾音"), "")
        self.assertEqual(
            canonical_manufacturer("东莞市锂宇能源有限公司（深圳市金宇宙能源有限公司旗下子公司）"),
            "东莞市锂宇能源有限公司（深圳市金宇宙能源有限公司旗下子公司）",
        )
        self.assertEqual(
            canonical_manufacturer("苏州赛芯电子科技股份有限公司"),
            "苏州赛芯电子科技",
        )
        self.assertEqual(canonical_manufacturer("来远电子"), "来远电子")

    def test_compact_chip_vendor_model_wording_is_source_grounded(self) -> None:
        self.assertEqual(
            manufacturer_from_evidence(
                "JL杰理科技AC7106F6蓝牙音频SoC，支持蓝牙6.0，用于无线连接和音频数据处理。",
                component_key="bluetooth_audio_soc",
            ),
            (
                "JL杰理科技",
                "compact_source_component_wording",
                "JL杰理科技AC7106F6蓝牙音频SoC",
            ),
        )
        self.assertEqual(
            manufacturer_from_evidence(
                "BES恒玄科技BES2700Y蓝牙音频SoC，用于无线连接和音频数据处理。",
                component_key="bluetooth_audio_soc",
            )[:2],
            ("BES恒玄科技", "compact_source_component_wording"),
        )
        self.assertEqual(
            manufacturer_from_evidence(
                "耳机搭载WUQI物奇WQ7035AX-B蓝牙音频SoC。",
                component_key="bluetooth_audio_soc",
            )[:2],
            ("WUQI物奇", "compact_source_component_wording"),
        )
        self.assertEqual(
            manufacturer_from_evidence(
                "主控芯片为Qualcomm高通QCC3044，是一款基于极低功耗架构的蓝牙音频SoC。",
                component_key="bluetooth_audio_soc",
            )[:2],
            ("Qualcomm高通", "compact_source_component_wording"),
        )
        self.assertEqual(
            canonical_manufacturer("JL杰理"),
            "JL杰理科技",
        )

    def test_compact_vendor_rule_rejects_ambiguous_summary_clause(self) -> None:
        self.assertEqual(
            manufacturer_from_evidence(
                "同系列产品分别采用高通QCC3091蓝牙音频SoC和恒玄BES2700Y蓝牙音频SoC。",
                component_key="bluetooth_audio_soc",
            )[:2],
            ("", "unknown"),
        )

    def test_llm_manufacturer_fallback_requires_verbatim_evidence(self) -> None:
        text = "耳机主板搭载Acme Semiconductor AX100音频芯片。"
        accepted = validated_manufacturer_enrichment(
            text,
            {
                "manufacturer": "Acme Semiconductor",
                "evidence_quote": "Acme Semiconductor AX100音频芯片",
            },
        )
        self.assertEqual(
            accepted,
            (
                "Acme Semiconductor",
                "llm_source_extraction_validated",
                "Acme Semiconductor AX100音频芯片",
            ),
        )
        self.assertIsNone(
            validated_manufacturer_enrichment(
                text,
                {"manufacturer": "Invented Corp", "evidence_quote": text},
            )
        )
        self.assertEqual(component_fact_key({"component": "芯片", "fact_text": text}), component_fact_key({"component": "芯片", "fact_text": text}))

    def test_battery_quality_gate_rejects_ic_and_product_manufacturer(self) -> None:
        self.assertFalse(
            component_evidence_supported(
                "battery", "电池", "锂电池充电IC，型号AW32001，来自艾为电子。"
            )
        )
        self.assertTrue(
            component_evidence_supported(
                "battery", "电池", "耳机内置钢壳扣式电池，额定容量54mAh。"
            )
        )
        self.assertEqual(
            manufacturer_from_evidence(
                "盒盖内侧产品信息，电池容量350mAh，耳机型号ETI11，制造商是OPPO。",
                component_key="battery",
            )[:2],
            ("", "unknown"),
        )
        self.assertEqual(
            manufacturer_from_evidence(
                "充电盒底部产品信息，产品名10.or Buds，耳机电池容量50mAh，充电盒电池容量550mAh，制造商亚马逊卓越有限公司。",
                component_key="battery",
            )[:2],
            ("", "unknown"),
        )
        self.assertEqual(
            manufacturer_from_evidence(
                "耳机内置锂离子电池组，额定容量1000mAh，生产厂家：东莞市盈达通电子科技有限公司。",
                component_key="battery",
            )[0],
            "东莞市盈达通电子科技有限公司",
        )
        self.assertEqual(
            manufacturer_from_evidence(
                "耳机采用钢壳扣式电池，额定容量50mAh。",
                component_key="battery",
            )[:2],
            ("", "unknown"),
        )
        self.assertEqual(
            manufacturer_from_evidence(
                "耳机采用VARTA的扣式电池，额定容量50mAh。",
                component_key="battery",
            )[0],
            "VARTA",
        )

    def test_payload_uses_report_date_and_has_no_extraction_time_field(self) -> None:
        products = [
            {
                "canonical_id": "sony--demo",
                "brand": "SONY索尼",
                "model": "Demo",
                "category": "开放式耳机",
                "report_ids": ["r1"],
                "technical_facts": [
                    {
                        "component": "电池",
                        "side": "左耳机",
                        "model": "ZP Z35FH",
                        "fact_text": "耳机电池型号ZP Z35FH，额定容量54mAh，来自ZeniPower至力。",
                        "parameters": [
                            {"label": "额定容量", "value": "54mAh", "evidence_quote": "额定容量54mAh"}
                        ],
                        "evidence_images": [
                            {
                                "index": 12,
                                "url": "https://example.test/battery.jpg",
                                "caption": "电池标签特写",
                            }
                        ],
                    },
                    {
                        "component": "电池",
                        "side": "右耳机",
                        "model": "ZP Z35FH",
                        "fact_text": "耳机电池型号ZP Z35FH，额定容量54mAh，来自ZeniPower至力。",
                        "parameters": [
                            {"label": "额定容量", "value": "54mAh", "evidence_quote": "额定容量54mAh"}
                        ],
                    },
                ],
            }
        ]
        reports = {
            "r1": {
                "id": "r1",
                "title": "拆解报告",
                "url": "https://example.test/r1",
                "published_at": "2026-06-25",
            }
        }
        rows = rows_from_products(products, reports)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["usage_location"], "earbud")
        self.assertEqual(rows[0]["component_model_normalized"], "zpz35fh")
        self.assertEqual(rows[0]["model_status"], "reported")
        self.assertEqual(rows[0]["product_brand_status"], "resolved")
        self.assertEqual(rows[0]["source_published_at"], "2026-06-25")
        self.assertEqual(rows[0]["evidence_image"]["url"], "https://example.test/battery.jpg")
        self.assertEqual(rows[0]["evidence_image"]["caption"], "电池标签特写")
        self.assertNotIn("extracted_at", rows[0])
        payload = component_payload(rows, "battery")
        self.assertEqual(payload["summary"]["products"], 1)
        self.assertEqual(payload["summary"]["manufacturer_coverage"], 1.0)
        self.assertEqual(payload["summary"]["model_known_products"], 1)
        self.assertEqual(payload["summary"]["parameterized_products"], 1)
        self.assertNotIn("generated_at", payload)

        brand_manifest = brand_supply_manifest(rows)
        self.assertEqual(brand_manifest["brands"][0]["brand"], "SONY索尼")
        self.assertEqual(brand_manifest["brands"][0]["products"], 1)
        self.assertTrue(brand_manifest["brands"][0]["key"].startswith("brand-"))
        brand_payload = brand_supply_payload(rows, "SONY索尼")
        self.assertEqual(brand_payload["summary"]["products"], 1)
        self.assertEqual(brand_payload["summary"]["components"], 1)
        self.assertEqual(brand_payload["summary"]["suppliers"], 1)
        self.assertEqual(brand_payload["brand_key"], brand_supply_key("SONY索尼"))
        self.assertEqual(brand_payload["rows"][0]["evidence_image"]["url"], "https://example.test/battery.jpg")

        supplier_manifest = supplier_supply_manifest(rows)
        expected_supplier = rows[0]["component_manufacturer_canonical"]
        self.assertTrue(expected_supplier.startswith("ZeniPower"))
        self.assertEqual(supplier_manifest["suppliers"][0]["supplier"], expected_supplier)
        self.assertEqual(supplier_manifest["suppliers"][0]["products"], 1)
        self.assertEqual(supplier_manifest["suppliers"][0]["components"], 1)
        supplier_payload = supplier_supply_payload(rows, expected_supplier)
        self.assertEqual(supplier_payload["summary"]["products"], 1)
        self.assertEqual(supplier_payload["summary"]["components"], 1)
        self.assertEqual(supplier_payload["supplier_key"], supplier_supply_key(expected_supplier))
        self.assertEqual(supplier_payload["rows"][0]["component_model"], "ZP Z35FH")

    def test_structural_features_are_verbatim_and_do_not_infer_material(self) -> None:
        text = "前腔内部通过透明支架和螺丝固定电池和主板单元；"
        features = structural_features("structural_support", "固定支架", text)
        values = {(item["label"], item["value"]) for item in features}
        self.assertIn(("详细位置", "前腔内部"), values)
        self.assertIn(("结构形态", "透明支架"), values)
        self.assertIn(("固定/承载对象", "电池"), values)
        self.assertIn(("固定/承载对象", "主板单元"), values)
        self.assertIn(("固定/密封方式", "螺丝固定"), values)
        self.assertNotIn("明确材料", {item["label"] for item in features})
        self.assertTrue(all(item["value"] in text for item in features))
        unrelated = structural_features(
            "structural_support",
            "固定支架",
            "卸掉螺丝，拆掉电池支架，电池导线与主板焊接。",
        )
        unrelated_targets = {
            item["value"] for item in unrelated if item["label"] == "固定/承载对象"
        }
        self.assertEqual(unrelated_targets, {"电池"})

    def test_structural_payload_uses_structure_view_and_disclosure_status(self) -> None:
        products = [{
            "canonical_id": "sony--demo-structure",
            "brand": "SONY索尼",
            "model": "Demo Structure",
            "category": "开放式耳机",
            "report_ids": ["r2"],
            "technical_facts": [{
                "component": "固定支架",
                "side": "耳机",
                "fact_text": "前腔内部通过透明支架固定电池和主板单元；",
            }],
        }]
        reports = {"r2": {"id": "r2", "published_at": "2026-06-25"}}
        rows = rows_from_products(products, reports)
        self.assertEqual(rows[0]["manufacturer_status"], "not_disclosed")
        self.assertEqual(rows[0]["model_status"], "not_disclosed")
        self.assertEqual(rows[0]["detail_status"], "source_structured")
        payload = component_payload(rows, "structural_support")
        self.assertEqual(payload["view_mode"], "structural")
        self.assertEqual(payload["summary"]["feature_coverage"], 1.0)

    def test_video_only_fact_is_excluded_from_report_evidence_analysis(self) -> None:
        products = [{
            "canonical_id": "demo--video-only",
            "brand": "Demo",
            "model": "Video Only",
            "category": "开放式耳机",
            "report_ids": [],
            "technical_facts": [{
                "component": "麦克风",
                "fact_text": "搭载歌尔声学降噪麦克风。",
                "evidence": {"text": "搭载歌尔声学降噪麦克风。"},
            }],
        }]
        self.assertEqual(rows_from_products(products, {}), [])


if __name__ == "__main__":
    unittest.main()
