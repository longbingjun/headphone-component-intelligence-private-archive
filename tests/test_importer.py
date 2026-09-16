from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from server.importer import (
    ImportStats,
    _delete_excluded_source,
    _upsert_product,
    image_object_key,
    import_deployment_seeds,
    import_repository,
)
from server.models import (
    Base,
    BomItem,
    BomItemParameter,
    ImageAsset,
    Product,
    Report,
    Video,
)


class ImporterTests(unittest.TestCase):
    def test_release_upsert_preserves_server_only_links_and_bom(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            release_report = Report(id="r-release", payload={})
            runtime_report = Report(id="r-runtime", payload={})
            release_video = Video(id="v-release", payload={})
            runtime_video = Video(id="v-runtime", payload={})
            product = Product(id="acme--buds", category="全入耳式耳机", payload={})
            product.reports.append(runtime_report)
            product.videos.append(runtime_video)
            session.add_all(
                [product, release_report, runtime_report, release_video, runtime_video]
            )
            session.flush()
            session.add_all(
                [
                    BomItem(
                        product_id=product.id,
                        component="旧发布电池",
                        source_type="report",
                        source_report_id="r-release",
                    ),
                    BomItem(
                        product_id=product.id,
                        component="服务器新增电池",
                        source_type="report",
                        source_report_id="r-runtime",
                    ),
                ]
            )
            session.commit()

            payload = {
                "canonical_id": product.id,
                "brand": "Acme",
                "model": "Buds",
                "category": "全入耳式耳机",
                "report_ids": ["r-release"],
                "video_ids": ["v-release"],
                "technical_facts": [
                    {
                        "component": "新版发布电池",
                        "evidence": {"report_id": "r-release", "text": "新版发布电池"},
                    }
                ],
            }
            _upsert_product(
                session,
                payload,
                ImportStats(),
                preserve_runtime_evidence=True,
                repository_report_ids=frozenset({"r-release"}),
            )
            session.commit()

            refreshed = session.get(Product, product.id)
            self.assertEqual({item.id for item in refreshed.reports}, {"r-release", "r-runtime"})
            self.assertEqual({item.id for item in refreshed.videos}, {"v-release", "v-runtime"})
            self.assertEqual(
                {item.component for item in refreshed.bom_items},
                {"新版发布电池", "服务器新增电池"},
            )
            self.assertEqual(
                set(refreshed.payload["report_ids"]),
                {"r-release", "r-runtime"},
            )

    def test_excluded_source_removes_its_polymorphic_images(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(Report(id="excluded", payload={}))
            session.add(
                ImageAsset(
                    owner_type="report",
                    owner_id="excluded",
                    source_url="https://img.example.test/excluded.jpg",
                    object_key="images/excluded.webp",
                )
            )
            session.commit()
            _delete_excluded_source(
                session,
                owner_type="report",
                owner_id="excluded",
            )
            session.commit()
            self.assertIsNone(session.get(Report, "excluded"))
            self.assertEqual(session.scalar(select(func.count()).select_from(ImageAsset)), 0)

    def test_repository_import_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reports = root / "reports"
            products = root / "products"
            videos = root / "videos"
            roundups = root / "roundup_insights.json"
            reports.mkdir()
            products.mkdir()
            videos.mkdir()

            (reports / "r1.json").write_text(
                json.dumps(
                    {
                        "id": "r1",
                        "title": "Test teardown",
                        "url": "https://example.test/r1",
                        "published_at": "2026-08-10",
                        "brand": "Acme",
                        "model": "Pods",
                        "category": "真无线耳机TWS",
                        "summary": "summary",
                    }
                ),
                encoding="utf-8",
            )
            source_a = "https://img.example.test/a.jpg"
            source_b = "https://img.example.test/b.png"
            source_c = "https://img.example.test/roundup.png"
            (videos / "v1.json").write_text(
                json.dumps(
                    {
                        "id": "v1",
                        "title": "Test video",
                        "url": "https://example.test/v1",
                        "published_at": "2026-08-10",
                    }
                ),
                encoding="utf-8",
            )
            (products / "acme--pods.json").write_text(
                json.dumps(
                    {
                        "canonical_id": "acme--pods",
                        "brand": "Acme",
                        "model": "Pods",
                        "category": "真无线耳机TWS",
                        "first_seen": "2026-08-10",
                        "latest_published": "2026-08-10",
                        "report_ids": ["r1"],
                        "video_ids": ["v1"],
                        "cost_snapshot": {
                            "price_cny": 999,
                            "price_currency": "CNY",
                            "data_completeness": 0.8,
                        },
                        "technical_facts": [
                            {
                                "side": "耳机",
                                "component": "蓝牙音频SoC",
                                "brand": "Acme Semi",
                                "model": "A1",
                                "classification": "core",
                                "qty_hint": "1",
                                "fact_text": "A1蓝牙音频SoC采用12nm工艺。",
                                "parameters": [
                                    {
                                        "label": "制程",
                                        "value": "12nm",
                                        "evidence_quote": "采用12nm工艺",
                                    },
                                    {"label": "材料", "value": "硅"},
                                ],
                                "evidence": {
                                    "confidence": 0.9,
                                    "text": "A1蓝牙音频SoC采用12nm工艺。",
                                    "report_id": "r1",
                                },
                            }
                        ],
                        "summary_image_urls": [{"url": source_a, "caption": "summary"}],
                        "unboxing": {
                            "earbuds": {"appearance_images": [{"url": source_b, "alt": "earbuds"}]},
                            "packaging": {"appearance_images": [{"url": source_a, "alt": "shared"}]},
                        },
                    }
                ),
                encoding="utf-8",
            )
            roundups.write_text(
                json.dumps(
                    {
                        "reports": [
                            {
                                "id": "annual-2026",
                                "digest": {
                                    "image_highlights": [
                                        {"url": source_c, "caption": "annual chart"}
                                    ]
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            engine = create_engine("sqlite+pysqlite:///:memory:")
            Base.metadata.create_all(engine)
            with Session(engine) as session, patch(
                "server.importer.reports_dir", return_value=reports
            ), patch("server.importer.products_dir", return_value=products), patch(
                "server.importer.videos_dir", return_value=videos
            ), patch("server.importer.roundup_insights_path", return_value=roundups):
                first = import_repository(session)
                session.commit()
                second = import_repository(session)
                session.commit()

                self.assertEqual(first.products, 1)
                self.assertEqual(first.reports, 1)
                self.assertEqual(first.videos, 1)
                self.assertEqual(first.bom_items, 1)
                self.assertEqual(first.bom_parameters, 2)
                self.assertEqual(first.images, 4)
                self.assertEqual(second.invalid_files, 0)
                self.assertEqual(session.scalar(select(func.count()).select_from(Product)), 1)
                self.assertEqual(session.scalar(select(func.count()).select_from(Report)), 1)
                self.assertEqual(session.scalar(select(func.count()).select_from(Video)), 1)
                self.assertEqual(session.scalar(select(func.count()).select_from(BomItem)), 1)
                self.assertEqual(session.scalar(select(func.count()).select_from(BomItemParameter)), 2)
                # The same source URL can occur in more than one module; the
                # owner/source pair is stored once and shares one object key.
                self.assertEqual(session.scalar(select(func.count()).select_from(ImageAsset)), 3)
                product = session.get(Product, "acme--pods")
                bom_item = session.scalar(select(BomItem))
                self.assertEqual(bom_item.component_key, "bluetooth_audio_soc")
                # Preserve the upstream component brand as raw data, but do
                # not promote it into an analytical manufacturer unless the
                # source evidence contains the same name.
                self.assertEqual(bom_item.brand, "Acme Semi")
                self.assertEqual(bom_item.manufacturer, "")
                self.assertEqual(bom_item.manufacturer_basis, "unknown")
                # A model/rule-supplied material parameter is retained, but it
                # must not become an analysis material without a quote tying it
                # to the current component.
                self.assertEqual(bom_item.material, "")
                self.assertIn("硅", {parameter.value_text for parameter in bom_item.parameters})
                self.assertEqual(bom_item.source_report_id, "r1")
                self.assertEqual(bom_item.parameters[0].value_numeric, None)
                self.assertEqual(product.reports[0].id, "r1")
                self.assertEqual(product.videos[0].id, "v1")
                self.assertEqual(
                    session.scalar(select(ImageAsset.object_key).where(ImageAsset.source_url == source_a)),
                    image_object_key(source_a),
                )
                self.assertEqual(
                    session.scalar(select(ImageAsset.owner_type).where(ImageAsset.source_url == source_c)),
                    "roundup",
                )

                product.brand = "Live database edit"
                retained_image = session.scalar(
                    select(ImageAsset).where(ImageAsset.source_url == source_a)
                )
                retained_image.caption = "Human-corrected image caption"
                retained_image.alt_text = "æ\x8b\x86è§£æ\x8a¥å\x91\x8a"
                missing_image = session.scalar(
                    select(ImageAsset).where(ImageAsset.source_url == source_b)
                )
                session.delete(missing_image)
                session.flush()
                deployment_stats = import_deployment_seeds(session)
                session.commit()
                self.assertEqual(deployment_stats.images, 2)
                self.assertEqual(session.get(Product, "acme--pods").brand, "Live database edit")
                self.assertIsNotNone(
                    session.scalar(select(ImageAsset).where(ImageAsset.source_url == source_b))
                )
                retained_image = session.scalar(
                    select(ImageAsset).where(ImageAsset.source_url == source_a)
                )
                self.assertEqual(retained_image.caption, "Human-corrected image caption")
                self.assertEqual(retained_image.alt_text, "shared")


if __name__ == "__main__":
    unittest.main()
