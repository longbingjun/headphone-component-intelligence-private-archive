from __future__ import annotations

import unittest

from core.text_quality import contains_c1_controls, iter_display_strings, repair_utf8_mojibake
from scripts.repair_unboxing_encoding import _repair_product_snapshot


class TextQualityTests(unittest.TestCase):
    def test_repairs_utf8_text_decoded_as_latin1(self) -> None:
        broken = "æ\x8b\x86è§£æ\x8a¥å\x91\x8aï¼\x9aå°\x8fé¸\x9fè\x80³æ\x9cº"
        self.assertEqual(repair_utf8_mojibake(broken), "拆解报告：小鸟耳机")

    def test_preserves_real_non_breaking_space_in_mixed_text(self) -> None:
        broken = "AppleÂ\xa0AirPods æ\x8b\x86è§£"
        self.assertEqual(repair_utf8_mojibake(broken), "Apple\xa0AirPods 拆解")

    def test_repairs_context_qualified_truncated_legacy_character(self) -> None:
        self.assertEqual(repair_utf8_mojibake("采用漫步è\x80方案"), "采用漫步者方案")

    def test_preserves_existing_smart_quotes_during_partial_repair(self) -> None:
        self.assertEqual(repair_utf8_mojibake("“通话降å\x99”"), "“通话降噪”")

    def test_display_iterator_excludes_urls(self) -> None:
        payload = {"description": "正常", "images": [{"url": "https://x/æ\x8b.jpg", "alt": "æ\x8b"}]}
        self.assertEqual(
            list(iter_display_strings(payload)),
            [("description", "正常"), ("images[0].alt", "æ\x8b")],
        )
        self.assertTrue(contains_c1_controls("æ\x8b"))

    def test_unboxing_repair_falls_back_to_source_backed_description(self) -> None:
        broken = "æ\x90\xadé \x8d"
        product = {
            "canonical_id": "sample",
            "unboxing": {
                "packaging": {
                    "description": "搭配亲肤材质。",
                    "appearance_images": [],
                    "display_bullets": [
                        {"text": broken, "evidence_quote": broken}
                    ],
                }
            },
        }

        stats = _repair_product_snapshot(
            product,
            {"packaging": {"images": []}},
            "搭配亲肤材质。",
        )

        bullet = product["unboxing"]["packaging"]["display_bullets"][0]
        self.assertEqual(bullet["text"], "搭配亲肤材质。")
        self.assertEqual(bullet["evidence_quote"], "搭配亲肤材质。")
        self.assertEqual(stats["other_texts"], 2)


if __name__ == "__main__":
    unittest.main()
