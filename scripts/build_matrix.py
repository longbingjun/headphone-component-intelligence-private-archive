"""按品类生成成本工程师优先的竞品矩阵 JSON 与同品类对比 JSON。"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.cost_extract import extract_cost_fields  # noqa: E402
from core.ingest import load_all_records, merge_price_into_record  # noqa: E402
from core.paths import compare_dir, matrix_dir, products_dir  # noqa: E402
from core.products import canonical_product_id, load_channel_enrich, normalize_brand, normalize_model  # noqa: E402
from core.scope import HEADPHONE_CATEGORIES  # noqa: E402

MATRIX_DIR = matrix_dir(for_write=True)
COMPARE_DIR = compare_dir(for_write=True)
PRODUCTS_DIR = products_dir()

# V4 成本工程师矩阵列
COST_MATRIX_COLUMNS = [
    "brand",
    "model",
    "launch_date",
    "price_cny",
    "main_chip",
    "pmic",
    "battery_ear",
    "battery_case",
    "speaker",
    "materials",
    "weight_g",
    "weight_case_g",
    "weight_earbud_g",
    "ip_rating",
    "bluetooth",
    "bom_rows",
    "layer_badges",
    "data_completeness",
]

COMPARE_PARAM_ROWS = [
    "launch_date",
    "price_cny",
    "main_chip",
    "pmic",
    "battery_ear",
    "battery_case",
    "speaker",
    "materials",
    "weight_g",
    "weight_case_g",
    "weight_earbud_g",
    "ip_rating",
    "bluetooth",
    "bom_rows",
    "selling_point_tags",
    "scenarios",
    "positioning_summary",
]

# 定位摘要在对比表格里只展示一行，超长时截断（详情仍可在产品页看全文）
_POSITIONING_SUMMARY_MAX_LEN = 60


def _category_filename(category: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*]', "_", category.strip())
    return f"{safe}.json"


def _layer_badges(layer_refs: dict) -> str:
    badges = []
    for layer, refs in (layer_refs or {}).items():
        if refs:
            badges.append(layer)
    return "、".join(badges)


def _price_display(price: float | None, layer: str | None, source_label: str | None = None, currency: str | None = None) -> str:
    if price is None:
        return ""
    currency = str(currency or "CNY").upper()
    prefix = {"CNY": "¥", "USD": "US$", "EUR": "€", "GBP": "£", "JPY": "JP¥", "KRW": "₩"}.get(currency, f"{currency} ")
    txt = f"{prefix}{price}"
    fallback_labels = {"channel": "渠道价", "technical": "技术文章价", "official": "官方价"}
    label = source_label or fallback_labels.get(layer or "", "")
    return f"{txt}（{label}）" if label else txt


def _market_fields(product: dict) -> dict:
    """从产品 market 快照提取对比表可用的展示字段（卖点标签 / 场景 / 定位摘要）。"""
    market = product.get("market") or {}
    tags: list[str] = []
    for sp in market.get("selling_points") or []:
        tag = (sp or {}).get("tag")
        if tag and tag not in tags:
            tags.append(tag)
    scenarios = list(market.get("scenarios") or [])
    summary = market.get("positioning_summary") or ""
    if len(summary) > _POSITIONING_SUMMARY_MAX_LEN:
        summary = summary[:_POSITIONING_SUMMARY_MAX_LEN] + "…"
    return {
        "selling_point_tags": "、".join(tags),
        "scenarios": "、".join(scenarios),
        "positioning_summary": summary,
    }


def build_matrix() -> dict:
    MATRIX_DIR.mkdir(parents=True, exist_ok=True)
    COMPARE_DIR.mkdir(parents=True, exist_ok=True)

    if not PRODUCTS_DIR.exists():
        return {"matrices": 0, "files": []}

    by_category: dict[str, list[dict]] = defaultdict(list)

    for path in sorted(PRODUCTS_DIR.glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            product = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        cid = product["canonical_id"]
        category = product.get("category", "")
        if category not in HEADPHONE_CATEGORIES:
            continue
        snap = product.get("cost_snapshot") or {}
        fields = {}
        best_rid = snap.get("best_report_id")
        if best_rid:
            report_path = ROOT / "data" / "reports" / f"{best_rid}.json"
            if report_path.exists():
                try:
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                    report = merge_price_into_record(report)
                    fields = extract_cost_fields(report.get("views") or {})
                except Exception:
                    pass

        channel = load_channel_enrich(cid)
        price = snap.get("price_cny")
        price_layer = snap.get("price_layer")
        price_currency = snap.get("price_currency") or "CNY"
        launch = product.get("launch") or {}
        if channel and channel.get("price_cny") is not None:
            price = channel["price_cny"]
            price_layer = "channel"
            price_currency = channel.get("price_currency") or "CNY"

        row = {
            "canonical_id": cid,
            "brand": product.get("brand", ""),
            "model": product.get("model", ""),
            "price_cny": price,
            "launch_date": launch.get("display") or "",
            "launch_source_url": launch.get("source_url") or "",
            "launch_evidence": launch.get("evidence") or "",
            "launch_source_layer": "official" if launch.get("source_type") in {"official_news", "official_product_page", "official_store"} else "technical",
            "price_currency": price_currency,
            "price_layer": price_layer,
            "price_source_label": snap.get("price_source_label"),
            "price_source_url": snap.get("price_source_url"),
            "price_evidence": snap.get("price_evidence"),
            "main_chip": snap.get("main_chip") or (fields.get("main_chip") or {}).get("value"),
            "pmic": snap.get("pmic_case") or (fields.get("pmic") or {}).get("value"),
            "battery_ear": snap.get("battery_ear") or (fields.get("battery_ear") or {}).get("value"),
            "battery_case": snap.get("battery_case") or (fields.get("battery_case") or {}).get("value"),
            "speaker": snap.get("speaker") or (fields.get("speaker") or {}).get("value"),
            "materials": snap.get("materials") or (fields.get("materials") or {}).get("value"),
            "weight_g": snap.get("weight_g") or (fields.get("weight_g") or {}).get("value"),
            "weight_case_g": snap.get("weight_case_g") or (fields.get("weight_case_g") or {}).get("value"),
            "weight_earbud_g": snap.get("weight_earbud_g") or (fields.get("weight_earbud_g") or {}).get("value"),
            "ip_rating": snap.get("ip_rating") or (fields.get("ip_rating") or {}).get("value"),
            "bluetooth": snap.get("bluetooth") or (fields.get("bluetooth") or {}).get("value"),
            "bom_rows": snap.get("bom_row_count") or len(product.get("bom_table") or []),
            "layer_badges": _layer_badges(product.get("layer_refs")),
            "data_completeness": snap.get("data_completeness"),
            "best_report_id": best_rid,
            "has_report": bool(product.get("report_ids")),
            "has_video": bool(product.get("video_ids")),
            "cost_fields": fields,
            **_market_fields(product),
        }
        by_category[category].append(row)

    written_names: set[str] = set()
    written = []
    for category, rows in sorted(by_category.items()):
        rows.sort(key=lambda r: (r.get("brand", ""), r.get("model", "")))
        payload = {
            "category": category,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "columns": COST_MATRIX_COLUMNS,
            "rows": rows,
        }
        out_path = MATRIX_DIR / _category_filename(category)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        written_names.add(_category_filename(category))
        written.append({"category": category, "rows": len(rows), "path": str(out_path)})

        # 对比 JSON：行=参数，列=产品
        compare_cols = []
        compare_rows = []
        for row in rows:
            cells = {}
            for param in COMPARE_PARAM_ROWS:
                val = row.get(param)
                if param == "price_cny":
                    display = _price_display(val, row.get("price_layer"), row.get("price_source_label"), row.get("price_currency"))
                elif param == "launch_date":
                    display = str(val or "")
                elif param == "bom_rows":
                    display = str(val) if val is not None else ""
                else:
                    display = str(val) if val else ""
                field_ev = (row.get("cost_fields") or {}).get(param, {})
                if param == "price_cny":
                    evidence = row.get("price_evidence") or ""
                    source_layer = row.get("price_layer") or "technical"
                    source_url = row.get("price_source_url") or ""
                elif param == "launch_date":
                    evidence = row.get("launch_evidence") or ""
                    source_layer = row.get("launch_source_layer") or "technical"
                    source_url = row.get("launch_source_url") or ""
                else:
                    evidence = field_ev.get("evidence", "") if isinstance(field_ev, dict) else ""
                    source_layer = field_ev.get("source_layer", "technical") if isinstance(field_ev, dict) else "technical"
                    source_url = ""
                cells[param] = {
                    "value": display,
                    "evidence": evidence,
                    "source_layer": source_layer,
                    "source_url": source_url,
                }
            compare_cols.append(
                {
                    "canonical_id": row["canonical_id"],
                    "brand": row.get("brand"),
                    "model": row.get("model"),
                    "best_report_id": row.get("best_report_id"),
                    "cells": cells,
                }
            )

        for param in COMPARE_PARAM_ROWS:
            compare_rows.append(
                {
                    "param": param,
                    "cells": {c["canonical_id"]: c["cells"].get(param, {}) for c in compare_cols},
                }
            )

        compare_payload = {
            "category": category,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "param_rows": COMPARE_PARAM_ROWS,
            "products": compare_cols,
            "rows": compare_rows,
        }
        compare_path = COMPARE_DIR / _category_filename(category)
        compare_path.write_text(json.dumps(compare_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # 移除非耳机品类遗留矩阵/对比文件
    for folder in (MATRIX_DIR, COMPARE_DIR):
        if not folder.exists():
            continue
        for path in folder.glob("*.json"):
            if path.name not in written_names:
                path.unlink()

    return {"matrices": len(written), "files": written}


def main() -> None:
    stats = build_matrix()
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
