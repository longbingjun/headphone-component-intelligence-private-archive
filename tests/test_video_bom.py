from __future__ import annotations

import unittest

from server.video_bom import structured_rows_from_video_fact


class VideoBomTests(unittest.TestCase):
    def test_rule_first_extraction_keeps_supplier_model_and_parameters(self) -> None:
        rows = structured_rows_from_video_fact(
            {"raw_text": "耳机主控采用JL杰理科技AC7106F6蓝牙音频SoC"}
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["component_key"], "bluetooth_audio_soc")
        self.assertIn("JL", rows[0]["brand"])
        self.assertIn("AC7106F6", rows[0]["model"])

    def test_model_fields_must_exist_in_source_subtitle(self) -> None:
        rows = structured_rows_from_video_fact(
            {
                "raw_text": "充电盒电池容量500mAh",
                "bom_items": [
                    {
                        "component": "电池",
                        "manufacturer": "虚构电池厂",
                        "model": "FAKE-500",
                        "side": "充电盒",
                        "parameters": [
                            {"label": "容量", "value": "500mAh"},
                            {"label": "电压", "value": "3.85V"},
                        ],
                    }
                ],
            }
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["brand"], "")
        self.assertNotEqual(rows[0]["model"], "FAKE-500")
        self.assertEqual(
            {(item["label"], item["value"]) for item in rows[0]["parameters"]},
            {("容量", "500mAh")},
        )

    def test_rule_fallback_keeps_video_chip_identity_when_model_batch_fails(self) -> None:
        cases = (
            ("这是思佳讯AV6351无线音频传输IC", "wireless_audio_ic", "思佳讯", "AV6351"),
            ("这是旭化成AK4961音频芯片", "audio_codec_dsp", "旭化成", "AK4961"),
            ("——以及恩智浦LPC804MCU", "mcu", "恩智浦", "LPC804"),
            ("微源LP5305过压过流保护IC等", "battery_protection_ic", "微源", "LP5305"),
            ("这是丝印P24C512的存储器", "memory", "", "P24C512"),
            ("这是丝印P24C512的储存器", "memory", "", "P24C512"),
        )
        for text, key, manufacturer, model in cases:
            with self.subTest(text=text):
                rows = structured_rows_from_video_fact({"raw_text": text})
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["component_key"], key)
                self.assertIn(manufacturer, rows[0]["brand"])
                self.assertEqual(rows[0]["model"], model)

    def test_generic_component_word_is_not_published_as_model(self) -> None:
        rows = structured_rows_from_video_fact({"raw_text": "这是驻极体麦克风"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["component_key"], "microphone")
        self.assertEqual(rows[0]["model"], "")

    def test_rule_model_must_be_verbatim(self) -> None:
        rows = structured_rows_from_video_fact(
            {"raw_text": "右耳电池型号：LIP1522，额定容量1000mAh"}
        )
        self.assertTrue(rows)
        self.assertNotIn("IP1522", {row.get("model") for row in rows})

    def test_caption_block_uses_first_component_anchor(self) -> None:
        rows = structured_rows_from_video_fact(
            {
                "raw_text": "这是旭化成AK4961音频芯片",
                "bom_context_text": "这是旭化成AK4961音频芯片；内置麦克风放大器",
            }
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["component_key"], "audio_codec_dsp")
        self.assertEqual(rows[0]["brand"], "旭化成")
        self.assertEqual(rows[0]["model"], "AK4961")

    def test_ocr_chinese_units_remain_source_backed_parameters(self) -> None:
        rows = structured_rows_from_video_fact(
            {
                "raw_text": "耳机内置软包扣式电池型号PATL1254B",
                "bom_context_text": (
                    "耳机内置软包扣式电池型号PATL1254B；"
                    "标称电压3.85伏额定容量70毫安时0.27瓦时；来自小锂新能源"
                ),
            }
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["component_key"], "battery")
        self.assertEqual(rows[0]["model"], "PATL1254B")
        self.assertIn("小锂新能源", rows[0]["brand"])
        self.assertEqual(
            {(item["label"], item["value"]) for item in rows[0]["parameters"]},
            {("电压", "3.85伏"), ("容量", "70毫安时"), ("能量", "0.27瓦时")},
        )

    def test_labelled_numeric_battery_model_is_preserved(self) -> None:
        rows = structured_rows_from_video_fact(
            {"raw_text": "充电盒内置可充电锂离子电池型号：751641"}
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["component_key"], "battery")
        self.assertEqual(rows[0]["model"], "751641")

    def test_compact_power_vendor_supports_soc_and_programmable_boost_ic(self) -> None:
        cases = (
            ("昇生微电子SS88F8H电源管理SoC", "SS88F8H"),
            ("昇生微电子SSD101X可编程升压IC", "SSD101X"),
        )
        for source, model in cases:
            with self.subTest(source=source):
                rows = structured_rows_from_video_fact({"raw_text": source})
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["component_key"], "power_management_ic")
                self.assertEqual(rows[0]["brand"], "昇生微电子")
                self.assertEqual(rows[0]["model"], model)

    def test_unlabelled_ocr_watts_are_not_misclassified_as_battery_power(self) -> None:
        rows = structured_rows_from_video_fact(
            {
                "raw_text": "耳机内置电池型号PATL1254B",
                "bom_context_text": "耳机内置电池型号PATL1254B；额定容量70毫安时0.27瓦",
            }
        )
        self.assertNotIn("0.27瓦", {p["value"] for p in rows[0]["parameters"]})

    def test_labelled_chinese_power_is_preserved(self) -> None:
        rows = structured_rows_from_video_fact(
            {"raw_text": "扬声器额定功率20毫瓦，最大短时功率50毫瓦"}
        )
        self.assertEqual(
            {p["value"] for p in rows[0]["parameters"]},
            {"20毫瓦", "50毫瓦"},
        )

    def test_dimension_is_a_parameter_not_a_component_model(self) -> None:
        rows = structured_rows_from_video_fact(
            {"raw_text": "扬声器是一款尺寸为17x12的动圈单元"}
        )
        self.assertEqual(rows[0]["model"], "")
        self.assertEqual(
            {(p["label"], p["value"]) for p in rows[0]["parameters"]},
            {("尺寸", "17x12")},
        )

    def test_narrative_side_is_used_only_as_explicit_source_provenance(self) -> None:
        rows = structured_rows_from_video_fact(
            {
                "raw_text": "昇生微电子SS88F8H电源管理SoC",
                "narrative_side": "充电盒",
                "narrative_side_evidence": "拆解充电盒取掉底部外壳",
            }
        )
        self.assertEqual(rows[0]["side"], "充电盒")


if __name__ == "__main__":
    unittest.main()
