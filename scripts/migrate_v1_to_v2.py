"""将 v1 单体 JSON 迁移为 v2 按 ID 分文件（去除 content_html）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.ingest import append_record  # noqa: E402
from core.models_v2 import CostView, HardwareView, MarketView, RoleViews, SoftwareView, StructureView  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data"


def _map_report(old: dict) -> dict:
    tech = old.get("tech_specs", {})
    views = RoleViews(
        market=MarketView(
            brand=old.get("brand", ""),
            model=old.get("model", ""),
            category=old.get("category", ""),
            selling_points=[p.get("text", "") for p in old.get("selling_points", [])[:6]],
            positioning_summary=(old.get("selling_points") or [{}])[0].get("text", "") if old.get("selling_points") else "",
        ),
        cost=CostView(
            major_parts=[c.get("name", "") for c in old.get("components_major", [])],
            packaging_notes=tech.get("manual_notes", [])[:5],
        ),
        structure=StructureView(form_factor=old.get("category", "")),
        hardware=HardwareView(
            specs=[
                {"part": "充电方式", "value": s, "source_ref": "migrated"}
                for s in tech.get("charging_method", [])
            ]
            + [{"part": "充电接口", "value": s, "source_ref": "migrated"} for s in tech.get("charging_port", [])]
        ),
        software=SoftwareView(),
    )
    return {
        "id": old["id"],
        "type": "report",
        "source_id": old.get("source_id", "audio52"),
        "url": old["url"],
        "title": old.get("title", ""),
        "brand": old.get("brand", ""),
        "model": old.get("model", ""),
        "category": old.get("category", ""),
        "published_at": old.get("date", ""),
        "author": old.get("author", ""),
        "summary": old.get("summary", ""),
        "captured_at": old.get("crawled_at") or old.get("first_seen_at", ""),
        "views": views.to_dict(),
    }


def _map_video(old: dict) -> dict:
    return {
        "id": old["id"],
        "type": "video",
        "source_id": old.get("source_id", "audio52"),
        "url": old["url"],
        "title": old.get("title", ""),
        "product_title": old.get("product_title", old.get("title", "")),
        "brand": old.get("brand", ""),
        "model": old.get("model", ""),
        "category": old.get("category", ""),
        "published_at": old.get("date", ""),
        "publisher": old.get("publisher", ""),
        "summary": old.get("summary", ""),
        "source_site": old.get("source_site", ""),
        "video_embed_url": old.get("video_embed_url", ""),
        "captured_at": old.get("crawled_at") or old.get("first_seen_at", ""),
        "views": RoleViews().to_dict(),
    }


def main() -> None:
    reports_path = DATA / "reports.json"
    videos_path = DATA / "videos.json"
    n_r = n_v = 0
    if reports_path.exists():
        for item in json.loads(reports_path.read_text(encoding="utf-8")).get("items", []):
            if append_record("report", _map_report(item)):
                n_r += 1
    if videos_path.exists():
        for item in json.loads(videos_path.read_text(encoding="utf-8")).get("items", []):
            if append_record("video", _map_video(item)):
                n_v += 1
    print(f"Migrated {n_r} reports, {n_v} videos")


if __name__ == "__main__":
    main()
