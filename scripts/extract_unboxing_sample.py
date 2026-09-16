#!/usr/bin/env python3
"""对单条报告提取开箱三区样本，写入 data/staging/unboxing/{report_id}.json"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.extract.unboxing_sections import extract_unboxing_sections
from core.paths import unboxing_enrich_dir

REPORT_ID = "269067"  # HUAWEI FreeBuds Pro 5 — 开箱段完整


def main() -> None:
    report_id = sys.argv[1] if len(sys.argv) > 1 else REPORT_ID
    report_path = ROOT / "data" / "reports" / f"{report_id}.json"
    html_path = ROOT / "data" / "cache" / "content_html" / f"{report_id}.html"

    if not report_path.exists():
        raise SystemExit(f"report not found: {report_path}")
    if not html_path.exists():
        raise SystemExit(f"html cache not found: {html_path}")

    report = json.loads(report_path.read_text(encoding="utf-8"))
    html = html_path.read_text(encoding="utf-8")
    unboxing = extract_unboxing_sections(html)

    # 合并已有 BOM 行（按 side 过滤）
    bom = report.get("views", {}).get("cost", {}).get("bom_table") or []
    unboxing["charging_case"]["bom_rows"] = [r for r in bom if r.get("side") == "充电盒"]
    unboxing["earbuds"]["bom_rows"] = [r for r in bom if r.get("side") == "耳机"]

    # 补充 market 基本信息
    market = report.get("views", {}).get("market") or {}
    unboxing["product"] = {
        "brand": report.get("brand") or market.get("brand"),
        "model": report.get("model") or market.get("model"),
        "category": report.get("category") or market.get("category"),
        "earbud_type": (report.get("views", {}).get("structure") or {}).get("earbud_type"),
        "report_url": report.get("url"),
        "price_cny": market.get("price_cny"),
    }

    # 合并导语卖点 + 已有 selling_points（按 tag 去重）
    existing = market.get("selling_points") or []
    seen = {p.get("text", "")[:80] for p in unboxing["intro_selling_points"]}
    merged_sp = list(unboxing["intro_selling_points"])
    for p in existing:
        key = (p.get("text") or "")[:80]
        if key and key not in seen:
            merged_sp.append(p)
            seen.add(key)
    unboxing["selling_points_by_tag"] = _group_by_tag(merged_sp)

    out_dir = unboxing_enrich_dir(for_write=True)
    out_path = out_dir / f"{report_id}.json"
    payload = {
        "report_id": report_id,
        "canonical_id": f"{report.get('brand', '').lower()}--{report.get('model', '').lower()}".replace(" ", "-"),
        "source": "audio52",
        "extracted_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        **unboxing,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    print(f"  packaging images: {len(unboxing['packaging']['images'])}")
    print(f"  charging_case images: {len(unboxing['charging_case']['images'])}")
    print(f"  earbuds images: {len(unboxing['earbuds']['images'])}")
    print(f"  accessories: {unboxing['packaging']['accessories']}")
    if unboxing["gaps"]:
        print("  gaps:", "; ".join(unboxing["gaps"]))


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


if __name__ == "__main__":
    main()
