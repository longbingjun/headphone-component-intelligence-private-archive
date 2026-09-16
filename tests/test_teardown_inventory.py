from __future__ import annotations

import unittest

from core.extract.teardown_inventory import _side_decision, extract_report_images, extract_teardown_inventory


HTML = """
<h4>一、产品开箱</h4>
<p>Type-C接口外观一览。</p>
<h4>二、产品拆解</h4>
<h4>充电盒拆解</h4>
<figure><img src="https://example.test/pmic.jpg" alt="PMIC" /></figure>
<p>SinhMicro昇生微电子SSP707电源管理芯片，用于内置电池充电。</p>
<figure><img src="https://example.test/mcu.jpg" alt="MCU" /></figure>
<p>GigaDevice兆易创新GD32E113CBT6微控制器，用于整机控制。</p>
<p>丝印H1的TVS保护管，用于输入过压保护。</p>
<h4>耳机拆解</h4>
<p>AIROHA达发（络达）AB1585M蓝牙音频SoC，用于无线连接和音频处理。</p>
<p>丝印5V Ar的IC。</p>
<p>连接天线的金属弹片。</p>
<h4>三、总结</h4>
<p>Type-C接口总结，不应重复进入拆解清单。</p>
"""


class TeardownInventoryTests(unittest.TestCase):
    def test_extracts_audio_codec_and_driver_types(self) -> None:
        html = """
        <h2>耳机拆解</h2>
        <p>Cirrus Logic凌云逻辑CS47L90，是一款低功耗音频编解码器。</p>
        <p>耳机内有一个40mm的动圈单元和一个动铁单元。</p>
        """
        rows = extract_teardown_inventory(html)
        by_component = {row["component"]: row for row in rows}
        self.assertEqual(by_component["音频编解码器/DSP"]["model"], "CS47L90")
        self.assertEqual(by_component["音频编解码器/DSP"]["brand"], "Cirrus Logic凌云逻辑")
        self.assertEqual(by_component["动圈单元"]["specification"], "40mm")
        self.assertIn("动铁单元", by_component)

    def test_extracts_functional_classes_without_cost_or_variant_dimensions(self) -> None:
        rows = extract_teardown_inventory(HTML)
        by_model = {row["model"]: row for row in rows if row["model"]}
        self.assertEqual(by_model["SSP707"]["classification"], "core")
        self.assertEqual(by_model["GD32E113CBT6"]["component"], "微控制器")
        self.assertEqual(by_model["H1"]["classification"], "key")
        self.assertEqual(by_model["AB1585M"]["classification"], "core")
        self.assertEqual(by_model["5VAr"]["classification"], "unidentified_marking")
        self.assertTrue(any(row["component"] == "天线弹片" for row in rows))
        self.assertFalse(any(row["component"] == "Type-C接口" for row in rows))
        for row in rows:
            self.assertNotIn("cost", row)
            self.assertNotIn("version", row)
            self.assertNotIn("batch", row)
            self.assertNotIn("confidence", row["evidence"])

    def test_location_prefers_section_context_and_flags_unresolved_conflict(self) -> None:
        side, reason, status = _side_decision("为耳机充电的Pogo Pin连接器", "充电盒拆解")
        self.assertEqual(side, "充电盒")
        self.assertEqual(status, "resolved")
        self.assertIn("章节标题", reason)

        side, _, status = _side_decision("充电盒与耳机之间的连接器", "产品拆解")
        self.assertEqual(side, "")
        self.assertEqual(status, "needs_review")

    def test_keeps_all_report_originals_and_links_nearest_evidence_image(self) -> None:
        images = extract_report_images(HTML)
        self.assertEqual(len(images), 2)
        rows = extract_teardown_inventory(HTML)
        mcu = next(row for row in rows if row["model"] == "GD32E113CBT6")
        self.assertEqual(mcu["evidence_images"][0]["url"], "https://example.test/mcu.jpg")

    def test_preserves_energy_unit_without_truncating_wh_to_watts(self) -> None:
        html = """
        <h4>产品拆解</h4>
        <h4>充电盒拆解</h4>
        <p>充电盒内置锂电池组，型号：772130，额定容量：500mAh，额定能量：1.925Wh，标称电压：3.85V。</p>
        """
        rows = extract_teardown_inventory(html)
        battery = next(row for row in rows if row["component"] == "电池")
        self.assertIn("1.925Wh", battery["specification"])
        self.assertNotIn("1.925W /", battery["specification"])

    def test_localizes_long_prose_and_excludes_unheaded_summary(self) -> None:
        html = """
        <p>高通CSR8675蓝牙音频SoC用于音频处理；内置电池容量1200mAh。</p>
        <p>我爱音频网总结：耳罩为硅胶材质，主控芯片为高通CSR8675，内置电池容量1200mAh。</p>
        """
        rows = extract_teardown_inventory(html)
        battery = next(row for row in rows if row["component"] == "电池")
        self.assertEqual(battery["model"], "")
        self.assertEqual(battery["fact_text"], "内置电池容量1200mAh。")
        self.assertNotIn("硅胶", battery["fact_text"])


if __name__ == "__main__":
    unittest.main()
