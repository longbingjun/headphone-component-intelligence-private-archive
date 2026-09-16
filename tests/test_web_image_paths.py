from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from scripts.prepare_web_data import _local_image_path


class WebImagePathTests(unittest.TestCase):
    def test_default_path_keeps_github_pages_compatibility(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertRegex(_local_image_path("https://example.test/a.jpg"), r"^/images/[a-f0-9]{16}\.webp$")

    def test_minio_proxy_path_is_configurable(self) -> None:
        with patch.dict(os.environ, {"PUBLIC_IMAGE_BASE": "/media"}, clear=False):
            self.assertRegex(_local_image_path("https://example.test/a.jpg"), r"^/media/[a-f0-9]{16}\.webp$")


if __name__ == "__main__":
    unittest.main()
