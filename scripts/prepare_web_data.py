"""将 data/ 下的 JSON 同步到 web/public/data，供 Astro 构建使用。"""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(ROOT))

from core.ingest import load_all_records  # noqa: E402
from core.component_analytics import (  # noqa: E402
    SUPPORTED_COMPONENTS,
    brand_supply_manifest,
    brand_supply_payload,
    component_payload,
    manifest_payload,
    rows_from_products,
    supplier_supply_manifest,
    supplier_supply_payload,
)
from core.paths import (
    WEB_DATA,
    compare_dir,
    component_manufacturers_enrich_dir,
    last_step_stats,
    products_dir,
    products_index_path,
)
from core.release_metadata import RELEASE_GENERATED_AT, release_stamp  # noqa: E402
from core.extract.consumer_claims import is_publishable_consumer_claim  # noqa: E402
from core.scope import is_headphone_record  # noqa: E402

# 单次构建产出的拆解报告数相比上一次意外下降超过该比例时，视为可能的抓取/合并回归
DROP_ALERT_THRESHOLD = 0.3

# 汇总/盘点类文章记录的是一个年度或品类的研究样本，不是单一可售 SKU。
# 它们保留在原始报告中，并由产品构建步骤排除出 SKU 聚合；这里单独导出为首页洞察。
ROUNDUP_MARKERS = ("汇总", "盘点", "年度报告", "给你答案")
HEADPHONE_MARKERS = ("耳机", "TWS", "OWS", "蓝牙")
ROUNDUP_SUMMARIES_DIR = ROOT / "data" / "staging" / "roundup_summaries"
LEGACY_ROUNDUP_SUMMARIES_DIR = ROOT / "data" / "enrich" / "roundup_summaries"


def _native_path(path: Path) -> Path:
    """Return a Windows extended path when the repository path is very long."""
    if os.name == "nt" and not str(path).startswith("\\\\?\\"):
        return Path(f"\\\\?\\{path.resolve()}")
    return path


def _read_text(path: Path) -> str:
    """Read UTF-8 text while supporting repository paths over 260 chars on Windows."""
    return _native_path(path).read_text(encoding="utf-8")


def _check_teardown_count_regression(new_count: int) -> None:
    prev_stats = last_step_stats("prepare_web_data")
    if not prev_stats:
        return
    prev_count = prev_stats.get("teardown_reports")
    if not prev_count:
        return
    drop_ratio = (prev_count - new_count) / prev_count
    if drop_ratio > DROP_ALERT_THRESHOLD:
        message = (
            f"[prepare_web_data] 数据质量警告：拆解报告数从 {prev_count} 骤降至 {new_count} "
            f"（降幅 {drop_ratio:.0%}，超过 {DROP_ALERT_THRESHOLD:.0%} 阈值），"
            "可能是抓取失败或合并逻辑回归，请检查后再发布"
        )
        print(message, file=sys.stderr)
        if os.environ.get("CI"):
            sys.exit(1)
DATA = ROOT / "data"
SITE = ROOT / "site"

# V3/V4 多角色静态页目录（V5 Astro 不再生成，构建前清理避免 Pages 残留旧入口）
LEGACY_SITE_PATHS = (
    "reports",
    "videos",
    "compare",
    "matrix",
    "products",
    "about.html",
)


def _slug(category: str) -> str:
    return re.sub(r'[<>:"/\\|?*\s]+', "-", category.strip()).strip("-") or "other"


def clean_legacy_site() -> list[str]:
    """移除旧版多角色静态页，避免与 V5 成本工作台并存。"""
    removed: list[str] = []
    if not SITE.exists():
        return removed
    for rel in LEGACY_SITE_PATHS:
        target = SITE / rel
        if not target.exists():
            continue
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        removed.append(rel)
    return removed


def _teardown_list_item(record: dict, *, kind: str) -> dict:
    title = record.get("title") or record.get("product_title") or ""
    publisher = record.get("publisher") or record.get("author") or record.get("source_site") or ""
    brand = record.get("brand") or (record.get("views") or {}).get("market", {}).get("brand", "")
    item = {
        "id": record.get("id", ""),
        "kind": kind,
        "title": title,
        "publisher": publisher,
        "published_at": record.get("published_at", ""),
        "url": record.get("url", ""),
        "category": record.get("category", ""),
        "brand": brand,
    }
    return item


def _build_teardown_manifest() -> dict:
    reports = [
        _teardown_list_item(r, kind="report")
        for r in load_all_records("report")
        if is_headphone_record(r)
    ]
    videos = [
        _teardown_list_item(v, kind="video")
        for v in load_all_records("video")
        if is_headphone_record(v)
    ]
    reports.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    videos.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    return {
        "generated_at": RELEASE_GENERATED_AT,
        "report_count": len(reports),
        "video_count": len(videos),
        "reports": reports,
        "videos": videos,
    }


def _is_roundup_report(record: dict) -> bool:
    title = str(record.get("title") or record.get("product_title") or "")
    return bool(
        any(marker in title for marker in ROUNDUP_MARKERS)
        and any(marker.lower() in title.lower() for marker in HEADPHONE_MARKERS)
    )


def _roundup_kind(title: str) -> str:
    if any(marker in title for marker in ("应用案例", "方案", "芯片", "电感")):
        return "方案案例"
    if "年度" in title or re.search(r"20\d{2}", title):
        return "年度汇总"
    return "品类汇总"


def _local_image_path(url: str) -> str:
    """Return the configured public path for a deterministic WebP derivative."""
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    image_base = os.environ.get("PUBLIC_IMAGE_BASE", "/images").rstrip("/") or "/images"
    return f"{image_base}/{digest}.webp"


INDEX_PRODUCT_KEYS = {
    "canonical_id", "brand", "model", "category", "report_count", "video_count",
    "category_raw", "category_status", "category_basis", "category_confidence",
    "first_seen", "latest_published", "cost_completeness", "bom_row_count",
    "launch_date", "launch_display", "launch_status", "research_priority",
    "priority_rank", "official_page_status", "official_page_url",
}

PUBLIC_PRODUCT_KEYS = {
    "canonical_id", "brand", "model", "category", "cost_snapshot", "launch",
    "first_seen", "latest_published",
    "category_raw", "category_status", "category_basis", "category_confidence",
    "category_evidence_quote", "category_source_report_id", "category_classifier_version",
    "market", "bom_table", "teardown_inventory", "technical_facts",
    "data_quality_review_queue", "unboxing",
    "summary_image_urls", "report_image_urls", "report_ids", "video_ids",
}


def _write_web_json(path: Path, payload: object) -> None:
    """Write compact generated JSON; source JSON remains human-readable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, dict):
        payload = {**payload, "_release": release_stamp()}
    _native_path(path).write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def _trim_images(images: object, limit: int = 9999) -> list[dict]:
    """Keep all images (limit is respected but set high enough to include everything)."""
    if not isinstance(images, list):
        return []
    trimmed = []
    for item in images:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        copy = dict(item)
        copy["public_path"] = _local_image_path(str(item["url"]))
        trimmed.append(copy)
        if len(trimmed) >= limit:
            break
    return trimmed


def _trim_teardown_inventory(rows: object) -> list[dict]:
    """Attach deployable media paths to nested BOM evidence without mutating source JSON."""

    if not isinstance(rows, list):
        return []
    output = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        copy = dict(row)
        copy["evidence_images"] = _trim_images(row.get("evidence_images"))
        output.append(copy)
    return output


def _related_intelligence_payload(
    product: dict,
    reports_by_id: dict[str, dict],
    videos_by_id: dict[str, dict],
) -> list[dict]:
    """Build a small, source-backed provenance list for the product overview."""
    related: list[dict] = []
    for kind, identifiers, records in (
        ("report", product.get("report_ids") or [], reports_by_id),
        ("video", product.get("video_ids") or [], videos_by_id),
    ):
        for identifier in identifiers:
            source_id = str(identifier)
            record = records.get(source_id) or {}
            source_url = str(record.get("source_url") or record.get("url") or "").strip()
            video_embed_url = str(record.get("video_embed_url") or "").strip()
            bvid_match = re.search(r"(?:[?&]bvid=)(BV[0-9A-Za-z]+)", video_embed_url)
            related.append(
                {
                    "kind": kind,
                    "id": source_id,
                    "title": str(record.get("title") or record.get("product_title") or source_id),
                    "published_at": str(record.get("published_at") or ""),
                    "publisher": str(
                        record.get("publisher")
                        or record.get("author")
                        or record.get("source_site")
                        or ""
                    ),
                    "source_url": source_url,
                    "video_url": (
                        f"https://www.bilibili.com/video/{bvid_match.group(1)}/"
                        if bvid_match
                        else ""
                    ),
                }
            )
    return sorted(
        related,
        key=lambda item: (item.get("published_at") or "", item.get("id") or ""),
        reverse=True,
    )


def _public_product_payload(
    product: dict,
    reports_by_id: dict[str, dict] | None = None,
    videos_by_id: dict[str, dict] | None = None,
) -> dict:
    """Keep only fields used by the Astro detail and compare experiences."""
    payload = {key: product.get(key) for key in PUBLIC_PRODUCT_KEYS if key in product}
    market = product.get("market")
    if isinstance(market, dict):
        public_market = dict(market)
        for field in ("consumer_claims", "selling_points"):
            rows = market.get(field)
            if not isinstance(rows, list):
                continue
            public_market[field] = [
                row
                for row in rows
                if isinstance(row, dict)
                and is_publishable_consumer_claim(
                    row.get("text") or row.get("claim"),
                    row.get("category") or row.get("tag"),
                )
            ]
        payload["market"] = public_market
    payload["summary_image_urls"] = _trim_images(product.get("summary_image_urls"))
    payload["report_image_urls"] = _trim_images(product.get("report_image_urls"))
    payload["teardown_inventory"] = _trim_teardown_inventory(
        product.get("teardown_inventory")
    )
    payload["technical_facts"] = _trim_teardown_inventory(
        product.get("technical_facts")
    )
    payload["related_intelligence"] = _related_intelligence_payload(
        product,
        reports_by_id or {},
        videos_by_id or {},
    )
    unboxing = product.get("unboxing")
    if isinstance(unboxing, dict):
        trimmed_unboxing = {"completeness": unboxing.get("completeness")}
        for section_name in ("packaging", "charging_case", "earbuds"):
            section = unboxing.get(section_name)
            if not isinstance(section, dict):
                continue
            trimmed_section = {
                "description": section.get("description"),
                "accessories": section.get("accessories") or [],
                "image_count": section.get("image_count"),
                "teardown_image_count": section.get("teardown_image_count"),
                "appearance_images": _trim_images(section.get("appearance_images")),
                "display_bullets": section.get("display_bullets") or [],
                "summary_meta": section.get("summary_meta") or {},
            }
            trimmed_unboxing[section_name] = trimmed_section
        payload["unboxing"] = trimmed_unboxing
    return payload


def _card_image_url(product: dict) -> str:
    """Score unboxing appearance images and return the best candidate URL."""
    unboxing = product.get("unboxing") or {}
    candidates: list[tuple[int, str]] = []
    for section_name, base_score in (("earbuds", 6), ("charging_case", 4), ("packaging", 2)):
        section = unboxing.get(section_name) or {}
        for image in _trim_images(section.get("appearance_images")):
            url = str(image.get("url") or "").strip()
            if not url:
                continue
            description = f"{image.get('alt') or ''} {image.get('caption') or ''}"
            bonus = 20 if re.search(r"整体|全貌|全景|外观|一览|展示|真机|佩戴|正面|侧面|背面", description) else 0
            penalty = 12 if re.search(r"特写|内部|拆解|芯片|主板|电池|接口|触点|铭牌|参数|包装盒", description) else 0
            candidates.append((base_score + bonus - penalty, url))
    return max(candidates, default=(0, ""), key=lambda item: item[0])[1]


def _roundup_digest(report_id: str) -> dict:
    path = ROUNDUP_SUMMARIES_DIR / f"{report_id}.json"
    if not path.exists():
        path = LEGACY_ROUNDUP_SUMMARIES_DIR / f"{report_id}.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(_read_text(path))
    except (OSError, json.JSONDecodeError):
        return {}
    images = []
    for image in payload.get("image_highlights") or []:
        if not isinstance(image, dict) or not str(image.get("url") or "").strip():
            continue
        url = str(image["url"])
        images.append({
            "url": url,
            "local_path": _local_image_path(url),
            "caption": str(image.get("caption") or image.get("description") or "原文配图").strip(),
        })
    findings = []
    for item in payload.get("key_findings") or []:
        if isinstance(item, dict) and str(item.get("text") or "").strip():
            findings.append({"title": str(item.get("title") or "关键发现").strip(), "text": str(item["text"]).strip()})
    return {"overview": str(payload.get("overview") or "").strip(), "key_findings": findings, "image_highlights": images}


def _roundup_list_item(record: dict) -> dict:
    """Return source-grounded content for a roundup report card.

    The summary is captured from the original 52audio report. We deliberately
    do not generate a new interpretation here, so homepage content stays
    traceable to the report and can link back to it directly.
    """
    title = str(record.get("title") or record.get("product_title") or "")
    summary = re.sub(r"\s+", " ", str(record.get("summary") or "")).strip()
    report_id = str(record.get("id") or "")
    return {
        "id": report_id,
        "title": title,
        "published_at": record.get("published_at", ""),
        "url": record.get("url", ""),
        "category": record.get("category", ""),
        "author": record.get("author", ""),
        "kind": _roundup_kind(title),
        "summary": summary,
        "digest": _roundup_digest(report_id),
    }


def _build_roundup_manifest() -> dict:
    reports = [_roundup_list_item(record) for record in load_all_records("report") if _is_roundup_report(record)]
    reports.sort(key=lambda item: item.get("published_at", ""), reverse=True)
    years = sorted(
        {str(item.get("published_at") or "")[:4] for item in reports if str(item.get("published_at") or "")[:4]},
        reverse=True,
    )
    return {
        "generated_at": RELEASE_GENERATED_AT,
        "report_count": len(reports),
        "years": years,
        "reports": reports,
    }


def prepare() -> dict:
    # Astro empties its output directory during a real build. Runtime refreshes
    # only regenerate JSON and must never delete already-published routes or
    # hashed CSS/JavaScript from the immutable application image.
    legacy_removed = (
        clean_legacy_site()
        if os.environ.get("PREPARE_CLEAN_LEGACY_SITE", "").strip().lower() in {"1", "true", "yes"}
        else []
    )
    if WEB_DATA.exists():
        shutil.rmtree(_native_path(WEB_DATA))
    WEB_DATA.mkdir(parents=True, exist_ok=True)
    # site/ 不再纳入 git 跟踪，全新 checkout 时目录本身不存在，需先创建
    SITE.mkdir(parents=True, exist_ok=True)
    # 确保 GitHub Pages（Jekyll）不忽略 _astro 等目录
    (SITE / ".nojekyll").touch(exist_ok=True)

    # compare
    compare_src = compare_dir()
    compare_dst = WEB_DATA / "compare"
    compare_dst.mkdir(parents=True, exist_ok=True)
    categories = []
    if compare_src.exists():
        for path in sorted(compare_src.glob("*.json")):
            try:
                payload = json.loads(_read_text(path))
                name = payload.get("category", path.stem)
            except Exception:
                payload = {}
                name = path.stem
            _write_web_json(compare_dst / path.name, payload)
            products = payload.get("products", []) if isinstance(payload, dict) else []
            categories.append(
                {
                    "name": name,
                    "slug": _slug(name),
                    "file": path.name,
                    "product_count": len(products),
                }
            )

    reports_by_id = {
        str(report.get("id") or ""): report for report in load_all_records("report")
    }
    videos_by_id = {
        str(video.get("id") or ""): video for video in load_all_records("video")
    }

    # products index + files
    products_dst = WEB_DATA / "products"
    products_dst.mkdir(parents=True, exist_ok=True)
    products_src = products_dir()
    idx_src = products_index_path()
    n_products = 0
    card_image_by_id: dict[str, str] = {}
    source_products: list[dict] = []
    if products_src.exists():
        for path in products_src.glob("*.json"):
            if path.name == "index.json":
                continue
            try:
                product = json.loads(_read_text(path))
            except (OSError, json.JSONDecodeError):
                continue
            source_products.append(product)
            _write_web_json(
                products_dst / path.name,
                _public_product_payload(product, reports_by_id, videos_by_id),
            )
            card_url = _card_image_url(product)
            if card_url:
                card_image_by_id[str(product.get("canonical_id") or path.stem)] = _local_image_path(card_url)
            n_products += 1

    source_index: dict = {}
    if idx_src.exists():
        source_index = json.loads(_read_text(idx_src))
    public_index = {
        "generated_at": RELEASE_GENERATED_AT,
        "count": source_index.get("count", n_products),
        "products": [],
    }
    for product in source_index.get("products") or []:
        item = {key: product.get(key) for key in INDEX_PRODUCT_KEYS if key in product}
        card_path = card_image_by_id.get(str(product.get("canonical_id") or ""))
        if card_path:
            item["card_image_path"] = card_path
        public_index["products"].append(item)
    _write_web_json(products_dst / "index.json", public_index)

    # Analysis files are split by normalized component type so the browser only
    # downloads the selected family. Business rows intentionally omit system
    # extraction timestamps; source report publication date remains available.
    manufacturer_enrichments: dict[str, dict[str, dict]] = {}
    manufacturer_enrich_dir = component_manufacturers_enrich_dir()
    if manufacturer_enrich_dir.exists():
        for path in manufacturer_enrich_dir.glob("*.json"):
            try:
                payload = json.loads(_read_text(path))
            except (OSError, json.JSONDecodeError):
                continue
            product_id = str(payload.get("canonical_id") or path.stem)
            manufacturer_enrichments[product_id] = {
                str(item.get("key") or ""): item
                for item in payload.get("items") or []
                if isinstance(item, dict) and item.get("key")
            }
    component_rows = rows_from_products(
        source_products,
        reports_by_id,
        manufacturer_enrichments,
        videos_by_id,
    )
    for row in component_rows:
        image = row.get("evidence_image")
        if isinstance(image, dict) and str(image.get("url") or "").strip():
            image["public_path"] = _local_image_path(str(image["url"]))
    analysis_dst = WEB_DATA / "bom-analysis"
    analysis_dst.mkdir(parents=True, exist_ok=True)
    analysis_manifest = manifest_payload(component_rows)
    _write_web_json(analysis_dst / "manifest.json", analysis_manifest)
    for component_name, _label in SUPPORTED_COMPONENTS:
        if any(row.get("component_key") == component_name for row in component_rows):
            _write_web_json(
                analysis_dst / f"{component_name}.json",
                component_payload(component_rows, component_name),
            )

    brand_analysis_dst = WEB_DATA / "brand-supply-chain"
    brand_analysis_dst.mkdir(parents=True, exist_ok=True)
    brand_manifest = brand_supply_manifest(component_rows)
    current_brand_files = {
        f"{item['key']}.json" for item in brand_manifest.get("brands", [])
    }
    for stale in brand_analysis_dst.glob("brand-*.json"):
        if stale.name not in current_brand_files:
            stale.unlink()
    _write_web_json(brand_analysis_dst / "manifest.json", brand_manifest)
    for item in brand_manifest.get("brands", []):
        _write_web_json(
            brand_analysis_dst / f"{item['key']}.json",
            brand_supply_payload(component_rows, str(item["brand"])),
        )

    supplier_analysis_dst = WEB_DATA / "supplier-sourcing"
    supplier_analysis_dst.mkdir(parents=True, exist_ok=True)
    supplier_manifest = supplier_supply_manifest(component_rows)
    current_supplier_files = {
        f"{item['key']}.json" for item in supplier_manifest.get("suppliers", [])
    }
    for stale in supplier_analysis_dst.glob("supplier-*.json"):
        if stale.name not in current_supplier_files:
            stale.unlink()
    _write_web_json(supplier_analysis_dst / "manifest.json", supplier_manifest)
    for item in supplier_manifest.get("suppliers", []):
        _write_web_json(
            supplier_analysis_dst / f"{item['key']}.json",
            supplier_supply_payload(component_rows, str(item["supplier"])),
        )

    # profiles + field annotations
    for name in ("compare_profiles.json", "field_annotations.json"):
        src = DATA / name
        if src.exists():
            _write_web_json(WEB_DATA / name, json.loads(_read_text(src)))

    manifest = {
        "generated_at": RELEASE_GENERATED_AT,
        "categories": categories,
        "product_count": n_products,
    }
    _write_web_json(WEB_DATA / "categories.json", manifest)

    teardown = _build_teardown_manifest()
    _write_web_json(WEB_DATA / "teardown_details.json", teardown)

    recognized_brand_names = {
        str(product.get("brand") or "").strip()
        for product in public_index.get("products") or []
        if str(product.get("brand") or "").strip()
    }
    coverage_summary = {
        "generated_at": RELEASE_GENERATED_AT,
        "product_count": n_products,
        "supplier_name_count": len(supplier_manifest.get("suppliers") or []),
        "brand_name_count": len(recognized_brand_names),
        "report_count": teardown["report_count"],
        "video_count": teardown["video_count"],
        "scope_note": "供应商和品牌按当前规范化名称去重，仍可能包含待清洗别名。",
    }
    _write_web_json(WEB_DATA / "coverage-summary.json", coverage_summary)

    _write_web_json(
        WEB_DATA / "release-meta.json",
        {
            **release_stamp(),
            "counts": {
                "products": n_products,
                "reports": teardown["report_count"],
                "videos": teardown["video_count"],
                "suppliers": len(supplier_manifest.get("suppliers") or []),
                "brands": len(recognized_brand_names),
            },
        },
    )

    roundups = _build_roundup_manifest()
    _write_web_json(WEB_DATA / "roundup_insights.json", roundups)

    _check_teardown_count_regression(teardown["report_count"])
    return {
        "categories": len(categories),
        "products": n_products,
        "teardown_reports": teardown["report_count"],
        "teardown_videos": teardown["video_count"],
        "roundup_reports": roundups["report_count"],
        "analysis_component_types": len(analysis_manifest["components"]),
        "analysis_brands": len(brand_manifest["brands"]),
        "recognized_brand_names": len(recognized_brand_names),
        "coverage_summary": str(WEB_DATA / "coverage-summary.json"),
        "legacy_site_removed": legacy_removed,
        "out": str(WEB_DATA),
    }


def main() -> None:
    stats = prepare()
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
