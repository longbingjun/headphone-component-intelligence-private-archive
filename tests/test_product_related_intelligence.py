from __future__ import annotations

import unittest

from scripts.prepare_web_data import _related_intelligence_payload


class ProductRelatedIntelligenceTests(unittest.TestCase):
    def test_reports_and_videos_keep_traceable_links(self) -> None:
        product = {"report_ids": ["r1"], "video_ids": ["v1"]}
        reports = {
            "r1": {
                "id": "r1",
                "title": "拆解报告",
                "url": "https://example.test/reports/r1",
                "published_at": "2026-01-01",
                "author": "研究员",
            }
        }
        videos = {
            "v1": {
                "id": "v1",
                "title": "拆解视频",
                "url": "https://example.test/videos/v1",
                "published_at": "2026-02-01",
                "publisher": "视频作者",
                "video_embed_url": (
                    "https://player.bilibili.com/player.html?bvid=BV1tsNm6FEA5&page=1"
                ),
            }
        }

        result = _related_intelligence_payload(product, reports, videos)

        self.assertEqual([item["kind"] for item in result], ["video", "report"])
        self.assertEqual(result[0]["video_url"], "https://www.bilibili.com/video/BV1tsNm6FEA5/")
        self.assertEqual(result[0]["source_url"], "https://example.test/videos/v1")
        self.assertEqual(result[1]["publisher"], "研究员")

    def test_missing_source_record_remains_identifiable(self) -> None:
        result = _related_intelligence_payload(
            {"report_ids": ["missing"], "video_ids": []}, {}, {}
        )

        self.assertEqual(result[0]["id"], "missing")
        self.assertEqual(result[0]["title"], "missing")
        self.assertEqual(result[0]["source_url"], "")


if __name__ == "__main__":
    unittest.main()
