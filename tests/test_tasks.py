from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.config import Settings
from server.tasks import _build_site_atomically


class AtomicSiteBuildTests(unittest.TestCase):
    def test_successful_build_replaces_live_site(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "web").mkdir()
            site = root / "site"
            site.mkdir()
            (site / "index.html").write_text("old", encoding="utf-8")
            settings = Settings(
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
                site_dir=site,
                image_cache_dir=root / "cache",
                public_base_path="/",
                public_image_base="/media",
            )

            def fake_build(command, *, cwd, env):
                del command, cwd
                output = Path(env["ASTRO_OUT_DIR"])
                (output / "_astro").mkdir(parents=True)
                (output / "_astro" / "app.css").write_text("body{}", encoding="utf-8")
                (output / "index.html").write_text(
                    '<link rel="stylesheet" href="/_astro/app.css">new', encoding="utf-8"
                )
                return ""

            with patch("server.tasks._run", side_effect=fake_build), patch(
                "server.tasks._npm", return_value="npm"
            ):
                _build_site_atomically(settings)

            self.assertTrue((site / "index.html").read_text(encoding="utf-8").endswith("new"))
            self.assertEqual(list(root.glob(".site-*")), [])


if __name__ == "__main__":
    unittest.main()
