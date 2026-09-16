from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from server.catalog_reconciliation import (
    CanonicalCatalog,
    MIGRATION_ID,
    build_reconciliation_plan,
    reconcile_release_catalog,
)
from server.models import (
    Base,
    BomItem,
    BomItemParameter,
    DataMigration,
    ImageAsset,
    Product,
    Report,
    Video,
    VideoFact,
)
from server.release_validation import validate_release_data


class CatalogReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    @staticmethod
    def catalog() -> CanonicalCatalog:
        return CanonicalCatalog(
            payloads={
                "vendor--new-model": {
                    "canonical_id": "vendor--new-model",
                    "brand": "Vendor",
                    "model": "New Model",
                    "category": "耳夹式耳机",
                    "report_ids": ["r1"],
                    "video_ids": ["v1"],
                }
            },
            report_targets={"r1": frozenset({"vendor--new-model"})},
            video_targets={"v1": frozenset({"vendor--new-model"})},
        )

    def seed_old_identity(self, session: Session) -> None:
        report = Report(id="r1", payload={"id": "r1"})
        video = Video(
            id="v1",
            candidate_product_id="unknown--old-model",
            matched_product_id="unknown--old-model",
            payload={"id": "v1"},
        )
        source = Product(
            id="unknown--old-model",
            brand="Unknown",
            model="Old Model",
            category="开放式耳机",
            payload={
                "canonical_id": "unknown--old-model",
                "report_ids": ["r1"],
                "video_ids": ["v1"],
            },
        )
        source.reports.append(report)
        source.videos.append(video)
        session.add_all([source, report, video])
        session.flush()
        session.add(
            VideoFact(
                video_id="v1",
                product_id=source.id,
                segment_id=1,
                fact_text="有证据的视频事实",
                status="published",
            )
        )
        report_bom = BomItem(
            product_id=source.id,
            ordinal=0,
            component="电池",
            component_key="battery",
            source_type="report",
            source_report_id="r1",
        )
        video_bom = BomItem(
            product_id=source.id,
            ordinal=1,
            component="主控",
            component_key="bluetooth_audio_soc",
            model="ABC1",
            source_type="video",
            source_video_id="v1",
            source_video_fact_id=1,
        )
        session.add_all([report_bom, video_bom])
        session.flush()
        session.add(BomItemParameter(bom_item_id=report_bom.id, label="容量", value_text="50mAh"))
        session.add(
            ImageAsset(
                owner_type="product",
                owner_id=source.id,
                image_kind="summary",
                source_url="https://images.test/a.jpg",
                object_key="images/aaaaaaaaaaaaaaaa.webp",
                storage_status="ready",
            )
        )
        session.commit()

    def test_dry_run_is_read_only(self) -> None:
        with Session(self.engine) as session:
            self.seed_old_identity(session)
            result = reconcile_release_catalog(session, apply=False, catalog=self.catalog())
            session.commit()
            self.assertEqual(result.plan["planned_identity_moves"], 1)
            self.assertIsNotNone(session.get(Product, "unknown--old-model"))
            self.assertIsNone(session.get(Product, "vendor--new-model"))
            self.assertIsNone(session.get(DataMigration, MIGRATION_ID))

    def test_apply_moves_evidence_and_is_idempotent(self) -> None:
        with Session(self.engine) as session:
            self.seed_old_identity(session)

            def import_target(active_session: Session, **_kwargs):
                target = Product(
                    id="vendor--new-model",
                    brand="Vendor",
                    model="New Model",
                    category="耳夹式耳机",
                    payload=dict(self.catalog().payloads["vendor--new-model"]),
                )
                active_session.add(target)
                active_session.flush()
                return SimpleNamespace(to_dict=lambda: {"products": 1})

            with patch(
                "server.catalog_reconciliation.import_repository",
                side_effect=import_target,
            ):
                result = reconcile_release_catalog(
                    session,
                    apply=True,
                    catalog=self.catalog(),
                    source_commit="deadbeef",
                )
                session.commit()

            self.assertEqual(result.moved_products, 1)
            self.assertIsNone(session.get(Product, "unknown--old-model"))
            target = session.get(Product, "vendor--new-model")
            self.assertEqual([item.id for item in target.reports], ["r1"])
            self.assertEqual([item.id for item in target.videos], ["v1"])
            video = session.get(Video, "v1")
            self.assertEqual(video.matched_product_id, target.id)
            self.assertEqual(video.candidate_product_id, target.id)
            self.assertEqual(session.scalar(select(VideoFact.product_id)), target.id)
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(BomItem).where(BomItem.source_type == "report")
                ),
                0,
            )
            self.assertEqual(
                session.scalar(select(BomItem.product_id).where(BomItem.source_type == "video")),
                target.id,
            )
            self.assertEqual(
                session.scalar(select(ImageAsset.owner_id)),
                target.id,
            )
            self.assertIsNotNone(session.get(DataMigration, MIGRATION_ID))

            validation = validate_release_data(session, catalog=self.catalog())
            self.assertTrue(validation["passed"], validation)

            second = reconcile_release_catalog(session, apply=True, catalog=self.catalog())
            self.assertTrue(second.already_applied)
            self.assertEqual(second.moved_products, 0)

    def test_ambiguous_and_runtime_only_products_are_not_moved(self) -> None:
        catalog = CanonicalCatalog(
            payloads={"a": {}, "b": {}},
            report_targets={"r1": frozenset({"a", "b"})},
            video_targets={},
        )
        with Session(self.engine) as session:
            report = Report(id="r1", payload={"id": "r1"})
            ambiguous = Product(id="old", payload={"report_ids": ["r1"]})
            incremental = Product(id="runtime-only", payload={})
            ambiguous.reports.append(report)
            session.add_all([ambiguous, incremental])
            session.commit()
            plan = build_reconciliation_plan(session, catalog)
            self.assertEqual(plan.conflicts, {"old": ["a", "b"]})
            self.assertEqual(plan.preserved_runtime_products, ["runtime-only"])

            def import_targets(active_session: Session, **_kwargs):
                active_session.add_all([Product(id="a", payload={}), Product(id="b", payload={})])
                active_session.flush()
                return SimpleNamespace(to_dict=lambda: {"products": 2})

            with patch(
                "server.catalog_reconciliation.import_repository",
                side_effect=import_targets,
            ):
                result = reconcile_release_catalog(
                    session,
                    apply=True,
                    catalog=catalog,
                    source_commit="test-commit",
                )
                session.commit()
            self.assertEqual(result.unresolved_identity_conflicts, 1)
            self.assertIsNotNone(session.get(Product, "old"))
            self.assertIsNotNone(session.get(Product, "runtime-only"))
            marker = session.get(DataMigration, MIGRATION_ID)
            self.assertEqual(marker.status, "applied_with_conflicts")

    def test_repository_known_noncanonical_product_is_retired(self) -> None:
        catalog = CanonicalCatalog(
            payloads={"reviewed--product": {}},
            report_targets={},
            video_targets={},
            repository_report_ids=frozenset({"article-1"}),
        )
        with Session(self.engine) as session:
            report = Report(id="article-1", payload={"id": "article-1"})
            legacy = Product(id="legacy--article-as-product", category="真无线耳机TWS", payload={})
            legacy.reports.append(report)
            session.add_all([legacy, report])
            session.commit()

            plan = build_reconciliation_plan(session, catalog)
            self.assertEqual(plan.retired_legacy_products, [legacy.id])
            self.assertEqual(plan.preserved_runtime_products, [])


if __name__ == "__main__":
    unittest.main()
