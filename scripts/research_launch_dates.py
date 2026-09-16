#!/usr/bin/env python3
"""Incrementally extract source-backed product launch dates from 52audio.

This intentionally treats the source article's publication date as *not* being
the product launch date.  DeepSeek may return a date only when the supplied
article explicitly says the identified product was launched, released or went
on sale on that date.  Every positive and negative result is cached by
canonical product ID, so later daily workflows analyse only new products.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.ingest import load_all_records  # noqa: E402
from core.paths import launch_enrich_dir, products_dir, write_launch_enrich  # noqa: E402

MODEL = "deepseek-v4-flash"
MAX_ARTICLE_CHARS = 14_000
DATE_RE = re.compile(r"^20\d{2}(?:-(?:0[1-9]|1[0-2])(?:-(?:0[1-9]|[12]\d|3[01]))?)?$")
SYSTEM_PROMPT = """You extract product launch timing from a source article.
Return JSON only. The article's own publication date, a component production
date, an award year, and a comparison to another product are NOT launch dates.
Return a date only if the supplied article explicitly states that this exact
product launched, was released, or went on sale then. Do not infer a date from
the report date. Keep evidence_quote as a short verbatim quotation from the
article. If no explicit product launch timing exists, return not_found.
Schema:
{"status":"verified|year_only|not_found","launch_date":"YYYY-MM-DD|YYYY-MM|YYYY|","launch_scope":"China|global|unknown","evidence_quote":"","confidence":0.0}"""


def article_text(url: str) -> str:
    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; 52audio-launch-research/1.0)"},
        timeout=30,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    for node in soup.select("script, style, noscript, svg, nav, footer"):
        node.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:MAX_ARTICLE_CHARS]


def call_model(api_key: str, product: dict, record: dict, text: str) -> dict:
    payload = {
        "product": {"brand": product.get("brand"), "model": product.get("model")},
        "source": {"url": record.get("url"), "title": record.get("title")},
        "article_text": text,
    }
    response = requests.post(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": MODEL,
            "thinking": {"type": "disabled"},
            "temperature": 0,
            "max_tokens": 360,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        },
        timeout=60,
    )
    response.raise_for_status()
    return json.loads(response.json()["choices"][0]["message"]["content"])


def validate(result: dict, article: str) -> dict:
    status = str(result.get("status") or "not_found")
    value = str(result.get("launch_date") or "").strip()
    quote = re.sub(r"\s+", " ", str(result.get("evidence_quote") or "").strip())[:500]
    try:
        confidence = float(result.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    # Cache negatives too.  Positive values require both a strict date shape
    # and verbatim evidence in the fetched article.
    if status not in {"verified", "year_only"} or not DATE_RE.fullmatch(value):
        return {"status": "not_found", "launch_date": "", "launch_display": "", "launch_scope": "", "evidence": "", "confidence": 0}
    article_normalized = re.sub(r"\s+", " ", article)
    if confidence < 0.9 or not quote or quote not in article_normalized:
        return {"status": "not_found", "launch_date": "", "launch_display": "", "launch_scope": "", "evidence": "", "confidence": 0}
    return {
        "status": "verified" if len(value) >= 7 else "year_only",
        "launch_date": value,
        "launch_display": value,
        "launch_scope": str(result.get("launch_scope") or "unknown")[:40],
        "evidence": quote,
        "confidence": round(confidence, 2),
    }


def candidate_products(*, recent_only: bool, limit: int | None) -> list[dict]:
    candidates: list[dict] = []
    for path in sorted(products_dir().glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            product = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        canonical_id = str(product.get("canonical_id") or "")
        if not canonical_id or (launch_enrich_dir() / f"{canonical_id}.json").exists():
            continue
        if recent_only and str(product.get("first_seen") or "")[:4] < "2025":
            continue
        if product.get("report_ids"):
            candidates.append(product)
    candidates.sort(key=lambda item: (str(item.get("first_seen") or ""), str(item.get("canonical_id") or "")), reverse=True)
    return candidates[:limit] if limit else candidates


def resolve_one(api_key: str, product: dict, records: dict[str, dict]) -> dict:
    canonical_id = str(product["canonical_id"])
    record = next((records[rid] for rid in product.get("report_ids") or [] if rid in records and records[rid].get("url")), None)
    base = {"canonical_id": canonical_id, "source_url": (record or {}).get("url") or "", "checked_at": datetime.now(timezone.utc).isoformat()}
    if not record:
        return {**base, "status": "not_found", "reason": "no original report URL"}
    try:
        text = article_text(base["source_url"])
        if not text:
            return {**base, "status": "not_found", "reason": "source text unavailable"}
        validated = validate(call_model(api_key, product, record, text), text)
        return {**base, **validated, "source_type": "source_article", "report_id": record.get("id")}
    except requests.RequestException as exc:
        return {**base, "status": "not_found", "reason": f"source fetch failed: {exc.__class__.__name__}"}
    except Exception as exc:
        return {**base, "status": "not_found", "reason": f"model extraction failed: {exc.__class__.__name__}"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Incrementally extract evidence-backed product launch dates")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--recent-only", action="store_true", help="only products first seen in 2025 or later")
    scope.add_argument("--all", action="store_true", help="include historical products as well")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--write", action="store_true", help="persist source-backed and negative cache entries")
    args = parser.parse_args()
    api_key = os.environ["DEEPSEEK_API_KEY"]
    reports = {str(item.get("id")): item for item in load_all_records("report")}
    candidates = candidate_products(recent_only=args.recent_only, limit=args.limit)
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 3))) as executor:
        futures = [executor.submit(resolve_one, api_key, product, reports) for product in candidates]
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: item["canonical_id"])
    if args.write:
        for result in results:
            write_launch_enrich(result["canonical_id"], result)
    summary = {
        "processed": len(results),
        "verified": sum(item.get("status") in {"verified", "year_only"} for item in results),
        "not_found": sum(item.get("status") == "not_found" for item in results),
        "written": bool(args.write),
        "scope": "recent_only" if args.recent_only else "all",
    }
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
