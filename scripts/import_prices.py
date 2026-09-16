"""渠道层 enrich 导入：按 canonical_id 写入 data/staging/channel/{id}.json

CSV 格式（UTF-8，首行表头）— 二选一 ID 列：
  canonical_id,price_cny,price_source,channel_url,sales_hint,price_note
  id,price_cny,price_source,channel_url,sales_hint,price_note   # 报告 ID，自动映射到产品

Optional fields for traceable non-standard prices:
  price_amount,price_currency,price_kind,price_label
  price_kind: promo | used | inventory | channel_event | channel

字段说明：
  - canonical_id / id  产品 canonical ID 或报告 ID（必填其一）
  - price_cny          人民币现价
  - price_source       来源：jd / tmall / pdd / manual_csv
  - channel_url        渠道 SKU 页 URL
  - sales_hint         销量区间或备注
  - price_note         活动价等备注

用法：
  python scripts/import_prices.py data/staging/channel/example.csv
  python scripts/import_prices.py data/staging/prices/example.csv --legacy
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.ingest import load_all_records, reports_dir  # noqa: E402
from core.paths import price_enrich_dir, write_channel_enrich  # noqa: E402
from core.products import canonical_product_id, normalize_brand, normalize_model  # noqa: E402

PRICES_DIR = price_enrich_dir(for_write=True)


def _canonical_from_report_id(report_id: str) -> str | None:
    path = reports_dir() / f"{report_id}.json"
    if path.exists():
        try:
            r = json.loads(path.read_text(encoding="utf-8"))
            brand = normalize_brand(r.get("brand") or "")
            model = normalize_model(r.get("model") or "", brand)
            return canonical_product_id(brand, model)
        except Exception:
            return None
    for r in load_all_records("report"):
        if r["id"] == report_id:
            brand = normalize_brand(r.get("brand") or "")
            model = normalize_model(r.get("model") or "", brand)
            return canonical_product_id(brand, model)
    return None


def _write_channel(canonical_id: str, row: dict) -> None:
    payload = {
        "canonical_id": canonical_id,
        "price_cny": float(row["price_cny"]) if row.get("price_cny") else (float(row["price_amount"]) if row.get("price_amount") and (row.get("price_currency") or "CNY").upper() == "CNY" else None),
        "price_amount": float(row["price_amount"]) if row.get("price_amount") else None,
        "price_currency": (row.get("price_currency") or "CNY").upper(),
        "price_kind": row.get("price_kind") or "channel",
        "price_label": row.get("price_label") or "",
        "price_source": row.get("price_source") or row.get("channel") or "manual_csv",
        "channel_url": row.get("channel_url") or row.get("price_url") or "",
        "sales_hint": row.get("sales_hint") or "",
        "price_note": row.get("price_note") or "",
        "captured_at": datetime.now(timezone.utc).date().isoformat(),
        "source_layer": "channel",
    }
    write_channel_enrich(canonical_id, payload)


def _write_legacy_price(report_id: str, row: dict) -> None:
    PRICES_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "price_cny": float(row["price_cny"]) if row.get("price_cny") else None,
        "price_source": row.get("price_source", "manual_csv"),
        "price_url": row.get("price_url") or row.get("channel_url") or "",
        "price_note": row.get("price_note", ""),
        "price_captured_at": datetime.now(timezone.utc).date().isoformat(),
    }
    (PRICES_DIR / f"{report_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", help="售价 CSV 文件路径")
    parser.add_argument("--legacy", action="store_true", help="按旧字段格式写入 data/staging/prices/{report_id}.json")
    args = parser.parse_args()

    n_channel = 0
    n_legacy = 0
    with open(args.csv_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            cid = (row.get("canonical_id") or "").strip()
            report_id = (row.get("id") or "").strip()

            if args.legacy and report_id:
                _write_legacy_price(report_id, row)
                n_legacy += 1
                continue

            if not cid and report_id:
                cid = _canonical_from_report_id(report_id) or ""
            if not cid:
                continue
            _write_channel(cid, row)
            n_channel += 1

    print(f"Imported channel={n_channel}, legacy_prices={n_legacy}")


if __name__ == "__main__":
    main()
