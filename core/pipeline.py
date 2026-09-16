"""统一的抓取编排逻辑：fetch_list -> parse_detail -> 合并落盘。

这个模块完全不知道 52audio 是什么，只认识 BaseSource 接口和
core.models 里定义的数据结构，未来新增情报源时，只要在 scripts/crawl.py
里把新 Source 的实例加入 SOURCES 列表即可，这里的代码不用改。

落盘策略：
- data/reports.json / data/videos.json 用"以 id 为 key 合并覆盖"的方式更新，
  这样每天定时任务重新抓一遍最近文章时，历史已经抓过的数据不会丢，
  同时旧记录的 first_seen_at 会被保留下来（为以后的时间线功能做准备），
  但内容字段（正文、卖点、部件等）会用最新抓取结果覆盖。
- data/images_queue.json 是"待接入真实 OCR"的全量队列快照（按 image_url 去重）。
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from core.base_source import BaseSource
from core.models import TeardownReport, VideoItem

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def run_sources(sources: list[BaseSource], limit_per_source: int = 30) -> dict:
    """跑一遍所有情报源，返回本次抓取的统计信息。"""

    reports_path = DATA_DIR / "reports.json"
    videos_path = DATA_DIR / "videos.json"
    queue_path = DATA_DIR / "images_queue.json"

    existing_reports = {r["id"]: r for r in _load_json(reports_path).get("items", [])}
    existing_videos = {v["id"]: v for v in _load_json(videos_path).get("items", [])}
    existing_queue = {q["image_url"]: q for q in _load_json(queue_path).get("items", [])}

    stats = {"sources": {}}
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for source in sources:
        new_reports = 0
        updated_reports = 0
        new_videos = 0
        updated_videos = 0
        errors = 0
        images_reused = 0

        if hasattr(source, "existing_reports"):
            source.existing_reports = existing_reports

        list_items = source.fetch_list(limit=limit_per_source)
        for raw_item in list_items:
            try:
                parsed = source.parse_detail(raw_item)
            except Exception as e:
                print(f"[pipeline] 解析失败 {raw_item.get('url')}: {e}")
                errors += 1
                continue
            if parsed is None:
                continue

            if isinstance(parsed, TeardownReport):
                prev = existing_reports.get(parsed.id)
                if (
                    prev
                    and prev.get("content_html") == parsed.content_html
                    and prev.get("images")
                    and parsed.images
                ):
                    images_reused += 1
                parsed.first_seen_at = prev["first_seen_at"] if prev and prev.get("first_seen_at") else now_iso
                if prev is None:
                    new_reports += 1
                else:
                    updated_reports += 1
                existing_reports[parsed.id] = parsed.to_dict()
            elif isinstance(parsed, VideoItem):
                prev = existing_videos.get(parsed.id)
                parsed.first_seen_at = prev["first_seen_at"] if prev and prev.get("first_seen_at") else now_iso
                if prev is None:
                    new_videos += 1
                else:
                    updated_videos += 1
                existing_videos[parsed.id] = parsed.to_dict()

        # 汇总本次抓取产生的 OCR 待办队列（按 image_url 去重，保留最新一次判断依据）
        queue_entries = getattr(source, "image_queue_entries", [])
        for q in queue_entries:
            existing_queue[q["image_url"]] = q

        stats["sources"][source.source_id] = {
            "display_name": source.display_name,
            "fetched": len(list_items),
            "new_reports": new_reports,
            "updated_reports": updated_reports,
            "new_videos": new_videos,
            "updated_videos": updated_videos,
            "images_reused": images_reused,
            "errors": errors,
        }

    reports_list = sorted(existing_reports.values(), key=lambda r: r.get("date", ""), reverse=True)
    videos_list = sorted(existing_videos.values(), key=lambda v: v.get("date", ""), reverse=True)
    queue_list = list(existing_queue.values())

    _save_json(reports_path, {"generated_at": now_iso, "count": len(reports_list), "items": reports_list})
    _save_json(videos_path, {"generated_at": now_iso, "count": len(videos_list), "items": videos_list})
    _save_json(queue_path, {"generated_at": now_iso, "count": len(queue_list), "items": queue_list})

    stats["total_reports"] = len(reports_list)
    stats["total_videos"] = len(videos_list)
    stats["total_images_queue"] = len(queue_list)
    stats["generated_at"] = now_iso
    return stats
