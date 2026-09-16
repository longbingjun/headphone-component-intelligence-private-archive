#!/usr/bin/env python3
"""批量提取开箱三区（包装 / 充电盒 / 耳机）→ staging/unboxing/{report_id}.json

用法:
  py -3 scripts/enrich_unboxing.py --headphones
  py -3 scripts/enrich_unboxing.py 269067
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.extract.unboxing_sections import extract_unboxing_sections  # noqa: E402
from core.paths import products_index_path, reports_dir, unboxing_enrich_dir, write_unboxing_enrich  # noqa: E402
from core.products import canonical_product_id  # noqa: E402
from core.scope import HEADPHONE_CATEGORIES, is_headphone_record, normalize_headphone_category  # noqa: E402
from sources.audio52.source_v2 import Audio52SourceV2  # noqa: E402

CACHE_DIR = ROOT / "data" / "cache" / "content_html"


def _group_by_tag(points: list[dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for p in points:
        tag = p.get("tag") or "其他"
        text = (p.get("text") or "").strip()
        if not text:
            continue
        out.setdefault(tag, [])
        if text not in out[tag]:
            out[tag].append(text)
    return out


def _cache_path(report_id: str) -> Path:
    return CACHE_DIR / f"{report_id}.html"


def _resolve_html(
    source: Audio52SourceV2,
    report: dict,
    feed_index: dict[str, str],
    *,
    delay_sec: float = 0.0,
    refresh_html: bool = False,
) -> str:
    rid = report["id"]
    cached = _cache_path(rid)
    if cached.exists() and not refresh_html:
        html = cached.read_text(encoding="utf-8")
        if html.strip():
            return html
    html = source.resolve_content_html(rid, report.get("url", ""), feed_index)
    if delay_sec > 0:
        # politeness delay: only applied on an actual network fetch (cache miss)
        time.sleep(delay_sec)
    if html.strip():
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(html, encoding="utf-8")
    return html


def build_unboxing_payload(
    report: dict,
    *,
    html: str,
    canonical_id: str | None = None,
) -> dict:
    if not html.strip():
        raise ValueError("no_content_html")

    unboxing = extract_unboxing_sections(html)
    bom = (report.get("views") or {}).get("cost", {}).get("bom_table") or []
    unboxing["charging_case"]["bom_rows"] = [r for r in bom if r.get("side") == "充电盒"]
    unboxing["earbuds"]["bom_rows"] = [r for r in bom if r.get("side") == "耳机"]

    market = (report.get("views") or {}).get("market") or {}
    title = report.get("title") or report.get("product_title") or ""
    category = normalize_headphone_category(
        title,
        report.get("category") or market.get("category") or "",
    )
    cid = canonical_id or canonical_product_id(
        report.get("brand") or market.get("brand") or "",
        report.get("model") or market.get("model") or "",
    )
    unboxing["product"] = {
        "canonical_id": cid,
        "brand": report.get("brand") or market.get("brand"),
        "model": report.get("model") or market.get("model"),
        "category": category,
        "earbud_type": (report.get("views") or {}).get("structure", {}).get("earbud_type"),
        "report_url": report.get("url"),
        "price_cny": market.get("price_cny"),
    }

    existing = market.get("selling_points") or []
    seen = {p.get("text", "")[:80] for p in unboxing["intro_selling_points"]}
    merged_sp = list(unboxing["intro_selling_points"])
    for p in existing:
        key = (p.get("text") or "")[:80]
        if key and key not in seen:
            merged_sp.append(p)
            seen.add(key)
    unboxing["selling_points_by_tag"] = _group_by_tag(merged_sp)

    payload = {
        "report_id": report["id"],
        "canonical_id": cid,
        "source": "audio52",
        "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **unboxing,
    }
    return payload


def enrich_report(
    report: dict,
    *,
    source: Audio52SourceV2,
    feed_index: dict[str, str],
    canonical_id: str | None = None,
    delay_sec: float = 0.0,
    refresh_html: bool = False,
) -> dict:
    html = _resolve_html(
        source,
        report,
        feed_index,
        delay_sec=delay_sec,
        refresh_html=refresh_html,
    )
    payload = build_unboxing_payload(report, html=html, canonical_id=canonical_id)
    write_unboxing_enrich(report["id"], payload)
    return payload


def _list_headphone_report_ids(limit: int | None = None) -> list[str]:
    idx_path = products_index_path()
    if not idx_path.exists():
        return []
    index = json.loads(idx_path.read_text(encoding="utf-8"))
    ids: list[str] = []
    products_dir = idx_path.parent
    for p in index.get("products") or []:
        if p.get("category") not in HEADPHONE_CATEGORIES:
            continue
        prod_path = products_dir / f"{p['canonical_id']}.json"
        if not prod_path.exists():
            continue
        prod = json.loads(prod_path.read_text(encoding="utf-8"))
        ids.extend(prod.get("report_ids") or [])
    out = sorted(set(ids))
    if limit:
        return out[:limit]
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report_id", nargs="?", help="单条报告 ID")
    parser.add_argument("--headphones", action="store_true", help="批量处理耳机产品关联报告")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true", help="Re-process reports even if staging/unboxing output already exists")
    parser.add_argument("--refresh-html", action="store_true", help="Ignore cached article HTML and fetch source bytes again")
    parser.add_argument("--delay", type=float, default=0.8, help="Politeness delay (sec) after each real network fetch (cache hits are not delayed)")
    args = parser.parse_args()

    source = Audio52SourceV2()
    feed_index: dict[str, str] = {}

    if args.headphones:
        report_ids = _list_headphone_report_ids(args.limit)
    elif args.report_id:
        report_ids = [args.report_id]
    else:
        parser.error("请提供 report_id 或 --headphones")

    out_dir = unboxing_enrich_dir()
    total = len(report_ids)
    ok, skipped, failed = 0, 0, []
    for i, rid in enumerate(report_ids, 1):
        if not args.force and (out_dir / f"{rid}.json").exists():
            skipped += 1
            continue
        path = reports_dir() / f"{rid}.json"
        if not path.exists():
            failed.append({"report_id": rid, "error": "report_not_found"})
            continue
        report = json.loads(path.read_text(encoding="utf-8"))
        if not is_headphone_record(report):
            failed.append({"report_id": rid, "error": "not_headphone"})
            continue
        try:
            enrich_report(
                report,
                source=source,
                feed_index=feed_index,
                delay_sec=args.delay,
                refresh_html=args.refresh_html,
            )
            ok += 1
        except Exception as e:
            failed.append({"report_id": rid, "error": str(e)})
        if i % 25 == 0 or i == total:
            print(
                f"[progress] {i}/{total} scanned | ok={ok} skipped={skipped} failed={len(failed)}",
                flush=True,
            )

    print(
        json.dumps(
            {"ok": ok, "skipped": skipped, "failed": failed, "total": total},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
