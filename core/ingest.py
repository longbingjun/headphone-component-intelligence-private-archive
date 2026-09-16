"""v2 入库：按 ID 单文件追加，永不覆盖。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from core.paths import channel_enrich_dir, price_enrich_dir, reports_dir, video_enrich_dir, videos_dir

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
REPORTS_DIR = reports_dir()
VIDEOS_DIR = videos_dir()
CHANNEL_ENRICH_DIR = channel_enrich_dir()
INDEX_PATH = DATA_DIR / "index.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_index() -> dict:
    if not INDEX_PATH.exists():
        return {
            "report_ids": [],
            "video_ids": [],
            "last_daily_crawl_at": None,
            "last_backfill_at": None,
        }
    try:
        return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"report_ids": [], "video_ids": [], "last_daily_crawl_at": None, "last_backfill_at": None}


def save_index(index: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def known_ids(kind: Literal["report", "video"]) -> set[str]:
    idx = load_index()
    key = "report_ids" if kind == "report" else "video_ids"
    return set(idx.get(key, []))


def exists(kind: Literal["report", "video"], item_id: str) -> bool:
    return item_id in known_ids(kind)


def _path_for(kind: Literal["report", "video"], item_id: str) -> Path:
    base = reports_dir() if kind == "report" else videos_dir()
    return base / f"{item_id}.json"


def append_record(kind: Literal["report", "video"], record: dict) -> bool:
    """写入新记录。若 ID 已存在则跳过并返回 False。"""
    item_id = record["id"]
    if exists(kind, item_id):
        return False

    path = _path_for(kind, item_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    index = load_index()
    key = "report_ids" if kind == "report" else "video_ids"
    ids: list[str] = index.get(key, [])
    if item_id not in ids:
        ids.append(item_id)
    index[key] = sorted(ids)
    save_index(index)
    return True


def load_all_records(kind: Literal["report", "video"]) -> list[dict]:
    base = reports_dir() if kind == "report" else videos_dir()
    if not base.exists():
        return []
    records = []
    for path in sorted(base.glob("*.json")):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return sorted(records, key=lambda r: r.get("published_at", ""), reverse=True)


def load_price_enrich(item_id: str) -> dict | None:
    path = price_enrich_dir() / f"{item_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_channel_enrich(canonical_id: str) -> dict | None:
    path = channel_enrich_dir() / f"{canonical_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def merge_price_into_record(record: dict) -> dict:
    """构建站点时合并售价 enrich。"""
    price = load_price_enrich(record["id"])
    if not price:
        return record
    out = dict(record)
    views = dict(out.get("views", {}))
    market = dict(views.get("market", {}))
    if price.get("price_cny") is not None:
        market["price_cny"] = price["price_cny"]
    if price.get("price_note"):
        market["price_note"] = price["price_note"]
    if price.get("price_source"):
        market["price_source"] = price["price_source"]
    if price.get("price_url"):
        market["price_url"] = price["price_url"]
    views["market"] = market
    out["views"] = views
    return out


def refresh_views_fields(record: dict, views_dict: dict, data_completeness: float) -> dict:
    """结构化字段刷新例外：仅更新 views 与 data_completeness，保留 id/url/captured_at 等。"""
    out = dict(record)
    out["views"] = views_dict
    out["data_completeness"] = data_completeness
    return out


def save_record_in_place(kind: Literal["report", "video"], record: dict) -> None:
    """覆盖写入已有记录（仅用于 views 刷新脚本，非日常 ingest）。"""
    path = _path_for(kind, record["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
