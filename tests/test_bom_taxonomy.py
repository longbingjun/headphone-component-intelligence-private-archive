from __future__ import annotations

import unittest

from core.bom_taxonomy import component_key, material_hint, numeric_value


class BomTaxonomyTests(unittest.TestCase):
    def test_explicit_component_wins_over_unrelated_summary_words(self) -> None:
        summary = "采用Type-C接口，蓝牙SoC为QCC3008，并配备1300mAh电池。"
        self.assertEqual(component_key("Type-C接口", summary), "connector")
        self.assertEqual(component_key("蓝牙音频SoC", summary), "bluetooth_audio_soc")
        self.assertEqual(component_key("音频编解码器/DSP", "型号为CS47L90"), "audio_codec_dsp")
        self.assertEqual(component_key("芯片/模组", "型号为ABC123"), "generic_chip_module")
        self.assertEqual(component_key("喇叭单元", "一个动圈发声单元"), "speaker_driver")

    def test_long_article_summary_does_not_leak_material(self) -> None:
        summary = "产品使用塑料壳体。" + "完整拆解总结。" * 100
        self.assertEqual(material_hint([], summary), "")
        self.assertEqual(
            material_hint(
                [{"label": "材料", "value": "铝合金", "evidence_quote": summary}],
                summary,
                "支架",
            ),
            "",
        )
        self.assertEqual(
            material_hint(
                [{"label": "材料", "value": "铝合金", "evidence_quote": "支架采用铝合金材料"}],
                summary,
                "支架",
            ),
            "铝合金",
        )
        self.assertEqual(
            material_hint(
                [{"label": "材料", "value": "硅胶", "evidence_quote": "耳罩内侧是硅胶材质"}],
                summary,
                "电池",
            ),
            "",
        )

    def test_pc_device_name_is_not_treated_as_polycarbonate(self) -> None:
        self.assertEqual(material_hint([], "支持连接PC/Mac、Switch多种设备。", "外壳结构"), "")
        self.assertEqual(material_hint([], "外壳采用PC材质。", "外壳结构"), "PC")

    def test_nearby_protector_material_does_not_leak_to_driver(self) -> None:
        text = "扬声器单元外有塑料保护罩。"
        self.assertEqual(material_hint([], text, "喇叭单元"), "")
        self.assertEqual(material_hint([], "塑料外壳上开孔。", "外壳结构"), "塑料")
        self.assertEqual(material_hint([], "采用贴片陶瓷蓝牙天线。", "天线"), "陶瓷")

    def test_simple_numeric_parameter_is_queryable(self) -> None:
        self.assertEqual(numeric_value("120MHz"), (120.0, "MHz"))
        self.assertEqual(numeric_value("用于蓝牙配对"), (None, ""))


if __name__ == "__main__":
    unittest.main()
