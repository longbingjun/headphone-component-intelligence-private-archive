"""Database-backed read model for the cost-engineer component workspace."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from core.component_analytics import (
    COMPONENT_LABELS,
    SUPPORTED_COMPONENTS,
    component_payload,
    canonical_manufacturer,
    component_evidence_supported,
    deduplicate_rows,
    manufacturer_from_evidence,
    normalize_usage_location,
    structural_features,
)
from server.models import BomItem, Product, Report, Video
from server.video_intelligence import product_has_complete_teardown_report


def component_rows_from_database(session: Session, component: str) -> list[dict]:
    if component not in COMPONENT_LABELS:
        raise ValueError(f"unsupported component key: {component}")
    records = session.execute(
        select(BomItem, Product, Report, Video)
        .join(Product, Product.id == BomItem.product_id)
        .outerjoin(Report, Report.id == BomItem.source_report_id)
        .outerjoin(Video, Video.id == BomItem.source_video_id)
        .options(selectinload(BomItem.parameters), selectinload(Product.reports))
        .where(BomItem.component_key == component)
        .order_by(
            func.coalesce(Report.published_at, Video.published_at).desc().nullslast(),
            Product.id,
            BomItem.ordinal,
        )
    ).all()
    rows: list[dict] = []
    for item, product, report, video in records:
        if item.source_type == "video" and product_has_complete_teardown_report(product):
            continue
        if not component_evidence_supported(item.component_key, item.component, item.source_text):
            continue
        manufacturer, basis, quote = manufacturer_from_evidence(
            item.source_text, item.manufacturer or item.brand, component_key=item.component_key
        )
        location_key, location_label = normalize_usage_location(item.side)
        parameters = [
            {
                "label": parameter.label,
                "value": parameter.value_text,
                "evidence_quote": parameter.evidence_quote,
            }
            for parameter in item.parameters
            if parameter.label and parameter.value_text
        ]
        features = structural_features(
            item.component_key, item.component, item.source_text, item.material
        )
        source_is_video = item.source_type == "video"
        source_title = video.title if source_is_video and video else report.title if report else ""
        source_url = video.source_url if source_is_video and video else report.url if report else ""
        source_date = (
            video.published_at if source_is_video and video else report.published_at if report else None
        )
        evidence_image = None
        if source_is_video and video:
            object_key = str((item.evidence or {}).get("keyframe_object_key") or "")
            if object_key:
                filename = object_key.replace("\\", "/").rsplit("/", 1)[-1]
                evidence_image = {
                    "url": f"/video-media/{video.id}/{filename}",
                    "public_path": f"/video-media/{video.id}/{filename}",
                    "caption": f"{item.component}对应视频关键帧",
                    "alt": f"{product.brand} {product.model} {item.component}",
                }
        rows.append(
            {
                "row_id": str(item.id),
                "component_key": item.component_key,
                "component_label": COMPONENT_LABELS[item.component_key],
                "component_name": item.component,
                "component_manufacturer": manufacturer,
                "component_manufacturer_canonical": canonical_manufacturer(manufacturer),
                "manufacturer_basis": basis,
                "manufacturer_evidence_quote": quote,
                "manufacturer_status": "reported" if manufacturer else "not_disclosed",
                "component_brand": item.brand,
                "component_model": item.model,
                "model_status": "reported" if item.model else "not_disclosed",
                "material": item.material,
                "parameters": parameters,
                "semantic_features": features,
                "detail_status": "source_structured" if features or parameters else "evidence_only",
                "usage_location": location_key,
                "usage_location_label": location_label,
                "product_id": product.id,
                "product_brand": product.brand,
                "product_model": product.model,
                "product_category": product.category,
                "source_type": "video" if source_is_video else "report",
                "source_report_id": item.source_report_id,
                "source_video_id": item.source_video_id or "",
                "source_title": source_title,
                "source_report_title": source_title,
                "source_report_url": source_url,
                "source_published_at": source_date.isoformat() if source_date else "",
                "evidence_quote": item.source_text,
                "evidence_image": evidence_image,
                "confidence": item.confidence,
            }
        )
    return deduplicate_rows(rows)


def component_analysis_payload(session: Session, component: str) -> dict:
    return component_payload(component_rows_from_database(session, component), component)


def component_analysis_manifest(session: Session) -> dict:
    counts: dict[str, tuple[int, int]] = {}
    for key in COMPONENT_LABELS:
        rows = component_rows_from_database(session, key)
        if rows:
            counts[key] = (len(rows), len({row["product_id"] for row in rows}))
    return {
        "default_component": "battery",
        "components": [
            {
                "key": key,
                "label": label,
                "rows": counts[key][0],
                "products": counts[key][1],
                "file": f"{key}.json",
            }
            for key, label in SUPPORTED_COMPONENTS
            if key in counts
        ],
        "business_fields": [
            "器件类型", "耳机类型", "耳机制造商/品牌", "耳机产品",
            "使用位置", "器件生产商", "器件型号", "参数", "报告发布日期", "来源证据",
        ],
    }
