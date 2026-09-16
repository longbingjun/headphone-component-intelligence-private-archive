"""从 content_html 提取 3–5 张关键拆解图 URL（仅存链接，不下载二进制）。"""

from __future__ import annotations

import re

from core.extract.text_utils import parse_content_blocks
from sources.audio52 import lexicon

_SECTION_HINTS: list[tuple[str, list[str], int]] = [
    ("主板", lexicon.KEY_IMAGE_KEYWORDS.get("pcb", []), 10),
    ("电池", lexicon.KEY_IMAGE_KEYWORDS.get("battery", []), 9),
    ("爆炸图", lexicon.KEY_IMAGE_KEYWORDS.get("exploded", []), 8),
    ("包装", lexicon.KEY_IMAGE_KEYWORDS.get("packaging", []), 5),
    ("整机", lexicon.KEY_IMAGE_KEYWORDS.get("overview", []), 4),
]

_MIN_SCORE = 2
_MAX_IMAGES = 5


def _score_image(alt: str, caption: str, heading: str | None) -> tuple[int, str]:
    text = f"{heading or ''} {alt} {caption}".strip()
    if not text:
        return 0, ""
    best_score = 0
    best_label = ""
    for label, keywords, weight in _SECTION_HINTS:
        hits = sum(1 for kw in keywords if kw.lower() in text.lower() or kw in text)
        if hits:
            score = hits * weight
            if score > best_score:
                best_score = score
                best_label = label
    return best_score, best_label


def extract_key_image_urls(content_html: str, *, min_count: int = 3, max_count: int = _MAX_IMAGES) -> list[dict]:
    """返回 [{url, alt, caption, section_hint, score}, ...]，按相关性降序。"""
    if not content_html:
        return []

    blocks = parse_content_blocks(content_html)
    current_heading: str | None = None
    scored: list[tuple[int, dict]] = []
    seen_urls: set[str] = set()

    for block in blocks:
        if block.kind == "heading":
            current_heading = block.text
        elif block.kind == "image" and block.img_url:
            url = block.img_url.strip()
            if not url or url in seen_urls:
                continue
            if re.search(r"logo|qrcode|二维码|icon|avatar", block.img_alt + block.text, re.I):
                continue
            score, label = _score_image(block.img_alt, block.text, current_heading)
            if score < _MIN_SCORE:
                continue
            seen_urls.add(url)
            scored.append(
                (
                    score,
                    {
                        "url": url,
                        "alt": block.img_alt,
                        "caption": block.text,
                        "section_hint": label,
                        "score": score,
                    },
                )
            )

    scored.sort(key=lambda x: x[0], reverse=True)
    picked = [item for _, item in scored[:max_count]]
    if len(picked) < min_count:
        for block in blocks:
            if block.kind != "image" or not block.img_url:
                continue
            url = block.img_url.strip()
            if url in seen_urls:
                continue
            if re.search(r"logo|qrcode|二维码", block.img_alt + block.text, re.I):
                continue
            seen_urls.add(url)
            picked.append(
                {
                    "url": url,
                    "alt": block.img_alt,
                    "caption": block.text,
                    "section_hint": "其他",
                    "score": 1,
                }
            )
            if len(picked) >= min_count:
                break
    return picked[:max_count]
