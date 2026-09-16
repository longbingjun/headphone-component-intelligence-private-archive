from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from server.static_site import inspect_static_site


class StaticSiteIntegrityTests(unittest.TestCase):
    def test_missing_referenced_asset_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "index.html").write_text(
                '<link rel="stylesheet" href="/_astro/app.css">', encoding="utf-8"
            )
            state = inspect_static_site(root)
            self.assertFalse(state.ready)
            self.assertEqual(state.missing, ("_astro/app.css",))

    def test_complete_site_is_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "_astro").mkdir()
            (root / "_astro" / "app.js").write_text("", encoding="utf-8")
            (root / "index.html").write_text(
                '<script type="module" src="/_astro/app.js"></script>', encoding="utf-8"
            )
            state = inspect_static_site(root)
            self.assertTrue(state.ready)
            self.assertEqual(state.references, 1)

    def test_wrong_public_base_path_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "_astro").mkdir()
            (root / "_astro" / "app.js").write_text("", encoding="utf-8")
            (root / "index.html").write_text(
                '<script type="module" src="/headphone-component-intelligence/_astro/app.js"></script>',
                encoding="utf-8",
            )
            state = inspect_static_site(root, expected_base_path="/")
            self.assertFalse(state.ready)
            self.assertEqual(state.reason, "base_path_mismatch")
            self.assertEqual(state.observed_base_paths, ("/headphone-component-intelligence/",))


if __name__ == "__main__":
    unittest.main()
