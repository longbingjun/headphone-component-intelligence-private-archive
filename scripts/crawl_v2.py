"""v2 爬虫入口：追加写入，支持历史 backfill（回溯至指定日期）与日更。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.ingest import append_record, load_index, save_index  # noqa: E402
from core.scope import is_headphone_record  # noqa: E402
from sources.audio52.source_v2 import Audio52SourceV2  # noqa: E402


def run_backfill_2026(source: Audio52SourceV2) -> dict:
    stats = {"mode": "backfill-2026", "new_reports": 0, "new_videos": 0, "skipped": 0, "errors": 0, "non_headphone": 0}
    for item in source.iter_feed_items(year=2026):
        try:
            record = source.parse_item(item)
            if record is None:
                continue
            rec_dict = record.to_dict()
            if not is_headphone_record(rec_dict):
                stats["non_headphone"] += 1
                continue
            kind = "report" if record.type == "report" else "video"
            if append_record(kind, rec_dict):
                if kind == "report":
                    stats["new_reports"] += 1
                else:
                    stats["new_videos"] += 1
            else:
                stats["skipped"] += 1
        except Exception as e:
            print(f"[crawl] 解析失败 {item.get('url')}: {e}")
            stats["errors"] += 1

    index = load_index()
    from datetime import datetime, timezone

    index["last_backfill_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save_index(index)
    return stats


def run_backfill_historical(
    source: Audio52SourceV2,
    *,
    since: date | None,
    max_pages: int | None,
) -> dict:
    """通用历史回溯：翻分类 RSS Feed 直到早于 since 的日期或翻页 404 为止。

    与 backfill-2026 的区别：不按年份过滤，逐页遍历全部历史（2018 年至今），
    每条记录仍走 append_record 按 ID 去重立即落盘，天然支持中断后恢复。
    """
    stats = {
        "mode": "backfill-historical",
        "since": since.isoformat() if since else None,
        "max_pages": max_pages,
        "new_reports": 0,
        "new_videos": 0,
        "skipped": 0,
        "errors": 0,
        "non_headphone": 0,
        "earliest_seen": None,
        "latest_seen": None,
    }
    for item in source.iter_feed_items(stop_before=since, max_pages=max_pages):
        pub_date = item.get("pub_date")
        if pub_date:
            iso = pub_date.isoformat()
            if stats["earliest_seen"] is None or iso < stats["earliest_seen"]:
                stats["earliest_seen"] = iso
            if stats["latest_seen"] is None or iso > stats["latest_seen"]:
                stats["latest_seen"] = iso
        try:
            record = source.parse_item(item)
            if record is None:
                continue
            rec_dict = record.to_dict()
            if not is_headphone_record(rec_dict):
                stats["non_headphone"] += 1
                continue
            kind = "report" if record.type == "report" else "video"
            if append_record(kind, rec_dict):
                if kind == "report":
                    stats["new_reports"] += 1
                else:
                    stats["new_videos"] += 1
            else:
                stats["skipped"] += 1
        except Exception as e:
            print(f"[crawl] 解析失败 {item.get('url')}: {e}")
            stats["errors"] += 1

    index = load_index()
    from datetime import timezone

    index["last_backfill_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    index["last_historical_backfill_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save_index(index)
    return stats


def run_daily(source: Audio52SourceV2) -> dict:
    stats = {"mode": "daily", "new_reports": 0, "new_videos": 0, "skipped": 0, "errors": 0, "non_headphone": 0}
    for item in source.iter_feed_items(max_pages=1):
        try:
            record = source.parse_item(item)
            if record is None:
                continue
            rec_dict = record.to_dict()
            if not is_headphone_record(rec_dict):
                stats["non_headphone"] += 1
                continue
            kind = "report" if record.type == "report" else "video"
            if append_record(kind, rec_dict):
                if kind == "report":
                    stats["new_reports"] += 1
                else:
                    stats["new_videos"] += 1
            else:
                stats["skipped"] += 1
        except Exception as e:
            print(f"[crawl] 解析失败 {item.get('url')}: {e}")
            stats["errors"] += 1

    total_items = stats["new_reports"] + stats["new_videos"] + stats["skipped"] + stats["errors"] + stats["non_headphone"]
    error_rate = stats["errors"] / max(total_items, 1)
    if total_items > 0 and error_rate > 0.5:
        print(
            f"[crawl] 解析错误率过高：{stats['errors']}/{total_items} ({error_rate:.0%})，"
            "可能是 52audio 站点模板变更导致解析器失效，终止并返回非零退出码"
        )
        sys.exit(1)

    index = load_index()
    from datetime import datetime, timezone

    index["last_daily_crawl_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save_index(index)
    return stats


def rebuild_product_data() -> dict:
    """爬虫完成后重建产品主数据与竞品矩阵。"""
    from scripts.build_matrix import build_matrix  # noqa: E402
    from scripts.build_products import build_products  # noqa: E402

    products_stats = build_products()
    matrix_stats = build_matrix()
    return {"products": products_stats, "matrix": matrix_stats}


def main() -> None:
    parser = argparse.ArgumentParser(description="52audio v2 爬虫")
    parser.add_argument(
        "--mode",
        choices=["backfill-2026", "backfill-historical", "daily"],
        default="daily",
        help=(
            "backfill-2026=首次抓取2026年全部；"
            "backfill-historical=按 --since/--max-pages 回溯历史全量；"
            "daily=日更仅第1页"
        ),
    )
    parser.add_argument(
        "--since",
        type=str,
        default="2018-01-01",
        help="backfill-historical 专用：回溯截止日期 YYYY-MM-DD（含），默认 2018-01-01",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=120,
        help="backfill-historical 专用：翻页安全上限（RSS Feed 实测约 106 页到 2018-04）",
    )
    parser.add_argument(
        "--skip-rebuild",
        action="store_true",
        help="跳过后续 build_products / build_matrix",
    )
    args = parser.parse_args()
    source = Audio52SourceV2()
    if args.mode == "backfill-2026":
        stats = run_backfill_2026(source)
    elif args.mode == "backfill-historical":
        since = date.fromisoformat(args.since) if args.since else None
        stats = run_backfill_historical(source, since=since, max_pages=args.max_pages)
    else:
        stats = run_daily(source)
    if not args.skip_rebuild:
        stats["rebuild"] = rebuild_product_data()
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
