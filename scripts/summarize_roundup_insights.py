"""Create cached, source-grounded image-and-text digests for roundup reports.

Every source article is re-fetched to detect edits.  DeepSeek is called only
for a report without a successful cache entry, or whose text/image fingerprint
has changed.  A failed call never discards the last successful digest: it is
recorded next to it so the next scheduled run can retry safely.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.ingest import load_all_records  # noqa: E402

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-v4-flash"
CACHE_DIR = ROOT / "data" / "staging" / "roundup_summaries"
LEGACY_CACHE_DIR = ROOT / "data" / "enrich" / "roundup_summaries"
# 汇总文的正文与图注都应进入模型上下文；上限仅防异常页面无限膨胀。
MAX_ARTICLE_CHARS = 80_000
MAX_IMAGES = 40
CACHE_SCHEMA_VERSION = 2
ROUNDUP_MARKERS = ("汇总", "盘点", "年度报告", "给你答案")
HEADPHONE_MARKERS = ("耳机", "TWS", "OWS", "蓝牙")

SYSTEM_PROMPT = """你是消费电子行业研究编辑。请基于一篇我爱音频网汇总文章的全文和文章图片的
标题/图注/附近文字，为企业竞品研究工作台写中文摘要。只能陈述输入明确支持的事实，不得补充
产品规格、销量、结论或看不见图片中的细节。提炼文章覆盖范围、可用于决策的共同趋势和代表性
样本；不逐条复述所有产品。返回 JSON：
{"overview":"120-260字概述","key_findings":[{"title":"小标题","text":"60-140字事实性发现"}],
"image_highlights":[{"source_index":0,"caption":"12-50字，说明该原文图片在文章中展示的内容"}]}
key_findings 保留 4-6 条；image_highlights 选择 2-4 条（原文确有合适图片时），source_index 必须引用给定 images 数组。
"""


def is_roundup(record: dict) -> bool:
    title = str(record.get("title") or record.get("product_title") or "")
    lower = title.lower()
    return any(marker in title for marker in ROUNDUP_MARKERS) and any(marker.lower() in lower for marker in HEADPHONE_MARKERS)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _article_root(soup: BeautifulSoup) -> Tag:
    candidates = soup.select("article, .entry-content, .post-content, .article-content, .content, main")
    return max(candidates, key=lambda node: len(node.get_text(" ", strip=True)), default=soup.body or soup)


def fetch_article(url: str) -> tuple[str, list[dict]]:
    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; 52audio-roundup-digest/1.0)"},
        timeout=35,
    )
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    soup = BeautifulSoup(response.text, "html.parser")
    root = _article_root(soup)
    for node in root.select("script, style, noscript, svg, nav, footer, form"):
        node.decompose()
    text = _clean_text(root.get_text(" ", strip=True))[:MAX_ARTICLE_CHARS]

    images: list[dict] = []
    seen: set[str] = set()
    for img in root.select("img"):
        raw_url = str(img.get("data-src") or img.get("data-original") or img.get("src") or "").strip()
        if not raw_url or raw_url.startswith("data:"):
            continue
        image_url = urljoin(url, raw_url)
        if image_url in seen or not re.search(r"\.(?:jpe?g|png|webp|gif)(?:\?|$)", image_url, re.I):
            continue
        seen.add(image_url)
        figure = img.find_parent("figure")
        caption_node = figure.find("figcaption") if figure else None
        nearby = caption_node.get_text(" ", strip=True) if caption_node else ""
        if not nearby and img.parent:
            nearby = img.parent.get_text(" ", strip=True)
        description = _clean_text(f"{img.get('alt') or ''} {nearby}")[:420]
        # 站点 logo、二维码和空白占位图不会进入摘要候选。
        if len(description) < 4 and not "uploads" in image_url:
            continue
        images.append({"url": image_url, "description": description or "原文配图"})
        if len(images) >= MAX_IMAGES:
            break
    return text, images


def fingerprint(record: dict, text: str, images: list[dict]) -> str:
    source = {"id": record.get("id"), "url": record.get("url"), "title": record.get("title"), "text": text, "images": images}
    return hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[warn] unreadable roundup cache {path.name}: {exc}")
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_cache(path: Path, payload: dict) -> None:
    """Write complete JSON in one replacement so interrupted runs keep old data."""
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _record_failure(cache_path: Path, cached: dict, source_hash: str | None, exc: Exception) -> None:
    """Persist retry diagnostics without replacing a successful digest.

    ``source_hash`` deliberately remains the hash of the last *successful*
    digest.  Therefore an edited article that failed to summarize is retried on
    the next run, while the UI can still show the previous source-grounded
    digest instead of falling back to a short crawler excerpt.
    """
    payload = dict(cached)
    payload.update(
        {
            "schema_version": CACHE_SCHEMA_VERSION,
            "status": "error",
            "last_attempt_at": _now(),
            "last_attempt_source_hash": source_hash,
            "last_error": {
                "type": type(exc).__name__,
                "message": str(exc)[:500],
            },
        }
    )
    _write_cache(cache_path, payload)


def summarize(api_key: str, record: dict, text: str, images: list[dict]) -> dict:
    payload = {
        "article": {"title": record.get("title", ""), "url": record.get("url", ""), "published_at": record.get("published_at", "")},
        "article_text": text,
        "images": [{"source_index": index, **image} for index, image in enumerate(images)],
    }
    response = requests.post(
        API_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": MODEL,
            "thinking": {"type": "disabled"},
            "temperature": 0.2,
            "max_tokens": 1800,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        },
        timeout=60,
    )
    response.raise_for_status()
    result = json.loads(response.json()["choices"][0]["message"]["content"])
    findings = []
    for item in result.get("key_findings", [])[:6]:
        if isinstance(item, dict) and str(item.get("text") or "").strip():
            findings.append({"title": str(item.get("title") or "关键发现").strip()[:48], "text": str(item["text"]).strip()[:240]})
    highlights = []
    for item in result.get("image_highlights", [])[:4]:
        if not isinstance(item, dict) or not isinstance(item.get("source_index"), int):
            continue
        index = item["source_index"]
        if 0 <= index < len(images):
            highlights.append({**images[index], "caption": str(item.get("caption") or images[index]["description"]).strip()[:140]})
    overview = str(result.get("overview") or "").strip()[:600]
    if not overview:
        raise ValueError("DeepSeek response did not contain an overview")
    return {"overview": overview, "key_findings": findings, "image_highlights": highlights}


def main() -> None:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print(json.dumps({"skipped": True, "reason": "DEEPSEEK_API_KEY is not set"}, ensure_ascii=False))
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    api_calls = cache_hits = failed = 0
    for record in (item for item in load_all_records("report") if is_roundup(item)):
        report_id = str(record.get("id") or "")
        if not report_id or not record.get("url"):
            continue
        cache_path: Path | None = None
        cached: dict = {}
        source_hash: str | None = None
        try:
            text, images = fetch_article(str(record["url"]))
            if not text:
                raise ValueError("original article had no extractable text")
            source_hash = fingerprint(record, text, images)
            cache_path = CACHE_DIR / f"{report_id}.json"
            legacy_cache_path = LEGACY_CACHE_DIR / cache_path.name
            cached = _read_cache(cache_path if cache_path.exists() else legacy_cache_path)
            if cached.get("source_hash") == source_hash and cached.get("overview"):
                cache_hits += 1
                continue
            digest = summarize(api_key, record, text, images)
            _write_cache(
                cache_path,
                {
                    "schema_version": CACHE_SCHEMA_VERSION,
                    "status": "success",
                    "model": MODEL,
                    "source_hash": source_hash,
                    "last_attempt_source_hash": source_hash,
                    "captured_at": _now(),
                    "last_attempt_at": _now(),
                    "last_success_at": _now(),
                    "source_url": record["url"],
                    **digest,
                },
            )
            api_calls += 1
        except Exception as exc:
            failed += 1
            if cache_path is None:
                cache_path = CACHE_DIR / f"{report_id}.json"
                cached = _read_cache(cache_path)
            _record_failure(cache_path, cached, source_hash, exc)
            print(f"[warn] roundup {report_id}: {exc}")
    print(json.dumps({"api_calls": api_calls, "cache_hits": cache_hits, "failed": failed, "model": MODEL}, ensure_ascii=False))


if __name__ == "__main__":
    main()
