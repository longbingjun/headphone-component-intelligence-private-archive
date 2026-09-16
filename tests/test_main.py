from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

from core.release_metadata import DATA_CONTRACT_VERSION, EXPECTED_ALEMBIC_HEAD
from server.main import run_startup_jobs, version


class StartupJobTests(unittest.TestCase):
    def test_version_requires_matching_commit_schema_and_generated_data(self) -> None:
        session = MagicMock()
        session.execute.return_value.scalar.return_value = EXPECTED_ALEMBIC_HEAD
        published = {
            "schema_version": EXPECTED_ALEMBIC_HEAD,
            "contract_version": DATA_CONTRACT_VERSION,
            "data_version": "20260915T000000Z-deadbeef",
            "generated_at": "2026-09-15T00:00:00+08:00",
        }
        with patch("server.main.get_settings", return_value=SimpleNamespace()), patch(
            "server.main._published_release", return_value=published
        ), patch("server.main.RELEASE_COMMIT", "deadbeef"):
            result = version(session)
        self.assertTrue(result["versions_match"])
        self.assertEqual(result["commit_sha"], "deadbeef")

    def test_image_bootstrap_precedes_initial_refresh(self) -> None:
        calls: list[str] = []
        with patch(
            "server.main.run_image_bootstrap", side_effect=lambda: calls.append("images")
        ), patch(
            "server.main.run_full_image_backfill", side_effect=lambda: calls.append("full")
        ), patch(
            "server.main.run_refresh", side_effect=lambda **kwargs: calls.append(kwargs["trigger"])
        ), patch("server.main.rebuild_site_from_database"):
            run_startup_jobs(
                object(),
                rebuild_site=False,
                sync_images=True,
                full_sync_images=True,
                initial_refresh=True,
            )
        self.assertEqual(calls, ["images", "full", "startup"])

    def test_disabled_startup_jobs_do_not_run(self) -> None:
        with patch("server.main.run_image_bootstrap") as images, patch(
            "server.main.run_refresh"
        ) as refresh, patch("server.main.rebuild_site_from_database") as rebuild, patch(
            "server.main.run_full_image_backfill"
        ) as full_images:
            run_startup_jobs(
                object(),
                rebuild_site=False,
                sync_images=False,
                full_sync_images=False,
                initial_refresh=False,
            )
        images.assert_not_called()
        full_images.assert_not_called()
        refresh.assert_not_called()
        rebuild.assert_not_called()


if __name__ == "__main__":
    unittest.main()
