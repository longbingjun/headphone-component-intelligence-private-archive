"""扫描 reports + videos，生成产品主数据索引与单产品 JSON（含成本快照）。"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.compare import suggest_internal_match  # noqa: E402
from core.catalog_overrides import load_catalog_manual_overrides, source_override  # noqa: E402
from core.ingest import load_all_records, merge_price_into_record  # noqa: E402
from core.products import (  # noqa: E402
    canonical_product_id,
    guess_brand_from_text,
    merge_cost_snapshot,
    merge_launch_snapshot,
    merge_market_snapshot,
    load_identity_overrides,
    merge_unboxing_snapshot,
    normalize_brand,
    normalize_model,
)
from core.scope import HEADPHONE_CATEGORIES, is_headphone_record, normalize_headphone_category  # noqa: E402
from core.earphone_categories import load_category_overrides, product_category_fields  # noqa: E402

from core.paths import (
    last_step_stats,
    products_dir,
    products_index_path,
    update_manifest,
    write_product_json,
    write_products_index,
)

# 单次构建产出的产品数相比上一次意外下降超过该比例时，视为可能的抓取/合并回归
DROP_ALERT_THRESHOLD = 0.3
ROUNDUP_MARKERS = ("汇总", "盘点", "年度报告", "给你答案")
HEADPHONE_MARKERS = ("耳机", "TWS", "OWS", "蓝牙")


def _is_roundup_report(record: dict) -> bool:
    """Return true for multi-product research roundups, never single SKUs."""
    title = str(record.get("title") or record.get("product_title") or "")
    return bool(
        any(marker in title for marker in ROUNDUP_MARKERS)
        and any(marker.lower() in title.lower() for marker in HEADPHONE_MARKERS)
    )


def _check_product_count_regression(new_count: int) -> None:
    """对比上一次 build_products 的产品数，产出异常骤降时发出警示（CI 环境下直接失败）。"""
    prev_stats = last_step_stats("build_products")
    if not prev_stats:
        return
    prev_count = prev_stats.get("products")
    if not prev_count:
        return
    drop_ratio = (prev_count - new_count) / prev_count
    if drop_ratio > DROP_ALERT_THRESHOLD:
        message = (
            f"[build_products] 数据质量警告：产品数从 {prev_count} 骤降至 {new_count} "
            f"（降幅 {drop_ratio:.0%}，超过 {DROP_ALERT_THRESHOLD:.0%} 阈值），"
            "可能是抓取失败或合并逻辑回归，请检查后再发布"
        )
        print(message, file=sys.stderr)
        if os.environ.get("CI"):
            sys.exit(1)


def _record_identity(record: dict) -> tuple[str, str, str]:
    brand = normalize_brand(record.get("brand") or record.get("views", {}).get("market", {}).get("brand", ""))
    model = record.get("model") or record.get("views", {}).get("market", {}).get("model", "")
    title = record.get("title") or record.get("product_title", "")

    if not brand:
        brand = guess_brand_from_text(title) or guess_brand_from_text(model)
    model = normalize_model(model, brand)
    if not model and title:
        model = normalize_model(title.replace(brand, "", 1) if brand else title, brand)

    text = title or f"{brand} {model}".strip()
    existing = (
        record.get("category")
        or record.get("views", {}).get("market", {}).get("category")
        or record.get("views", {}).get("structure", {}).get("form_factor", "")
        or ""
    )
    category = normalize_headphone_category(text, existing)
    return brand, model, category


def _pick_category(categories: list[str]) -> str:
    if not categories:
        return "真无线耳机TWS"
    hp = [c for c in categories if c in HEADPHONE_CATEGORIES]
    pool = hp or categories
    counts: dict[str, int] = defaultdict(int)
    for c in pool:
        counts[c] += 1
    return max(counts, key=lambda k: (counts[k], k))


def _identity_candidates(record: dict, overrides: dict[str, dict]) -> list[tuple[str, str, str]]:
    """Use source-grounded identity repairs without mutating the raw article.

    One article may describe several headphones.  A reviewed split therefore
    deliberately produces several product aggregates that share the same report.
    """
    override = overrides.get(str(record.get("id") or "")) or {}
    decision = str(override.get("decision") or "")
    if decision == "not_headphone":
        return []
    if decision in {"single_headphone", "multi_headphone"}:
        resolved: list[tuple[str, str, str]] = []
        for item in override.get("products") or []:
            if float(item.get("confidence") or 0) < 0.9:
                continue
            brand = normalize_brand(str(item.get("brand") or ""))
            model = normalize_model(str(item.get("model") or ""), brand)
            # Identity repair is responsible for brand/model. Keep an already
            # normalized source category instead of letting the model's free-
            # text category (for example "主动降噪耳机") invalidate an otherwise
            # evidence-backed identity.
            source_category = str(record.get("category") or "")
            category_hint = source_category if source_category in HEADPHONE_CATEGORIES else str(item.get("category") or "")
            category = normalize_headphone_category(
                f"{brand} {model} {record.get('title') or ''}", category_hint
            )
            if brand and model and category in HEADPHONE_CATEGORIES:
                resolved.append((brand, model, category))
        if resolved:
            return resolved
    return [_record_identity(record)]


def build_products() -> dict:
    products_dir(for_write=True)

    reports_by_id: dict[str, dict] = {}
    for r in load_all_records("report"):
        reports_by_id[r["id"]] = merge_price_into_record(r)

    videos_by_id: dict[str, dict] = {}
    for v in load_all_records("video"):
        videos_by_id[v["id"]] = v

    aggregates: dict[str, dict] = {}
    identity_overrides = load_identity_overrides()
    category_overrides = load_category_overrides()
    manual_overrides = load_catalog_manual_overrides()

    for kind in ("report", "video"):
        for record in load_all_records(kind):
            reviewed = source_override(kind, record, manual_overrides)
            if reviewed.get("action") in {"exclude_catalog", "related_intelligence_only"}:
                continue
            if not is_headphone_record(record):
                # A reviewed category can deliberately recover a source that
                # the old coarse title classifier put in "other audio".
                if not (reviewed.get("category") or {}).get("primary"):
                    continue
            if kind == "report" and _is_roundup_report(record):
                # Keep the original report in the research library, but never
                # turn a multi-product roundup title into a synthetic SKU.
                continue
            if kind == "report":
                record = reports_by_id.get(record["id"], record)
            reviewed_identity = reviewed.get("identity") or {}
            if reviewed_identity.get("brand") and reviewed_identity.get("model"):
                reviewed_brand = normalize_brand(str(reviewed_identity["brand"]))
                reviewed_model = normalize_model(str(reviewed_identity["model"]), reviewed_brand)
                reviewed_category = str((reviewed.get("category") or {}).get("primary") or "")
                reviewed_category = normalize_headphone_category(
                    f"{reviewed_brand} {reviewed_model} {record.get('title') or ''}",
                    reviewed_category,
                )
                candidates = [(reviewed_brand, reviewed_model, reviewed_category)]
            else:
                candidates = _identity_candidates(record, identity_overrides) if kind == "report" else [_record_identity(record)]
            for brand, model, category in candidates:
                reviewed_category = str((reviewed.get("category") or {}).get("primary") or "")
                if reviewed_category:
                    category = reviewed_category
                if category not in HEADPHONE_CATEGORIES:
                    continue
                cid = canonical_product_id(brand, model)
                published = record.get("published_at", "")

                if cid not in aggregates:
                    aggregates[cid] = {
                        "canonical_id": cid,
                        "brand": brand,
                        "model": model,
                        "categories": [],
                        "report_ids": [],
                        "video_ids": [],
                        "published_dates": [],
                        "market_prices": [],
                        "reviewed_categories": [],
                        "category_facets": [],
                        "category_details": [],
                        "usage_tags": [],
                        "reviewed_sources": [],
                    }

                agg = aggregates[cid]
                if brand and not agg["brand"]:
                    agg["brand"] = brand
                if model and (not agg["model"] or agg["model"] == "unknown"):
                    agg["model"] = model
                agg["categories"].append(category)
                reviewed_taxonomy = reviewed.get("category") or {}
                if reviewed_taxonomy.get("primary"):
                    agg["reviewed_categories"].append(str(reviewed_taxonomy["primary"]))
                    agg["category_facets"].extend(
                        str(value) for value in reviewed_taxonomy.get("facets") or [] if value
                    )
                    detail = str(reviewed_taxonomy.get("detail") or "").strip()
                    if detail:
                        agg["category_details"].append(detail)
                    agg["usage_tags"].extend(
                        str(value) for value in reviewed_taxonomy.get("usage_tags") or [] if value
                    )
                    agg["reviewed_sources"].append(f"{kind}:{record.get('id')}")
                agg["published_dates"].append(published)
                if kind == "report":
                    agg["report_ids"].append(record["id"])
                    mkt = (record.get("views") or {}).get("market") or {}
                    if mkt.get("price_cny") is not None:
                        agg["market_prices"].append(mkt["price_cny"])
                else:
                    agg["video_ids"].append(record["id"])

    index_items: list[dict] = []
    for cid, agg in sorted(aggregates.items()):
        report_ids = sorted(set(agg["report_ids"]))
        video_ids = sorted(set(agg["video_ids"]))
        dates = sorted(d for d in agg["published_dates"] if d)
        category_raw = _pick_category(agg["categories"])
        if agg["reviewed_categories"]:
            category = _pick_category(agg["reviewed_categories"])
            manual_review_version = str(
                manual_overrides.get("version") or "catalog-manual-review"
            )
            category_fields = {
                "category": category,
                "category_raw": category_raw,
                "category_status": "human_reviewed",
                "category_basis": "accepted_manual_review",
                "category_confidence": "high",
                "category_evidence_quote": "",
                "category_source_report_id": "",
                "category_classifier_version": manual_review_version,
            }
        else:
            category_fields = product_category_fields(cid, category_raw, category_overrides)
        category = category_fields["category"]
        brand = agg["brand"]
        model = agg["model"]
        market_price = agg["market_prices"][0] if agg["market_prices"] else None

        cost_data = merge_cost_snapshot(
            canonical_id=cid,
            report_ids=report_ids,
            video_ids=video_ids,
            reports_by_id=reports_by_id,
            videos_by_id=videos_by_id,
            market_price=market_price,
        )

        unboxing = merge_unboxing_snapshot(report_ids)
        market = merge_market_snapshot(
            canonical_id=cid,
            report_ids=report_ids,
            video_ids=video_ids,
            reports_by_id=reports_by_id,
            videos_by_id=videos_by_id,
        )
        launch = merge_launch_snapshot(cid, dates[0] if dates else "", market)

        product = {
            "canonical_id": cid,
            "brand": brand,
            "model": model,
            "category": category,
            **category_fields,
            "category_facets": list(dict.fromkeys([category, *agg["category_facets"]])),
            "category_details": list(dict.fromkeys(agg["category_details"])),
            "usage_tags": list(dict.fromkeys(agg["usage_tags"])),
            "manual_review_sources": list(dict.fromkeys(agg["reviewed_sources"])),
            "report_ids": report_ids,
            "video_ids": video_ids,
            "related_report_ids": report_ids,
            "related_video_ids": video_ids,
            "first_seen": dates[0] if dates else "",
            "latest_published": dates[-1] if dates else "",
            "internal_match_suggestions": suggest_internal_match(brand, model, category),
            "cost_snapshot": cost_data["cost_snapshot"],
            "bom_table": cost_data["bom_table"],
            "teardown_inventory": cost_data["teardown_inventory"],
            "technical_facts": cost_data["technical_facts"],
            "data_quality_review_queue": cost_data["data_quality_review_queue"],
            "summary_image_urls": cost_data["summary_image_urls"],
            "report_image_urls": cost_data["report_image_urls"],
            "summary_text": cost_data["summary_text"],
            "layer_refs": cost_data["layer_refs"],
            "unboxing": unboxing,
            "market": market,
            "launch": launch,
        }
        write_product_json(cid, product)
        index_items.append(
            {
                "canonical_id": cid,
                "brand": brand,
                "model": model,
                "category": category,
                "category_raw": category_fields["category_raw"],
                "category_status": category_fields["category_status"],
                "category_basis": category_fields["category_basis"],
                "category_confidence": category_fields["category_confidence"],
                "category_facets": product["category_facets"],
                "category_details": product["category_details"],
                "usage_tags": product["usage_tags"],
                "report_count": len(report_ids),
                "video_count": len(video_ids),
                "first_seen": product["first_seen"],
                "latest_published": product["latest_published"],
                "cost_completeness": cost_data["cost_snapshot"].get("data_completeness"),
                "bom_row_count": cost_data["cost_snapshot"].get("bom_row_count"),
                "launch_date": launch.get("date"),
                "launch_display": launch.get("display"),
                "launch_status": launch.get("status"),
            }
        )

    index_items.sort(key=lambda x: x.get("latest_published", ""), reverse=True)
    index = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "count": len(index_items),
        "products": index_items,
    }
    write_products_index(index)
    _check_product_count_regression(len(index_items))
    update_manifest(step="build_products", stats={"products": len(index_items)})

    # 移除不再生成的产品 JSON（非耳机等）
    keep_ids = set(aggregates.keys())
    for base in (products_dir(),):
        if not base.exists():
            continue
        for path in base.glob("*.json"):
            if path.name == "index.json" or path.stem in keep_ids:
                continue
            path.unlink()

    idx_path = products_index_path()
    return {"products": len(index_items), "index_path": str(idx_path)}


def main() -> None:
    stats = build_products()
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
