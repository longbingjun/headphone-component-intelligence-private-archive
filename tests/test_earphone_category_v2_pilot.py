from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.classify_earphone_categories import direct_record
from scripts.pilot_earphone_category_v2 import (
    chat_endpoint,
    explicit_labels,
    html_paragraphs,
    load_env_file,
    parsed_items_by_id,
    repair_mojibake,
    rule_hint,
    select_sample,
    select_sample_from_manifest,
    structural_support,
    validate_result,
)


def source(product_id: str, hint: str, text: str) -> dict:
    return {
        "product_id": product_id,
        "brand": "示例品牌",
        "model": product_id,
        "legacy_category": "开放式耳机",
        "report_id": product_id,
        "report_title": f"{product_id}拆解",
        "published_at": "2026-01-01",
        "source_url": "https://example.test/report",
        "segments": [{"segment_id": "form_1", "text": text}],
        "rule_hint": hint,
        "rule_evidence_quote": text,
        "rule_signals": [hint] if hint not in {"unclear", "conflict"} else [],
    }


class EarphoneCategoryV2PilotTests(unittest.TestCase):
    def test_rule_hint_requires_an_explicit_unambiguous_form(self) -> None:
        label, quote, _ = rule_hint([{"segment_id": "a", "text": "耳机采用耳夹式设计，通过C形桥固定。"}])
        self.assertEqual(label, "ear_clip")
        self.assertIn("耳夹式", quote)

        unclear, _, _ = rule_hint([{"segment_id": "a", "text": "这是一款开放式真无线耳机。"}])
        self.assertEqual(unclear, "unclear")

        conflict, _, signals = rule_hint([{"segment_id": "a", "text": "本段同时列出全入耳式产品和半入耳式产品。"}])
        self.assertEqual(conflict, "conflict")
        self.assertEqual(signals, ["full_in_ear", "semi_in_ear"])

    def test_priority_and_transition_rules_match_product_taxonomy(self) -> None:
        self.assertEqual(explicit_labels("采用耳挂式入耳设计，绕在耳廓上佩戴。"), ["ear_hook"])
        self.assertEqual(explicit_labels("产品由入耳式改为了半入耳式设计。"), ["semi_in_ear"])
        self.assertEqual(explicit_labels("这是一款颈挂式蓝牙入耳耳机。"), ["neckband"])
        self.assertEqual(explicit_labels("这是一款无线脖挂蓝牙耳机。"), ["neckband"])
        self.assertEqual(explicit_labels("主打降噪的项圈蓝牙耳机。"), ["neckband"])
        self.assertEqual(explicit_labels("产品名称：主动降噪耳机，佩戴方式：入耳式，接口：苹果Lightning接口。"), ["wired"])
        self.assertEqual(explicit_labels("Linner NC21主动降噪耳机采用Lightning接口直接供电。"), ["wired"])
        self.assertEqual(explicit_labels("充电盒采用Lightning接口充电。"), [])
        self.assertEqual(explicit_labels("耳机充电盒采用USB-C接口充电。"), [])
        self.assertEqual(explicit_labels("耳机看上去像入耳式设计，但佩戴时是半入耳的状态。"), ["semi_in_ear"])

    def test_direct_classifier_rejects_other_product_or_charging_evidence(self) -> None:
        unrelated = source(
            "target-product",
            "wired",
            "此前还拆解过另一款有线耳机。",
        )
        unrelated["segments"][0]["target_match"] = "false"
        self.assertIsNone(direct_record(unrelated))

        charging = source(
            "target-product",
            "unclear",
            "目标真无线耳机的充电盒采用USB-C接口充电。",
        )
        charging["segments"][0]["target_match"] = "true"
        self.assertIsNone(direct_record(charging))

        target = source(
            "target-product",
            "full_in_ear",
            "目标产品采用全入耳式设计。",
        )
        target["segments"][0]["target_match"] = "true"
        self.assertEqual(direct_record(target)["category_current"], "全入耳式耳机")

    def test_rule_hint_prefers_target_matched_segments(self) -> None:
        label, quote, signals = rule_hint(
            [
                {"segment_id": "intro_1", "text": "行业里还有耳夹式耳机。", "target_match": "false"},
                {"segment_id": "target_2", "text": "目标产品采用半入耳式设计。", "target_match": "true"},
            ]
        )
        self.assertEqual(label, "semi_in_ear")
        self.assertIn("半入耳式", quote)
        self.assertEqual(signals, ["semi_in_ear"])

    def test_validation_requires_verbatim_evidence_in_named_segment(self) -> None:
        item = source("p1", "ear_clip", "目标耳机采用耳夹式设计，夹持在耳廓上。")
        valid = validate_result(
            item,
            {
                "category_v2": "ear_clip",
                "evidence_quote": "采用耳夹式设计",
                "evidence_segment_id": "form_1",
                "rationale": "原文明示耳夹式。",
            },
        )
        self.assertTrue(valid["evidence_exact"])
        self.assertEqual(valid["pilot_review_status"], "high_confidence_candidate")

        corrected_segment = validate_result(
            item,
            {
                "category_v2": "ear_clip",
                "evidence_quote": "采用耳夹式设计",
                "evidence_segment_id": "wrong_segment",
                "rationale": "原文明示耳夹式。",
            },
        )
        self.assertTrue(corrected_segment["segment_exact"])
        self.assertTrue(corrected_segment["segment_id_corrected"])
        self.assertEqual(corrected_segment["resolved_evidence_segment_id"], "form_1")

        hallucinated = validate_result(
            item,
            {
                "category_v2": "ear_hook",
                "evidence_quote": "采用耳挂式设计",
                "evidence_segment_id": "form_1",
            },
        )
        self.assertFalse(hallucinated["evidence_exact"])
        self.assertEqual(hallucinated["pilot_review_status"], "needs_review")

    def test_sample_is_product_unique_and_biased_toward_difficult_cases(self) -> None:
        candidates = []
        groups = ["ear_clip", "ear_hook", "full_in_ear", "semi_in_ear", "conflict", "unclear"]
        for group in groups:
            for index in range(5):
                candidates.append(source(f"{group}-{index}", group, "测试文本"))
        selected = select_sample(candidates, 24)
        self.assertEqual(len(selected), 24)
        self.assertEqual(len({item["product_id"] for item in selected}), 24)
        self.assertTrue({"conflict", "unclear"}.issubset({item["rule_hint"] for item in selected}))

    def test_env_loader_does_not_override_injected_value(self) -> None:
        import os

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / ".env"
            path.write_text("PILOT_TEST_KEY=file-value\n", encoding="utf-8")
            os.environ["PILOT_TEST_KEY"] = "injected-value"
            try:
                load_env_file(path)
                self.assertEqual(os.environ["PILOT_TEST_KEY"], "injected-value")
            finally:
                os.environ.pop("PILOT_TEST_KEY", None)

    def test_chat_endpoint_accepts_base_or_full_endpoint(self) -> None:
        self.assertEqual(chat_endpoint("https://example.test/v1"), "https://example.test/v1/chat/completions")
        self.assertEqual(chat_endpoint("https://example.test/v1/chat/completions"), "https://example.test/v1/chat/completions")

    def test_partial_batch_response_can_be_detected_for_retry(self) -> None:
        response = {
            "parsed": {
                "items": [
                    {"product_id": "p1", "category_v2": "ear_clip"},
                    "ignore non-object output",
                    {"category_v2": "ear_hook"},
                ]
            }
        }
        indexed = parsed_items_by_id(response)
        self.assertEqual(set(indexed), {"p1"})
        self.assertEqual({"p1", "p2"} - set(indexed), {"p2"})

    def test_mojibake_repair_and_leaf_div_extraction(self) -> None:
        self.assertEqual(repair_mojibake("TWSè³æº"), "TWS耳机")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "article.html"
            path.write_text(
                '<main><p>这是正文第一段内容。</p><div>接口：苹果Lightning接口，有线耳机。</div></main>',
                encoding="utf-8",
            )
            blocks = html_paragraphs(path)
            self.assertIn("接口：苹果Lightning接口，有线耳机。", blocks)

    def test_previous_manifest_rebuilds_the_same_product_ids(self) -> None:
        candidates = [source("p1", "ear_clip", "耳夹式"), source("p2", "ear_hook", "耳挂式")]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample_manifest.json"
            path.write_text('{"items":[{"product_id":"p2"},{"product_id":"p1"}]}', encoding="utf-8")
            selected = select_sample_from_manifest(candidates, path)
            self.assertEqual([item["product_id"] for item in selected], ["p2", "p1"])

    def test_strong_ear_tip_evidence_is_an_auditable_medium_confidence_fallback(self) -> None:
        segments = [
            {
                "segment_id": "form_1",
                "text": "随机标配的三种尺寸硅胶耳塞，耳机上预装有一副。",
                "target_match": "false",
            }
        ]
        label, quote, segment_id = structural_support(segments)
        self.assertEqual(label, "full_in_ear")
        self.assertIn("三种尺寸硅胶耳塞", quote)
        self.assertEqual(segment_id, "form_1")

        item = source("p1", "unclear", segments[0]["text"])
        item["segments"] = segments
        resolved = validate_result(
            item,
            {"category_v2": "needs_review", "evidence_quote": "", "evidence_segment_id": ""},
        )
        self.assertEqual(resolved["resolved_category_v2"], "full_in_ear")
        self.assertEqual(resolved["pilot_review_status"], "medium_confidence_candidate")
        self.assertEqual(resolved["classification_basis"], "deterministic_structural_support")


if __name__ == "__main__":
    unittest.main()
