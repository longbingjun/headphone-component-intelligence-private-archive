from __future__ import annotations

import unittest
from unittest.mock import Mock

import requests

from sources.audio52.source_v2 import Audio52SourceV2


class Audio52SourceV2Tests(unittest.TestCase):
    def test_article_html_uses_utf8_bytes_when_header_has_no_charset(self) -> None:
        response = requests.Response()
        response.status_code = 200
        response.headers["Content-Type"] = "text/html"
        response.encoding = "ISO-8859-1"
        response._content = (
            '<html><body><div class="entry-content">'
            "<h2>一、开箱</h2><p>小鸟开放式耳机包装设计</p>"
            "</div></body></html>"
        ).encode("utf-8")

        source = Audio52SourceV2()
        source.session.get = Mock(return_value=response)

        html = source.fetch_article_html("https://www.52audio.com/archives/1.html")

        self.assertIn("一、开箱", html)
        self.assertIn("小鸟开放式耳机包装设计", html)
        self.assertNotIn("å°", html)


if __name__ == "__main__":
    unittest.main()
