from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from server.exporter import export_repository
from server.models import Base, BomItem, BomItemParameter, Product, Report, Video, VideoFact


class ExporterTests(unittest.TestCase):
    def test_database_payloads_are_restored_as_compatibility_json(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        with tempfile.TemporaryDirectory() as temp, Session(engine) as session:
            session.add(Product(id="acme--pods", payload={"canonical_id": "acme--pods"}))
            session.add(Report(id="report-1", payload={"id": "report-1", "title": "Test"}))
            session.add(Video(id="video-1", payload={"id": "video-1", "title": "Video"}))
            session.commit()
            root = Path(temp)
            stats = export_repository(
                session,
                report_output=root / "reports",
                product_output=root / "products",
                video_output=root / "videos",
            )
            self.assertEqual(
                stats.to_dict(), {"products": 1, "reports": 1, "videos": 1}
            )
            self.assertEqual(
                json.loads((root / "products" / "acme--pods.json").read_text(encoding="utf-8")),
                {"canonical_id": "acme--pods"},
            )
            self.assertEqual(
                json.loads((root / "reports" / "report-1.json").read_text(encoding="utf-8")),
                {"id": "report-1", "title": "Test"},
            )
            self.assertEqual(
                json.loads((root / "videos" / "video-1.json").read_text(encoding="utf-8"))["id"],
                "video-1",
            )

    def test_video_only_product_uses_the_report_shaped_detail_contract(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        with tempfile.TemporaryDirectory() as temp, Session(engine) as session:
            product = Product(
                id="acme--pulse",
                brand="Acme",
                model="Pulse",
                category="头戴式耳机",
                payload={
                    "canonical_id": "acme--pulse",
                    "brand": "Acme",
                    "model": "Pulse",
                    "category": "头戴式耳机",
                    "report_ids": [],
                    "video_ids": ["video-2"],
                },
            )
            video = Video(
                id="video-2",
                title="Acme Pulse 拆解",
                source_url="https://example.test/video-2",
                processing_status="published",
                subtitle_method="hard_subtitle_ocr",
                matched_product_id=product.id,
            )
            fact = VideoFact(
                video_id=video.id,
                product_id=product.id,
                segment_id=1,
                start_seconds=12,
                end_seconds=15,
                raw_text="耳机扬声器单元尺寸约为40mm",
                corrected_text="耳机扬声器单元尺寸约为 40 mm",
                fact_text="耳机扬声器单元尺寸约为 40 mm",
                importance=4,
                needs_review=False,
                keyframe_object_key="video-keyframes/video-2/frame.jpg",
                status="published",
                payload={"section": "bom", "topic": ""},
            )
            appearance_fact = VideoFact(
                video_id=video.id,
                product_id=product.id,
                segment_id=2,
                start_seconds=20,
                end_seconds=23,
                raw_text="头梁内侧采用悬挂式头垫设计",
                corrected_text="头梁内侧采用悬挂式头垫设计",
                fact_text="头梁内侧采用悬挂式头垫设计",
                importance=3,
                needs_review=False,
                status="published",
                payload={"section": "specification", "topic": "佩戴体验"},
            )
            item = BomItem(
                product_id=product.id,
                ordinal=0,
                side="耳机",
                component="扬声器/发声单元",
                component_key="speaker_driver",
                role="core",
                source_type="video",
                source_video_id=video.id,
                source_text=fact.raw_text,
                confidence=0.92,
                evidence={"keyframe_object_key": fact.keyframe_object_key},
            )
            item.parameters.append(
                BomItemParameter(label="尺寸", value_text="40mm", evidence_quote=fact.raw_text)
            )
            session.add_all([product, video, fact, appearance_fact, item])
            session.commit()

            root = Path(temp)
            export_repository(
                session,
                report_output=root / "reports",
                product_output=root / "products",
                video_output=root / "videos",
            )
            payload = json.loads(
                (root / "products" / "acme--pulse.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["profile_source"], "video_intelligence")
            self.assertEqual(payload["technical_facts"], payload["teardown_inventory"])
            self.assertEqual(payload["technical_facts"], payload["bom_table"])
            row = payload["technical_facts"][0]
            self.assertEqual(row["component_key"], "speaker_driver")
            self.assertEqual(row["parameters"][0]["value"], "40mm")
            self.assertEqual(
                row["evidence_images"][0]["url"],
                "/video-media/video-2/frame.jpg",
            )
            self.assertEqual(payload["cost_snapshot"]["speaker"], "40mm")
            self.assertEqual(
                payload["unboxing"]["earbuds"]["display_bullets"][0]["text"],
                "头梁内侧采用悬挂式头垫设计",
            )


if __name__ == "__main__":
    unittest.main()
