from __future__ import annotations

import unittest
import threading
from pathlib import Path
from unittest.mock import patch

from server.config import Settings
from server.scheduler import start_scheduler


class SchedulerTests(unittest.TestCase):
    @staticmethod
    def _settings(**overrides) -> Settings:
        values = dict(
            root=Path("."),
            database_url="sqlite+pysqlite:///:memory:",
            auto_migrate=True,
            import_on_start=True,
            initial_refresh_on_start=True,
            scheduler_enabled=True,
            scheduler_hour=9,
            scheduler_minute=10,
            scheduler_timezone="Asia/Shanghai",
            scheduler_misfire_grace_seconds=21600,
            crawl_with_ai=False,
            refresh_static_site=True,
            storage_backend="local",
            local_storage_path=Path("runtime/objects"),
            minio_endpoint="",
            minio_port=9000,
            minio_use_ssl=False,
            minio_access_key="",
            minio_secret_key="",
            minio_bucket="images",
            site_dir=Path("site"),
            image_cache_dir=Path("images"),
            public_base_path="/",
            public_image_base="/media",
        )
        values.update(overrides)
        return Settings(**values)

    def test_base_refresh_is_scheduled(self) -> None:
        settings = self._settings()
        scheduler = start_scheduler(settings)
        self.assertIsNotNone(scheduler)
        try:
            self.assertEqual([job.id for job in scheduler.get_jobs()], ["daily-research-refresh"])
        finally:
            scheduler.shutdown(wait=False)

    def test_short_interval_uses_the_same_refresh_job_in_test_mode(self) -> None:
        scheduler = start_scheduler(self._settings(scheduler_test_interval_seconds=60))
        self.assertIsNotNone(scheduler)
        try:
            jobs = scheduler.get_jobs()
            self.assertEqual([job.id for job in jobs], ["test-interval-research-refresh"])
            self.assertIn("0:01:00", str(jobs[0].trigger))
        finally:
            scheduler.shutdown(wait=False)

    def test_short_interval_actually_invokes_refresh(self) -> None:
        called = threading.Event()
        with patch("server.scheduler.run_refresh", side_effect=lambda **_: called.set()):
            scheduler = start_scheduler(self._settings(scheduler_test_interval_seconds=1))
            self.assertIsNotNone(scheduler)
            try:
                self.assertTrue(called.wait(3), "scheduled refresh was not invoked")
            finally:
                scheduler.shutdown(wait=False)


if __name__ == "__main__":
    unittest.main()
