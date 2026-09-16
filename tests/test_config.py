from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from server.config import get_settings, load_local_env, normalize_database_url


class ConfigTests(unittest.TestCase):
    def test_platform_postgresql_url_uses_psycopg3(self) -> None:
        self.assertEqual(
            normalize_database_url("postgresql://user:pass@db:5432/app"),
            "postgresql+psycopg://user:pass@db:5432/app",
        )

    def test_explicit_driver_is_preserved(self) -> None:
        value = "postgresql+psycopg://user:pass@db:5432/app"
        self.assertEqual(normalize_database_url(value), value)

    def test_server_defaults_do_not_mutate_runtime_data_on_start(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            get_settings.cache_clear()
            settings = get_settings()
            self.assertFalse(settings.auto_migrate)
            self.assertFalse(settings.import_on_start)
            self.assertFalse(settings.initial_refresh_on_start)
            self.assertTrue(settings.scheduler_enabled)
            self.assertFalse(settings.scheduler_run_in_web)
            self.assertFalse(settings.initial_image_sync_on_start)
            self.assertFalse(settings.full_image_sync_on_start)
            self.assertFalse(settings.rebuild_site_on_start)
        get_settings.cache_clear()

    def test_image_ready_ratio_rejects_nan(self) -> None:
        with patch.dict("os.environ", {"IMAGE_MIN_READY_RATIO": "NaN"}, clear=True):
            get_settings.cache_clear()
            with self.assertRaises(ValueError):
                get_settings()
        get_settings.cache_clear()

    def test_scheduler_test_interval_rejects_negative_values(self) -> None:
        with patch.dict(
            "os.environ", {"SCHEDULER_TEST_INTERVAL_SECONDS": "-1"}, clear=True
        ):
            get_settings.cache_clear()
            with self.assertRaises(ValueError):
                get_settings()
        get_settings.cache_clear()

    def test_local_env_does_not_override_injected_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / ".env.local"
            path.write_text(
                "VIDEO_COOKIES_FILE=D:\\local\\cookies.txt\nDEFAULT_MODEL_API_KEY=file-key\n",
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"DEFAULT_MODEL_API_KEY": "injected-key"}, clear=True):
                load_local_env(path)
                self.assertEqual(
                    __import__("os").environ["VIDEO_COOKIES_FILE"],
                    "D:\\local\\cookies.txt",
                )
                self.assertEqual(
                    __import__("os").environ["DEFAULT_MODEL_API_KEY"], "injected-key"
                )


if __name__ == "__main__":
    unittest.main()
