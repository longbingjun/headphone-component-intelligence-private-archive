from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from server.component_analysis import component_rows_from_database
from server.models import Base, BomItem, Product, Report, Video, VideoFact
from server.storage import LocalObjectStorage
from server.video_intelligence import (
    discover_video,
    publish_artifacts,
    serialize_product_video_intelligence,
    serialize_product_video_profile,
)


SAMPLE = {
    "id": "BV1tsNm6FEA5",
    "title": "拆解视频：SONY索尼LinkBuds Clip开放式耳机",
    "webpage_url": "https://www.bilibili.com/video/BV1tsNm6FEA5/",
    "uploader": "我爱音频网",
    "upload_date": "20260713",
    "brand": "SONY索尼",
    "model": "LinkBuds Clip",
    "category": "开放式耳机",
}


class VideoIntelligenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    @staticmethod
    def _complete_report(report_id: str = "280462") -> Report:
        return Report(
            id=report_id,
            payload={
                "id": report_id,
                "views": {
                    "cost": {"teardown_inventory": [{"component": f"part-{i}"} for i in range(5)]},
                    "structure": {"report_image_urls": [{"url": "https://example.test/report.jpg"}]},
                },
            },
        )

    def test_existing_product_skips_expensive_processing(self) -> None:
        with Session(self.engine) as session:
            product = Product(
                    id="sony--linkbuds-clip",
                    brand="SONY索尼",
                    model="LinkBuds Clip",
                    category="开放式耳机",
                    payload={"canonical_id": "sony--linkbuds-clip", "report_ids": ["280462"]},
                )
            product.reports.append(self._complete_report())
            session.add(product)
            session.commit()
            result = discover_video(session, SAMPLE)
            session.commit()

            self.assertEqual(result.status, "skipped_report_exists")
            self.assertEqual(result.matched_product_id, "sony--linkbuds-clip")
            self.assertEqual(session.get(Video, SAMPLE["id"]).facts, [])

    def test_title_only_metadata_can_match_existing_product(self) -> None:
        with Session(self.engine) as session:
            product = Product(
                    id="sony--linkbuds-clip",
                    brand="SONY索尼",
                    model="LinkBuds Clip",
                    category="开放式耳机",
                    payload={"canonical_id": "sony--linkbuds-clip"},
                )
            product.reports.append(self._complete_report())
            session.add(product)
            session.commit()
            metadata = {key: value for key, value in SAMPLE.items() if key not in {"brand", "model"}}
            result = discover_video(session, metadata)
            self.assertEqual(result.status, "skipped_report_exists")
            video = session.get(Video, SAMPLE["id"])
            self.assertEqual(video.brand, "SONY索尼")
            self.assertEqual(video.model, "LinkBuds Clip")

    def test_video_title_wrapper_is_not_part_of_the_product_model(self) -> None:
        from server.video_intelligence import infer_identity_from_title

        brand, model = infer_identity_from_title(
            "视频拆解：索尼Sony PlayStation 5 PULSE 3D 无线耳机组"
        )
        self.assertEqual(brand, "SONY索尼")
        self.assertEqual(model, "PlayStation 5 PULSE 3D")

    def test_only_first_uncovered_video_is_queued(self) -> None:
        with Session(self.engine) as session:
            first = discover_video(
                session,
                {
                    **SAMPLE,
                    "id": "BVNEW0001",
                    "brand": "DemoAudio",
                    "model": "Clip X1",
                    "title": "DemoAudio Clip X1 拆解",
                },
            )
            second = discover_video(
                session,
                {
                    **SAMPLE,
                    "id": "BVNEW0002",
                    "brand": "DemoAudio",
                    "model": "Clip X1",
                    "title": "DemoAudio Clip X1 深度拆解",
                },
            )
            self.assertEqual(first.status, "queued")
            self.assertEqual(second.status, "candidate_secondary_video")

    def test_product_gate_is_checked_again_before_processing(self) -> None:
        with tempfile.TemporaryDirectory() as temp, Session(self.engine) as session:
            queued = discover_video(
                session,
                {
                    **SAMPLE,
                    "id": "BVNEW0003",
                    "brand": "DemoAudio",
                    "model": "Clip X3",
                    "title": "DemoAudio Clip X3 拆解",
                },
            )
            self.assertEqual(queued.status, "queued")
            product = Product(
                    id=queued.candidate_product_id,
                    brand="DemoAudio",
                    model="Clip X3",
                    category="开放式耳机",
                    payload={"canonical_id": queued.candidate_product_id},
                )
            product.reports.append(self._complete_report("report-x3"))
            session.add(product)
            session.flush()
            result = publish_artifacts(
                session,
                video_id="BVNEW0003",
                artifacts_dir=Path(temp) / "does-not-need-to-exist",
                storage=LocalObjectStorage(Path(temp) / "objects"),
            )
            self.assertEqual(result.status, "skipped_report_exists")
            self.assertEqual(result.facts_total, 0)

    def test_bare_product_without_teardown_report_does_not_suppress_video(self) -> None:
        with Session(self.engine) as session:
            session.add(
                Product(
                    id="sony--linkbuds-clip",
                    brand="SONY索尼",
                    model="LinkBuds Clip",
                    category="开放式耳机",
                    payload={"canonical_id": "sony--linkbuds-clip"},
                )
            )
            session.commit()
            result = discover_video(session, SAMPLE)
            self.assertEqual(result.status, "queued")
            self.assertEqual(result.reason, "existing_product_missing_complete_teardown_report")

    def test_comparison_preview_can_include_video_superseded_by_report(self) -> None:
        with Session(self.engine) as session:
            product = Product(
                id="sony--linkbuds-clip",
                brand="SONY索尼",
                model="LinkBuds Clip",
                category="开放式耳机",
                payload={"canonical_id": "sony--linkbuds-clip", "report_ids": ["280462"]},
            )
            product.reports.append(self._complete_report())
            session.add_all(
                [
                    product,
                    Video(
                        id="BVLEGACY",
                        title="LinkBuds Clip 拆解",
                        processing_status="skipped_report_exists",
                        subtitle_method="creator_subtitle_track",
                    ),
                    VideoFact(
                        video_id="BVLEGACY",
                        product_id=product.id,
                        segment_id=1,
                        start_seconds=12,
                        end_seconds=15,
                        fact_text="主控采用 AB1585",
                        corrected_text="主控采用 AB1585",
                        status="published",
                        needs_review=False,
                    ),
                ]
            )
            session.commit()

            self.assertEqual(serialize_product_video_intelligence(session, product.id), [])
            preview = serialize_product_video_intelligence(
                session,
                product.id,
                include_superseded=True,
            )
            self.assertEqual(len(preview), 1)
            self.assertEqual(preview[0]["fact"], "主控采用 AB1585")

    def test_explicit_market_fact_is_not_reclassified_as_packaging(self) -> None:
        with Session(self.engine) as session:
            product = Product(
                id="demo--clip",
                brand="Demo",
                model="Clip",
                category="耳夹式耳机",
                payload={"canonical_id": "demo--clip"},
            )
            video = Video(
                id="BVMARKET",
                title="Demo Clip 拆解",
                processing_status="published",
                candidate_product_id=product.id,
                matched_product_id=product.id,
            )
            session.add_all(
                [
                    product,
                    video,
                    VideoFact(
                        video_id=video.id,
                        product_id=product.id,
                        segment_id=1,
                        fact_text="包装标注续航为7H/21H",
                        raw_text="包装标注续航为7H/21H",
                        importance=4,
                        status="published",
                        needs_review=False,
                        payload={"section": "market"},
                    ),
                    VideoFact(
                        video_id=video.id,
                        product_id=product.id,
                        segment_id=2,
                        start_seconds=3,
                        fact_text="产品标注支持IPX4防水",
                        raw_text="产品标注支持IPX4防水",
                        importance=4,
                        status="published",
                        needs_review=False,
                        payload={"section": "market"},
                    ),
                ]
            )
            session.flush()

            profile = serialize_product_video_profile(session, product.id)
            claims = profile["market"]["consumer_claims"]
            self.assertEqual(len(claims), 2)
            self.assertEqual(claims[0]["category"], "续航充电")
            self.assertEqual(claims[1]["category"], "耐用防护")

    def test_publish_artifacts_creates_product_and_only_exposes_reviewed_facts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifacts = root / "artifacts"
            keyframes = artifacts / "keyframes"
            keyframes.mkdir(parents=True)
            (keyframes / "frame.jpg").write_bytes(b"minimal-test-jpeg")
            (artifacts / "ocr_segments.json").write_text(
                json.dumps(
                    [
                        {
                            "id": 1,
                            "start": 10.0,
                            "end": 12.0,
                            "raw_text": "电池容量500mAh",
                            "confidence": 0.98,
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (artifacts / "run_stats.json").write_text(
                json.dumps({"subtitle_method": "creator_subtitle_track"}),
                encoding="utf-8",
            )
            (artifacts / "facts.json").write_text(
                json.dumps(
                    [
                        {
                            "segment_id": 1,
                            "start": 10.0,
                            "end": 12.0,
                            "raw_text": "电池容量500mAh",
                            "corrected_text": "电池容量 500 mAh",
                            "fact": "充电盒电池容量为 500 mAh",
                            "importance": 5,
                            "ocr_confidence": 0.98,
                            "needs_review": False,
                            "keyframe": "keyframes/frame.jpg",
                            "keyframe_time": 11.0,
                        },
                        {
                            "segment_id": 2,
                            "start": 20.0,
                            "end": 22.0,
                            "raw_text": "疑似AB123",
                            "fact": "主控型号疑似 AB123",
                            "needs_review": True,
                        },
                        {
                            "segment_id": 3,
                            "start": 30.0,
                            "end": 32.0,
                            "raw_text": "感谢观看",
                            "fact": "视频进入结束语",
                            "importance": 2,
                            "needs_review": False,
                            "keyframe": "keyframes/frame.jpg",
                        },
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with Session(self.engine) as session:
                discovered = discover_video(
                    session,
                    {
                        **SAMPLE,
                        "id": "BVNEW1000",
                        "brand": "DemoAudio",
                        "model": "Clip X2",
                        "title": "DemoAudio Clip X2 拆解",
                    },
                )
                self.assertEqual(discovered.status, "queued")
                result = publish_artifacts(
                    session,
                    video_id="BVNEW1000",
                    artifacts_dir=artifacts,
                    storage=LocalObjectStorage(root / "objects"),
                )
                session.commit()

                self.assertEqual(result.status, "published")
                self.assertEqual(result.facts_published, 1)
                self.assertEqual(result.facts_needing_review, 2)
                self.assertEqual(result.keyframes_uploaded, 1)
                self.assertEqual(session.get(Video, "BVNEW1000").subtitle_method, "creator_subtitle_track")
                self.assertEqual(session.get(Video, "BVNEW1000").site_publish_status, "pending")
                self.assertIsNotNone(session.get(Product, result.product_id))
                facts = session.scalars(select(VideoFact).order_by(VideoFact.segment_id)).all()
                self.assertEqual(
                    [fact.status for fact in facts],
                    ["published", "needs_review", "needs_review"],
                )
                visible = serialize_product_video_intelligence(session, result.product_id)
                self.assertEqual(len(visible), 1)
                self.assertEqual(visible[0]["fact"], "充电盒电池容量为 500 mAh")
                self.assertTrue(visible[0]["keyframe_path"].startswith("/video-media/"))
                bom_items = session.scalars(select(BomItem)).all()
                self.assertEqual(len(bom_items), 1)
                self.assertEqual(bom_items[0].component_key, "battery")
                self.assertEqual(bom_items[0].source_type, "video")
                self.assertEqual(bom_items[0].source_video_id, "BVNEW1000")
                self.assertEqual(bom_items[0].parameters[0].value_text, "500mAh")

                analysis_rows = component_rows_from_database(session, "battery")
                self.assertEqual(len(analysis_rows), 1)
                self.assertEqual(analysis_rows[0]["source_type"], "video")
                self.assertEqual(analysis_rows[0]["source_video_id"], "BVNEW1000")
                self.assertTrue(analysis_rows[0]["evidence_image"]["public_path"])

                # A later complete report becomes the default analytical
                # source without deleting the historical video BOM audit row.
                product = session.get(Product, result.product_id)
                product.reports.append(self._complete_report("report-after-video"))
                session.flush()
                self.assertEqual(component_rows_from_database(session, "battery"), [])
                self.assertEqual(len(session.scalars(select(BomItem)).all()), 1)


if __name__ == "__main__":
    unittest.main()
