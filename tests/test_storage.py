from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from server.config import Settings
from server.models import Base, ImageAsset
from server.storage import LocalObjectStorage, image_sync_required, minio_connection_target


class LocalStorageTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.webp"
            source.write_bytes(b"image-bytes")
            storage = LocalObjectStorage(root / "objects")
            storage.ensure_ready()
            storage.put_path("images/abc.webp", source, "image/webp")
            self.assertTrue(storage.exists("images/abc.webp"))
            chunks, close = storage.open("images/abc.webp")
            try:
                self.assertEqual(b"".join(chunks), b"image-bytes")
            finally:
                close()

    def test_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            storage = LocalObjectStorage(Path(temp) / "objects")
            with self.assertRaises(ValueError):
                storage.exists("../escape.webp")

    def test_missing_object_invalidates_stale_ready_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            storage = LocalObjectStorage(Path(temp) / "objects")
            storage.ensure_ready()
            engine = create_engine("sqlite+pysqlite:///:memory:")
            Base.metadata.create_all(engine)
            with Session(engine) as session:
                session.add_all(
                    [
                        ImageAsset(
                            owner_type="roundup",
                            owner_id="annual",
                            image_kind="roundup_highlight",
                            source_url="https://example.test/a.png",
                            object_key="images/a.webp",
                            storage_status="ready",
                        ),
                        ImageAsset(
                            owner_type="product",
                            owner_id="product",
                            image_kind="summary",
                            source_url="https://example.test/b.png",
                            object_key="images/b.webp",
                            storage_status="ready",
                        ),
                    ]
                )
                session.flush()
                required, details = image_sync_required(session, storage)
                self.assertTrue(required)
                self.assertGreater(details["sample_missing"], 0)
                self.assertEqual({row.storage_status for row in session.query(ImageAsset)}, {"pending"})

    def test_failed_product_image_is_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            storage = LocalObjectStorage(Path(temp) / "objects")
            storage.ensure_ready()
            engine = create_engine("sqlite+pysqlite:///:memory:")
            Base.metadata.create_all(engine)
            with Session(engine) as session:
                session.add(
                    ImageAsset(
                        owner_type="product",
                        owner_id="product",
                        image_kind="summary",
                        source_url="https://example.test/failed.png",
                        object_key="images/failed.webp",
                        storage_status="failed",
                    )
                )
                session.flush()
                required, details = image_sync_required(session, storage)
                self.assertTrue(required)
                self.assertEqual(details["not_ready"], 1)


class MinioSettingsTests(unittest.TestCase):
    @staticmethod
    def _settings(endpoint: str, port: int = 9000, ssl: bool = False) -> Settings:
        return Settings(
            root=Path("."), database_url="", auto_migrate=True, import_on_start=True,
            initial_refresh_on_start=True, scheduler_enabled=True, scheduler_hour=9,
            scheduler_minute=10, scheduler_timezone="Asia/Shanghai",
            scheduler_misfire_grace_seconds=21600, crawl_with_ai=False,
            refresh_static_site=True, storage_backend="minio",
            local_storage_path=Path("runtime/objects"), minio_endpoint=endpoint,
            minio_port=port, minio_use_ssl=ssl, minio_access_key="key",
            minio_secret_key="secret", minio_bucket="images", site_dir=Path("site"),
            image_cache_dir=Path("images"), public_base_path="/", public_image_base="/media",
        )

    def test_host_gets_configured_port(self) -> None:
        self.assertEqual(minio_connection_target(self._settings("minio")), ("minio:9000", False))

    def test_url_controls_tls_and_preserves_port(self) -> None:
        self.assertEqual(
            minio_connection_target(self._settings("https://objects.internal:9443")),
            ("objects.internal:9443", True),
        )


if __name__ == "__main__":
    unittest.main()
