from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from server.config import Settings
from server.image_cache import (
    fetch_and_sync_images,
    image_request_headers,
    image_sync_progress,
    priority_image_object_keys,
    requeue_missing_recovered_images,
    repair_legacy_image_url,
)
from server.models import Base, ImageAsset
from server.storage import LocalObjectStorage


def settings_for(root: Path) -> Settings:
    return Settings(
        root=root,
        database_url="",
        auto_migrate=True,
        import_on_start=True,
        initial_refresh_on_start=False,
        scheduler_enabled=False,
        scheduler_hour=9,
        scheduler_minute=10,
        scheduler_timezone="Asia/Shanghai",
        scheduler_misfire_grace_seconds=21600,
        crawl_with_ai=False,
        refresh_static_site=True,
        storage_backend="local",
        local_storage_path=root / "objects",
        minio_endpoint="",
        minio_port=9000,
        minio_use_ssl=False,
        minio_access_key="",
        minio_secret_key="",
        minio_bucket="images",
        site_dir=root / "site",
        image_cache_dir=root / "cache",
        public_base_path="/",
        public_image_base="/media",
    )


class PriorityImageTests(unittest.TestCase):
    def test_legacy_url_and_sina_referer_are_repaired(self) -> None:
        mojibake = "https://example.test/\u00e5\u008d\u008e\u00e7\u00b1\u00b3.jpg"
        self.assertEqual(
            repair_legacy_image_url(mojibake),
            "https://example.test/\u534e\u7c73.jpg",
        )
        self.assertEqual(
            image_request_headers("https://wx1.sinaimg.cn/large/example.jpg")["Referer"],
            "https://weibo.com/",
        )
        self.assertEqual(
            image_request_headers("https://www.52audio.com/example.jpg")["Referer"],
            "https://www.52audio.com/",
        )
        self.assertEqual(
            image_request_headers("http://note.youdao.com/yws/example")["Referer"],
            "https://note.youdao.com/",
        )

    def test_manifests_select_card_and_roundup_images(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = root / "web" / "public" / "data"
            (data / "products").mkdir(parents=True)
            (data / "products" / "index.json").write_text(
                json.dumps({"products": [{"card_image_path": "/media/aaaaaaaaaaaaaaaa.webp"}]}),
                encoding="utf-8",
            )
            (data / "roundup_insights.json").write_text(
                json.dumps(
                    {
                        "reports": [
                            {
                                "digest": {
                                    "image_highlights": [
                                        {"local_path": "/images/bbbbbbbbbbbbbbbb.webp"}
                                    ]
                                }
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                priority_image_object_keys(root),
                {"images/aaaaaaaaaaaaaaaa.webp", "images/bbbbbbbbbbbbbbbb.webp"},
            )

    def test_backfill_excludes_priority_images(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = root / "web" / "public" / "data"
            (data / "products").mkdir(parents=True)
            (data / "products" / "index.json").write_text(
                json.dumps({"products": [{"card_image_path": "/media/aaaaaaaaaaaaaaaa.webp"}]}),
                encoding="utf-8",
            )
            (data / "roundup_insights.json").write_text('{"reports":[]}', encoding="utf-8")
            settings = settings_for(root)
            settings.image_cache_dir.mkdir()
            (settings.image_cache_dir / "bbbbbbbbbbbbbbbb.webp").write_bytes(b"cached-image")
            engine = create_engine("sqlite+pysqlite:///:memory:")
            Base.metadata.create_all(engine)
            storage = LocalObjectStorage(settings.local_storage_path)
            with Session(engine) as session:
                priority = ImageAsset(
                    owner_type="product",
                    owner_id="priority",
                    image_kind="summary",
                    source_url="https://example.test/a.png",
                    object_key="images/aaaaaaaaaaaaaaaa.webp",
                    storage_status="pending",
                )
                detail = ImageAsset(
                    owner_type="product",
                    owner_id="detail",
                    image_kind="summary",
                    source_url="https://example.test/b.png",
                    object_key="images/bbbbbbbbbbbbbbbb.webp",
                    storage_status="pending",
                )
                session.add_all([priority, detail])
                session.flush()
                stats = fetch_and_sync_images(
                    session,
                    settings,
                    priority_only=False,
                    backfill_limit=10,
                    storage=storage,
                )
                self.assertEqual(stats.selected, 1)
                self.assertEqual(priority.storage_status, "pending")
                self.assertEqual(detail.storage_status, "ready")
                self.assertTrue(storage.exists(detail.object_key))

                (settings.image_cache_dir / "aaaaaaaaaaaaaaaa.webp").write_bytes(
                    b"priority-image"
                )
                full_stats = fetch_and_sync_images(
                    session,
                    settings,
                    priority_only=False,
                    include_priority=True,
                    backfill_limit=10,
                    storage=storage,
                )
                self.assertEqual(full_stats.selected, 1)
                self.assertEqual(priority.storage_status, "ready")

    def test_historical_seed_is_uploaded_without_redownloading(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = root / "web" / "public" / "data"
            (data / "products").mkdir(parents=True)
            (data / "products" / "index.json").write_text(
                '{"products":[]}', encoding="utf-8"
            )
            (data / "roundup_insights.json").write_text('{"reports":[]}', encoding="utf-8")
            seed_dir = root / "seed"
            seed_dir.mkdir()
            seed_name = "cccccccccccccccc.webp"
            (seed_dir / seed_name).write_bytes(b"versioned-webp")
            settings = replace(settings_for(root), image_seed_dir=seed_dir)
            engine = create_engine("sqlite+pysqlite:///:memory:")
            Base.metadata.create_all(engine)
            storage = LocalObjectStorage(settings.local_storage_path)
            with Session(engine) as session:
                image = ImageAsset(
                    owner_type="product",
                    owner_id="history",
                    image_kind="detail",
                    source_url="https://unreachable.test/history.png",
                    object_key=f"images/{seed_name}",
                    storage_status="pending",
                )
                session.add(image)
                session.flush()
                stats = fetch_and_sync_images(
                    session,
                    settings,
                    priority_only=False,
                    backfill_limit=10,
                    storage=storage,
                )
                self.assertEqual(stats.seeded, 1)
                self.assertEqual(stats.uploaded, 1)
                self.assertEqual(stats.failed, 0)
                self.assertEqual(image.storage_status, "ready")

    def test_progress_counts_unique_objects(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add_all(
                [
                    ImageAsset(
                        owner_type="product",
                        owner_id="one",
                        image_kind="detail",
                        source_url="https://example.test/shared-a.png",
                        object_key="images/dddddddddddddddd.webp",
                        storage_status="ready",
                    ),
                    ImageAsset(
                        owner_type="report",
                        owner_id="two",
                        image_kind="detail",
                        source_url="https://example.test/shared-b.png",
                        object_key="images/dddddddddddddddd.webp",
                        storage_status="pending",
                    ),
                    ImageAsset(
                        owner_type="product",
                        owner_id="three",
                        image_kind="detail",
                        source_url="https://example.test/failed.png",
                        object_key="images/eeeeeeeeeeeeeeee.webp",
                        storage_status="failed",
                    ),
                ]
            )
            session.flush()
            progress = image_sync_progress(session)
            self.assertEqual(progress.total, 2)
            self.assertEqual(progress.ready, 1)
            self.assertEqual(progress.failed, 1)
            self.assertEqual(progress.remaining, 1)

    def test_missing_recovered_object_is_requeued_even_when_database_says_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "data" / "seed" / "recovered_image_keys.json"
            manifest.parent.mkdir(parents=True)
            object_key = "images/3333333333333333.webp"
            manifest.write_text(json.dumps([object_key]), encoding="utf-8")
            settings = settings_for(root)
            storage = LocalObjectStorage(settings.local_storage_path)
            engine = create_engine("sqlite+pysqlite:///:memory:")
            Base.metadata.create_all(engine)
            with Session(engine) as session:
                image = ImageAsset(
                    owner_type="product",
                    owner_id="recovered",
                    image_kind="detail",
                    source_url="https://example.test/recovered.png",
                    object_key=object_key,
                    storage_status="ready",
                )
                session.add(image)
                session.flush()
                result = requeue_missing_recovered_images(session, settings, storage)
                self.assertEqual(result["missing"], 1)
                self.assertEqual(result["requeued_rows"], 1)
                self.assertEqual(image.storage_status, "pending")

    def test_failed_keys_can_be_excluded_from_the_next_retry_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = root / "web" / "public" / "data"
            (data / "products").mkdir(parents=True)
            (data / "products" / "index.json").write_text(
                '{"products":[]}', encoding="utf-8"
            )
            (data / "roundup_insights.json").write_text(
                '{"reports":[]}', encoding="utf-8"
            )
            settings = settings_for(root)
            engine = create_engine("sqlite+pysqlite:///:memory:")
            Base.metadata.create_all(engine)
            storage = LocalObjectStorage(settings.local_storage_path)
            first_key = "images/1111111111111111.webp"
            second_key = "images/2222222222222222.webp"
            with Session(engine) as session:
                session.add_all(
                    [
                        ImageAsset(
                            owner_type="product",
                            owner_id="first",
                            image_kind="detail",
                            source_url="https://unreachable.test/first.png",
                            object_key=first_key,
                            storage_status="failed",
                        ),
                        ImageAsset(
                            owner_type="product",
                            owner_id="second",
                            image_kind="detail",
                            source_url="https://unreachable.test/second.png",
                            object_key=second_key,
                            storage_status="failed",
                        ),
                    ]
                )
                session.flush()
                with patch(
                    "server.image_cache._download_image",
                    side_effect=RuntimeError("unreachable"),
                ):
                    stats = fetch_and_sync_images(
                        session,
                        settings,
                        priority_only=False,
                        include_priority=True,
                        backfill_limit=10,
                        exclude_object_keys={first_key},
                        storage=storage,
                    )
                self.assertEqual(stats.selected, 1)
                self.assertEqual(stats.failed_object_keys, {second_key})


if __name__ == "__main__":
    unittest.main()
