from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from server.video_pipeline import (
    SubtitleSegment,
    detect_summary_start,
    frame_quality,
    heuristic_events,
    parse_subtitle_file,
    validate_events,
)


class VideoPipelineTests(unittest.TestCase):
    def test_creator_vtt_is_parsed_and_duplicate_cues_are_merged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "source.zh-CN.vtt"
            path.write_text(
                """WEBVTT

00:00:01.000 --> 00:00:02.000
电池容量 500mAh

00:00:02.050 --> 00:00:03.000
电池容量 500mAh

00:00:05.000 --> 00:00:06.000
主控芯片 AB123
""",
                encoding="utf-8",
            )
            segments = parse_subtitle_file(path)

        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0].raw_text, "电池容量 500mAh")
        self.assertAlmostEqual(segments[0].end, 3.0)
        self.assertEqual(segments[0].samples, 2)

    def test_late_recap_is_detected_but_opening_summary_is_ignored(self) -> None:
        segments = [
            SubtitleSegment(1, 5, 7, "先总结三个特点"),
            SubtitleSegment(2, 70, 72, "最后总结一下主要配置"),
        ]
        result = detect_summary_start(segments, 100)
        self.assertTrue(result["summary_detected"])
        self.assertEqual(result["summary_start"], 70)

    def test_late_internal_configuration_recap_is_detected(self) -> None:
        segments = [
            SubtitleSegment(1, 10, 12, "包装盒内部物品有耳机和充电线"),
            SubtitleSegment(2, 82, 84, "内部主要配置方面"),
            SubtitleSegment(3, 95, 97, "以上就是此次的拆解内容了"),
        ]
        result = detect_summary_start(segments, 100)
        self.assertTrue(result["summary_detected"])
        self.assertEqual(result["summary_start"], 82)

    def test_heuristic_fallback_keeps_phase_and_drops_bare_operations(self) -> None:
        segments = [
            SubtitleSegment(1, 1, 2, "包装盒内部物品有耳机和充电线"),
            SubtitleSegment(2, 3, 4, "耳机充电盒"),
            SubtitleSegment(3, 5, 6, "电池容量460毫安时"),
            SubtitleSegment(4, 7, 8, "下面进入拆解部分"),
            SubtitleSegment(5, 9, 10, "取出扬声器"),
            SubtitleSegment(6, 11, 12, "杰理AK16G6蓝牙音频SoC"),
        ]
        events = heuristic_events(segments)
        by_id = {item["segment_id"]: item for item in events}
        self.assertEqual(by_id[1]["section"], "unboxing_packaging")
        self.assertEqual(by_id[3]["section"], "unboxing_charging_case")
        self.assertNotIn(5, by_id)
        self.assertEqual(by_id[6]["section"], "bom")

    def test_video_bom_context_keeps_supplier_followup(self) -> None:
        candidates = [
            SubtitleSegment(1, 1, 2, "耳机内置电池型号PATL1254B"),
            SubtitleSegment(2, 2.1, 3, "额定容量70毫安时"),
            SubtitleSegment(3, 3.1, 4, "来自小锂新能源"),
            SubtitleSegment(4, 4.1, 5, "杰理AK16G6蓝牙音频SoC"),
        ]
        result = validate_events(
            [{"segment_id": 1, "fact": candidates[0].raw_text, "section": "bom"}],
            candidates,
        )
        self.assertIn("来自小锂新能源", result[0]["bom_context_text"])
        self.assertNotIn("AK16G6", result[0]["bom_context_text"])

    def test_bom_narrative_heading_supplies_usage_side_with_evidence(self) -> None:
        candidates = [
            SubtitleSegment(1, 1, 2, "首先拆解耳机部分"),
            SubtitleSegment(2, 3, 4, "杰理AK16G6蓝牙音频SoC"),
            SubtitleSegment(3, 20, 21, "拆解充电盒取掉底部外壳"),
            SubtitleSegment(4, 22, 23, "昇生微电子SS88F8H电源管理SoC"),
        ]
        result = validate_events(
            [
                {"segment_id": 2, "fact": candidates[1].raw_text, "section": "bom"},
                {"segment_id": 4, "fact": candidates[3].raw_text, "section": "bom"},
            ],
            candidates,
        )
        self.assertEqual(result[0]["narrative_side"], "耳机")
        self.assertEqual(result[0]["narrative_side_evidence"], "首先拆解耳机部分")
        self.assertEqual(result[1]["narrative_side"], "充电盒")
        self.assertEqual(result[1]["narrative_side_evidence"], "拆解充电盒取掉底部外壳")

    def test_laplacian_quality_prefers_detailed_image(self) -> None:
        flat = np.zeros((240, 320, 3), dtype=np.uint8)
        detailed = flat.copy()
        detailed[20:190:4, 20:300] = 255
        self.assertGreater(frame_quality(detailed), frame_quality(flat))

    def test_model_output_cannot_invent_segment_or_silently_change_number(self) -> None:
        candidates = [SubtitleSegment(1, 1, 2, "电池容量500mAh")]
        result = validate_events(
            [
                {
                    "segment_id": 1,
                    "corrected_text": "电池容量550mAh",
                    "fact": "电池容量为550mAh",
                    "importance": 9,
                    "needs_review": False,
                },
                {"segment_id": 99, "fact": "模型虚构事实"},
            ],
            candidates,
        )
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["needs_review"])
        self.assertEqual(result[0]["importance"], 5)

    def test_split_component_labels_keep_only_nearby_source_context(self) -> None:
        candidates = [
            SubtitleSegment(1, 1, 2, "电池额定容量1000mAh"),
            SubtitleSegment(2, 2.1, 3, "型号LIP1522"),
            SubtitleSegment(3, 3.1, 4, "充电限制电压4.2V"),
        ]
        result = validate_events(
            [{"segment_id": 2, "fact": "型号 LIP1522", "section": "bom"}],
            candidates,
        )
        self.assertIn("电池额定容量1000mAh", result[0]["bom_context_text"])
        self.assertIn("充电限制电压4.2V", result[0]["bom_context_text"])

    def test_component_context_does_not_cross_into_next_component(self) -> None:
        candidates = [
            SubtitleSegment(1, 1, 2, "这是丝印24C128A存储器"),
            SubtitleSegment(2, 2.1, 3, "这是思佳讯AV6351无线音频传输IC"),
            SubtitleSegment(3, 3.1, 4, "耳罩采用倾斜结构"),
        ]
        result = validate_events(
            [{"segment_id": 2, "fact": candidates[1].raw_text, "section": "bom"}],
            candidates,
        )
        self.assertEqual(result[0]["bom_context_text"], candidates[1].raw_text)

    def test_mcu_identity_line_joins_its_parameter_block(self) -> None:
        candidates = [
            SubtitleSegment(1, 1, 2, "这是恩智浦LPC804"),
            SubtitleSegment(2, 2.1, 3, "基于ArmCortex-m0+的低成本32位MCU"),
            SubtitleSegment(3, 3.1, 4, "工作频率高达15MHz"),
            SubtitleSegment(4, 4.1, 5, "这是旭化成AK4961音频芯片"),
        ]
        result = validate_events(
            [{"segment_id": 3, "fact": candidates[2].raw_text, "section": "bom"}],
            candidates,
        )
        self.assertIn("恩智浦LPC804", result[0]["bom_context_text"])
        self.assertNotIn("AK4961", result[0]["bom_context_text"])

    def test_unrelated_numeric_interface_does_not_inherit_microphone(self) -> None:
        candidates = [
            SubtitleSegment(1, 1, 2, "通话麦克风拾音孔"),
            SubtitleSegment(2, 2.1, 3, "3.5mm音频输入接口"),
        ]
        result = validate_events(
            [{"segment_id": 2, "fact": candidates[1].raw_text}], candidates
        )
        self.assertEqual(result[0]["bom_context_text"], candidates[1].raw_text)

    def test_protection_block_stops_before_next_chip_identity(self) -> None:
        candidates = [
            SubtitleSegment(1, 1, 2, "这是微源LP5305过压过流保护IC"),
            SubtitleSegment(2, 2.1, 3, "在电池过压时断开充电器输入"),
            SubtitleSegment(3, 3.1, 4, "这是恩智浦LPC804"),
            SubtitleSegment(4, 4.1, 5, "基于ArmCortex-m0+的低成本32位MCU"),
        ]
        result = validate_events(
            [{"segment_id": 2, "fact": candidates[1].raw_text}], candidates
        )
        self.assertIn("LP5305", result[0]["bom_context_text"])
        self.assertNotIn("LPC804", result[0]["bom_context_text"])


if __name__ == "__main__":
    unittest.main()
