#!/usr/bin/env python3
"""清理非耳机数据：产品、矩阵、enrich、孤立 reports/videos。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.ingest import load_index, save_index  # noqa: E402
from core.paths import (  # noqa: E402
    channel_enrich_dir,
    compare_dir,
    matrix_dir,
    official_enrich_dir,
    products_dir,
    reports_dir,
    videos_dir,
)
from core.scope import HEADPHONE_CATEGORIES  # noqa: E402


def _delete_json_files(folder: Path, keep_ids: set[str] | None, *, skip_index: bool = True) -> int:
    if not folder.exists():
        return 0
    n = 0
    for path in folder.glob("*.json"):
        if skip_index and path.name == "index.json":
            continue
        stem = path.stem
        if keep_ids is not None and stem in keep_ids:
            continue
        path.unlink()
        n += 1
    return n


def prune() -> dict:
    # 先根据现有产品索引确定保留的 canonical_id
    idx_path = products_dir() / "index.json"
    keep_products: set[str] = set()
    keep_reports: set[str] = set()
    keep_videos: set[str] = set()

    if idx_path.exists():
        index = json.loads(idx_path.read_text(encoding="utf-8"))
        for p in index.get("products") or []:
            if p.get("category") not in HEADPHONE_CATEGORIES:
                continue
            cid = p["canonical_id"]
            keep_products.add(cid)
            prod_path = products_dir() / f"{cid}.json"
            if prod_path.exists():
                prod = json.loads(prod_path.read_text(encoding="utf-8"))
                keep_reports.update(prod.get("report_ids") or [])
                keep_videos.update(prod.get("video_ids") or [])

    stats: dict = {"keep_products": len(keep_products)}

    for base in (products_dir(),):
        if not base.exists():
            continue
        for path in base.glob("*.json"):
            if path.name == "index.json":
                continue
            if path.stem not in keep_products:
                path.unlink()
                stats["removed_products"] = stats.get("removed_products", 0) + 1

    for enrich_dir, key in (
        (channel_enrich_dir(), "removed_channel"),
        (official_enrich_dir(), "removed_official"),
    ):
        if not enrich_dir.exists():
            continue
        for path in enrich_dir.glob("*.json"):
            if path.stem not in keep_products:
                path.unlink()
                stats[key] = stats.get(key, 0) + 1

    for folder, key in ((matrix_dir(for_write=True), "removed_matrix"), (compare_dir(for_write=True), "removed_compare")):
        if folder.exists():
            for path in folder.glob("*.json"):
                path.unlink()
                stats[key] = stats.get(key, 0) + 1

    for folder, keep, key in (
        (reports_dir(), keep_reports, "removed_reports"),
        (videos_dir(), keep_videos, "removed_videos"),
    ):
        if not folder.exists():
            continue
        for path in folder.glob("*.json"):
            if path.stem in keep:
                continue
            path.unlink()
            stats[key] = stats.get(key, 0) + 1

    index = load_index()
    index["report_ids"] = sorted(keep_reports)
    index["video_ids"] = sorted(keep_videos)
    save_index(index)
    stats["report_ids"] = len(keep_reports)
    stats["video_ids"] = len(keep_videos)
    return stats


def main() -> None:
    stats = prune()
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
