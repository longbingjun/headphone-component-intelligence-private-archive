"""构建站点搜索索引；竞品矩阵由 build_matrix.py 单独负责。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.ingest import load_all_records, merge_price_into_record  # noqa: E402

DATA_DIR = ROOT / "data"
SEARCH_INDEX_PATH = DATA_DIR / "search-index.json"


def _tags_from_record(record: dict) -> list[str]:
    tags: list[str] = []
    for key in ("category", "brand", "model"):
        val = (record.get(key) or "").strip()
        if val and val not in tags:
            tags.append(val)
    v = record.get("views") or {}
    for part in v.get("cost", {}).get("major_parts", [])[:5]:
        p = (part or "").strip()
        if p and p not in tags:
            tags.append(p)
    for chip in v.get("cost", {}).get("chip_modules", [])[:3]:
        model = (chip.get("model") or "").strip()
        if model and model not in tags:
            tags.append(model)
    return tags[:12]


def _search_entry(record: dict, kind: str) -> dict:
    title = record.get("title") or record.get("product_title") or ""
    return {
        "id": record["id"],
        "type": kind,
        "brand": record.get("brand") or "",
        "model": record.get("model") or "",
        "category": record.get("category") or "",
        "title": title,
        "published_at": record.get("published_at") or "",
        "tags": _tags_from_record(record),
    }


def build_search_index() -> list[dict]:
    entries: list[dict] = []
    for r in load_all_records("report"):
        entries.append(_search_entry(merge_price_into_record(r), "report"))
    for v in load_all_records("video"):
        entries.append(_search_entry(v, "video"))
    entries.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    SEARCH_INDEX_PATH.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    return entries


def main() -> None:
    entries = build_search_index()
    print(f"[build_search_index] {len(entries)} 条索引")


if __name__ == "__main__":
    main()
