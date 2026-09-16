from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import urlparse

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from core.bom_taxonomy import component_key, material_hint, numeric_value
from core.catalog_overrides import load_catalog_manual_overrides, source_override
from core.component_analytics import manufacturer_from_evidence
from core.paths import products_dir, reports_dir, roundup_insights_path, videos_dir
from core.text_quality import contains_c1_controls
from server.models import (
    BomItem,
    BomItemParameter,
    ImageAsset,
    Product,
    Report,
    Video,
    VideoFact,
    VideoTranscript,
)


log = logging.getLogger("intel.importer")


@dataclass
class ImportStats:
    products: int = 0
    reports: int = 0
    videos: int = 0
    bom_items: int = 0
    bom_parameters: int = 0
    images: int = 0
    invalid_files: int = 0

    def to_dict(self) -> dict[str, int]:
        return vars(self).copy()


def _read_json(path: Path) -> dict[str, Any]:
    read_path = path
    if os.name == "nt" and not str(path).startswith("\\\\?\\"):
        read_path = Path(f"\\\\?\\{path.resolve()}")
    payload = json.loads(read_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object in {path}")
    return payload


def _date(value: Any) -> date | None:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def image_object_key(url: str) -> str:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return f"images/{digest}.webp"


def _image(
    value: Any,
    *,
    owner_type: str,
    owner_id: str,
    image_kind: str,
) -> dict[str, str] | None:
    if isinstance(value, str):
        url = value.strip()
        caption = ""
        alt = ""
    elif isinstance(value, dict):
        url = str(value.get("url") or "").strip()
        caption = str(value.get("caption") or value.get("description") or "").strip()
        alt = str(value.get("alt") or "").strip()
    else:
        return None
    if not url.startswith(("http://", "https://")):
        return None
    return {
        "owner_type": owner_type,
        "owner_id": owner_id,
        "image_kind": image_kind,
        "source_url": url,
        "object_key": image_object_key(url),
        "original_name": Path(urlparse(url).path).name,
        "caption": caption,
        "alt_text": alt,
    }


def iter_product_images(product: dict[str, Any]) -> Iterator[dict[str, str]]:
    product_id = str(product.get("canonical_id") or "")
    if not product_id:
        return
    for value in product.get("summary_image_urls") or []:
        item = _image(value, owner_type="product", owner_id=product_id, image_kind="summary")
        if item:
            yield item
    unboxing = product.get("unboxing") or {}
    if isinstance(unboxing, dict):
        for section in ("packaging", "charging_case", "earbuds"):
            section_data = unboxing.get(section) or {}
            if not isinstance(section_data, dict):
                continue
            for value in section_data.get("appearance_images") or []:
                item = _image(
                    value,
                    owner_type="product",
                    owner_id=product_id,
                    image_kind=f"unboxing_{section}",
                )
                if item:
                    yield item


def iter_report_images(report: dict[str, Any]) -> Iterator[dict[str, str]]:
    """Yield all source-report originals for internal MinIO evidence storage."""

    report_id = str(report.get("id") or "").strip()
    if not report_id:
        return
    views = report.get("views") or {}
    structure = views.get("structure") if isinstance(views, dict) else {}
    structure = structure if isinstance(structure, dict) else {}
    for value in structure.get("report_image_urls") or []:
        item = _image(
            value,
            owner_type="report",
            owner_id=report_id,
            image_kind="report_original",
        )
        if item:
            yield item


def iter_roundup_images(payload: dict[str, Any]) -> Iterator[dict[str, str]]:
    """Yield homepage annual-insight images so they share the MinIO lifecycle."""
    for report in payload.get("reports") or []:
        if not isinstance(report, dict):
            continue
        report_id = str(report.get("id") or "").strip()
        digest = report.get("digest") or {}
        if not report_id or not isinstance(digest, dict):
            continue
        for value in digest.get("image_highlights") or []:
            item = _image(
                value,
                owner_type="roundup",
                owner_id=report_id,
                image_kind="roundup_highlight",
            )
            if item:
                yield item


def _upsert_report(session: Session, payload: dict[str, Any]) -> Report | None:
    report_id = str(payload.get("id") or "").strip()
    if not report_id:
        return None
    report = session.get(Report, report_id) or Report(id=report_id)
    views = payload.get("views") or {}
    market = views.get("market") if isinstance(views, dict) else {}
    market = market if isinstance(market, dict) else {}
    report.title = str(payload.get("title") or payload.get("product_title") or "")
    report.url = str(payload.get("url") or "")
    report.author = str(payload.get("author") or payload.get("publisher") or "")
    report.published_at = _date(payload.get("published_at"))
    report.brand = str(payload.get("brand") or market.get("brand") or "")
    report.model = str(payload.get("model") or market.get("model") or "")
    report.category = str(payload.get("category") or "")
    report.summary = str(payload.get("summary") or "")
    report.payload = payload
    session.add(report)
    return report


def _upsert_video(session: Session, payload: dict[str, Any]) -> Video | None:
    video_id = str(payload.get("id") or "").strip()
    if not video_id:
        return None
    video = session.get(Video, video_id) or Video(id=video_id)
    video.source_id = str(payload.get("source_id") or "audio52")
    video.source_url = str(payload.get("url") or "")
    video.embed_url = str(payload.get("video_embed_url") or "")
    video.source_site = str(payload.get("source_site") or "")
    video.title = str(payload.get("title") or payload.get("product_title") or "")
    video.publisher = str(payload.get("publisher") or payload.get("author") or "")
    video.published_at = _date(payload.get("published_at") or payload.get("date"))
    video.brand = str(payload.get("brand") or "")
    video.model = str(payload.get("model") or "")
    video.category = str(payload.get("category") or "")
    video.summary = str(payload.get("summary") or "")
    requested_processing_status = str(payload.get("processing_status") or "").strip()
    if requested_processing_status:
        video.processing_status = requested_processing_status
    video.candidate_product_id = str(
        payload.get("candidate_product_id") or video.candidate_product_id or ""
    )
    video.matched_product_id = str(
        payload.get("matched_product_id") or video.matched_product_id or ""
    )
    video.match_confidence = payload.get("match_confidence", video.match_confidence)
    video.match_reason = str(payload.get("match_reason") or video.match_reason or "")
    video.subtitle_method = str(payload.get("subtitle_method") or video.subtitle_method or "")
    video.payload = payload
    session.add(video)
    return video


def _restore_video_intelligence(session: Session, payload: dict[str, Any]) -> None:
    """Restore exported transcript, reviewed facts and their BOM projections."""

    video_id = str(payload.get("id") or "").strip()
    video = session.get(Video, video_id) if video_id else None
    if video is None:
        return
    transcript_payload = payload.get("transcript_record")
    if isinstance(transcript_payload, dict):
        transcript = session.scalar(
            select(VideoTranscript).where(VideoTranscript.video_id == video.id)
        ) or VideoTranscript(video_id=video.id)
        for field in (
            "status",
            "method",
            "model_name",
            "language",
            "transcript",
            "transcript_chars",
            "summary",
            "segments",
            "source_checksum",
            "payload",
        ):
            if field in transcript_payload:
                setattr(transcript, field, transcript_payload[field])
        session.add(transcript)

    facts_payload = payload.get("intelligence_facts")
    if not isinstance(facts_payload, list):
        return
    from server.video_intelligence import add_video_bom_for_fact, delete_video_bom

    delete_video_bom(session, video.id)
    session.execute(delete(VideoFact).where(VideoFact.video_id == video.id))
    ordinal = 0
    for item in facts_payload:
        if not isinstance(item, dict):
            continue
        product_id = str(item.get("product_id") or video.candidate_product_id or "")
        if not product_id or session.get(Product, product_id) is None:
            continue
        fact = VideoFact(
            video_id=video.id,
            product_id=product_id,
            segment_id=int(item.get("segment_id") or 0),
            start_seconds=float(item.get("start_seconds") or 0),
            end_seconds=float(item.get("end_seconds") or 0),
            raw_text=str(item.get("raw_text") or ""),
            corrected_text=str(item.get("corrected_text") or ""),
            fact_text=str(item.get("fact_text") or ""),
            importance=max(1, min(5, int(item.get("importance") or 3))),
            ocr_confidence=(
                float(item["ocr_confidence"])
                if item.get("ocr_confidence") is not None
                else None
            ),
            needs_review=bool(item.get("needs_review")),
            selection_reason=str(item.get("selection_reason") or ""),
            keyframe_time_seconds=(
                float(item["keyframe_time_seconds"])
                if item.get("keyframe_time_seconds") is not None
                else None
            ),
            keyframe_object_key=str(item.get("keyframe_object_key") or ""),
            keyframe_sha256=str(item.get("keyframe_sha256") or ""),
            status=str(item.get("status") or "needs_review"),
            payload=dict(item.get("payload") or {}),
        )
        session.add(fact)
        session.flush()
        if fact.status == "published" and not fact.needs_review and fact.importance >= 3:
            artifact = {**dict(fact.payload or {}), "raw_text": fact.raw_text}
            ordinal += add_video_bom_for_fact(
                session,
                video=video,
                fact=fact,
                artifact=artifact,
                ordinal_start=ordinal,
            )


def _upsert_image(session: Session, values: dict[str, str]) -> ImageAsset:
    image = session.scalar(
        select(ImageAsset).where(
            ImageAsset.owner_type == values["owner_type"],
            ImageAsset.owner_id == values["owner_id"],
            ImageAsset.source_url == values["source_url"],
        )
    )
    if image is None:
        image = ImageAsset(**values)
    else:
        for key, value in values.items():
            setattr(image, key, value)
    session.add(image)
    return image


def _delete_excluded_source(session: Session, *, owner_type: str, owner_id: str) -> None:
    """Delete an explicitly excluded source together with its polymorphic images."""

    session.execute(
        delete(ImageAsset).where(
            ImageAsset.owner_type == owner_type,
            ImageAsset.owner_id == owner_id,
        )
    )
    model = Report if owner_type == "report" else Video
    existing = session.get(model, owner_id)
    if existing is not None:
        session.delete(existing)


def _upsert_product(
    session: Session,
    payload: dict[str, Any],
    stats: ImportStats,
    *,
    preserve_runtime_evidence: bool = False,
    repository_report_ids: frozenset[str] = frozenset(),
) -> Product | None:
    product_id = str(payload.get("canonical_id") or "").strip()
    if not product_id:
        return None
    snapshot = payload.get("cost_snapshot") or {}
    launch = payload.get("launch") or {}
    product = session.get(Product, product_id) or Product(id=product_id)
    product.brand = str(payload.get("brand") or "")
    product.model = str(payload.get("model") or "")
    product.category = str(payload.get("category") or "")
    product.first_seen = _date(payload.get("first_seen"))
    product.latest_published = _date(payload.get("latest_published"))
    product.launch_date = _date(launch.get("date") if isinstance(launch, dict) else None)
    product.price_amount = _decimal(snapshot.get("price_cny"))
    product.price_currency = str(snapshot.get("price_currency") or "CNY")
    product.data_completeness = snapshot.get("data_completeness")
    product.payload = payload
    session.add(product)
    session.flush()

    retained_reports = {item.id: item for item in product.reports} if preserve_runtime_evidence else {}
    for report_id in payload.get("report_ids") or []:
        report = session.get(Report, str(report_id))
        if report is not None:
            retained_reports[report.id] = report
    product.reports = list(retained_reports.values())

    retained_videos = {item.id: item for item in product.videos} if preserve_runtime_evidence else {}
    for video_id in payload.get("video_ids") or payload.get("related_video_ids") or []:
        video = session.get(Video, str(video_id))
        if video is not None:
            retained_videos[video.id] = video
    product.videos = list(retained_videos.values())

    if preserve_runtime_evidence:
        product.payload = {
            **dict(product.payload or {}),
            "report_ids": sorted(retained_reports),
            "related_report_ids": sorted(retained_reports),
            "video_ids": sorted(retained_videos),
            "related_video_ids": sorted(retained_videos),
        }

    # Report refreshes replace only report-derived rows. Reviewed video rows
    # remain auditable and are hidden dynamically whenever a complete report
    # covers the product.
    replace_filters = [
        BomItem.product_id == product_id,
        BomItem.source_type != "video",
    ]
    if preserve_runtime_evidence:
        # Replace release-known rows, but retain BOM evidence from newer
        # server-side reports that are absent from this Git release.
        if repository_report_ids:
            replace_filters.append(
                (BomItem.source_report_id == "")
                | (BomItem.source_report_id.in_(repository_report_ids))
            )
        else:
            replace_filters.append(BomItem.source_report_id == "")
    existing_bom_ids = select(BomItem.id).where(*replace_filters)
    session.execute(
        delete(BomItemParameter).where(BomItemParameter.bom_item_id.in_(existing_bom_ids))
    )
    session.execute(
        delete(BomItem).where(*replace_filters)
    )
    source_rows = (
        payload.get("technical_facts")
        or payload.get("teardown_inventory")
        or payload.get("bom_table")
        or []
    )
    report_ids = [str(value) for value in payload.get("report_ids") or [] if value]
    for ordinal, row in enumerate(source_rows):
        if not isinstance(row, dict):
            continue
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        if str(evidence.get("source_type") or "").startswith("video"):
            # Video rows are restored from their VideoFact records, not
            # mislabelled as teardown-report evidence during product import.
            continue
        parameters = row.get("parameters") if isinstance(row.get("parameters"), list) else []
        source_text = str(evidence.get("text") or row.get("fact_text") or "")
        confidence_raw = evidence.get("confidence")
        try:
            confidence = float(confidence_raw) if confidence_raw not in (None, "") else None
        except (TypeError, ValueError):
            confidence = None
        normalized_component_key = component_key(row.get("component"), source_text)
        manufacturer, manufacturer_basis, manufacturer_quote = manufacturer_from_evidence(
            source_text,
            row.get("manufacturer") or row.get("brand"),
            component_key=normalized_component_key,
        )
        item = BomItem(
            product_id=product_id,
            ordinal=ordinal,
            side=str(row.get("side") or ""),
            component=str(row.get("component") or ""),
            component_key=normalized_component_key,
            brand=str(row.get("brand") or ""),
            manufacturer=manufacturer,
            manufacturer_basis=manufacturer_basis,
            manufacturer_evidence_quote=manufacturer_quote,
            model=str(row.get("model") or ""),
            role=str(row.get("classification") or row.get("role") or ""),
            quantity_hint=str(row.get("qty_hint") or ""),
            material=material_hint(parameters, source_text, row.get("component")),
            source_type="report",
            source_report_id=str(
                row.get("report_id") or evidence.get("report_id") or (report_ids[0] if report_ids else "")
            ),
            source_text=source_text,
            confidence=confidence,
            evidence=evidence,
        )
        session.add(item)
        session.flush()
        for parameter_ordinal, parameter in enumerate(parameters):
            if not isinstance(parameter, dict):
                continue
            value_text = str(parameter.get("value") or "")
            value_number, unit = numeric_value(value_text)
            session.add(
                BomItemParameter(
                    bom_item_id=item.id,
                    ordinal=parameter_ordinal,
                    label=str(parameter.get("label") or ""),
                    value_text=value_text,
                    value_numeric=value_number,
                    unit=unit,
                    evidence_quote=str(parameter.get("evidence_quote") or source_text),
                )
            )
            stats.bom_parameters += 1
        stats.bom_items += 1

    for image_values in iter_product_images(payload):
        _upsert_image(session, image_values)
        stats.images += 1
    return product


def _import_missing_product_images(session: Session, stats: ImportStats) -> None:
    """Register versioned product images that are absent from the live database.

    Deployment seed import deliberately avoids re-importing products because the
    database may contain newer crawler or human edits.  Image rows are additive,
    though, and are required for the MinIO backfill worker to discover recovered
    files bundled with the release.
    """
    existing = {
        (asset.owner_type, asset.owner_id, asset.source_url): asset
        for asset in session.scalars(select(ImageAsset)).all()
    }
    for path in sorted(item for item in products_dir().glob("*.json") if item.name != "index.json"):
        try:
            for image_values in iter_product_images(_read_json(path)):
                identity = (
                    image_values["owner_type"],
                    image_values["owner_id"],
                    image_values["source_url"],
                )
                current = existing.get(identity)
                if current is not None:
                    for field in ("caption", "alt_text"):
                        old_value = str(getattr(current, field) or "")
                        new_value = str(image_values.get(field) or "")
                        if (
                            old_value
                            and contains_c1_controls(old_value)
                            and new_value
                            and not contains_c1_controls(new_value)
                        ):
                            setattr(current, field, new_value)
                    continue
                asset = ImageAsset(**image_values)
                session.add(asset)
                existing[identity] = asset
                stats.images += 1
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            log.warning("skipped invalid product image seed JSON %s: %s", path, exc)
            stats.invalid_files += 1


def _import_roundup_images(session: Session, stats: ImportStats) -> None:
    roundup_path = roundup_insights_path()
    if not roundup_path.is_file():
        return
    try:
        for image_values in iter_roundup_images(_read_json(roundup_path)):
            _upsert_image(session, image_values)
            stats.images += 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        log.warning("skipped invalid roundup insight JSON %s: %s", roundup_path, exc)
        stats.invalid_files += 1


def import_deployment_seeds(session: Session) -> ImportStats:
    """Backfill versioned images without replacing live data."""
    stats = ImportStats()
    _import_missing_product_images(session, stats)
    _import_roundup_images(session, stats)
    session.flush()
    return stats


def import_repository(
    session: Session,
    *,
    preserve_runtime_evidence: bool = False,
) -> ImportStats:
    """Idempotently import canonical JSON into PostgreSQL.

    JSON remains the crawler/build compatibility layer during migration. The
    database is the durable query and operational store.
    """
    stats = ImportStats()
    catalog_overrides = load_catalog_manual_overrides()
    report_paths: Iterable[Path] = sorted(reports_dir().glob("*.json"))
    repository_report_ids = frozenset(path.stem for path in report_paths)
    for path in report_paths:
        try:
            payload = _read_json(path)
            if source_override("report", payload, catalog_overrides).get("action") == "exclude_catalog":
                _delete_excluded_source(
                    session,
                    owner_type="report",
                    owner_id=str(payload.get("id") or ""),
                )
                continue
            if _upsert_report(session, payload):
                stats.reports += 1
            for image_values in iter_report_images(payload):
                _upsert_image(session, image_values)
                stats.images += 1
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            log.warning("skipped invalid report JSON %s: %s", path, exc)
            stats.invalid_files += 1

    video_payloads: list[dict[str, Any]] = []
    video_paths: Iterable[Path] = sorted(videos_dir().glob("*.json"))
    for path in video_paths:
        try:
            video_payload = _read_json(path)
            if source_override("video", video_payload, catalog_overrides).get("action") == "exclude_catalog":
                _delete_excluded_source(
                    session,
                    owner_type="video",
                    owner_id=str(video_payload.get("id") or ""),
                )
                continue
            video_payloads.append(video_payload)
            if _upsert_video(session, video_payload):
                stats.videos += 1
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            log.warning("skipped invalid video JSON %s: %s", path, exc)
            stats.invalid_files += 1

    product_paths = sorted(
        path for path in products_dir().glob("*.json") if path.name != "index.json"
    )
    for path in product_paths:
        try:
            if _upsert_product(
                session,
                _read_json(path),
                stats,
                preserve_runtime_evidence=preserve_runtime_evidence,
                repository_report_ids=repository_report_ids,
            ):
                stats.products += 1
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            log.warning("skipped invalid product JSON %s: %s", path, exc)
            stats.invalid_files += 1

    session.flush()
    for video_payload in video_payloads:
        _restore_video_intelligence(session, video_payload)

    # Products must exist before applying the video cost gate.  This pass is
    # intentionally metadata-only: covered products are skipped, while only
    # uncovered, confidently identified products enter the worker queue.
    session.flush()
    from server.video_intelligence import discover_video, product_has_complete_teardown_report

    # Reconcile historical rows too. Keep prior facts/keyframes for audit, but
    # stop publishing or reprocessing their videos when report evidence now
    # covers the product.
    for product in session.scalars(select(Product).order_by(Product.id)):
        if not product_has_complete_teardown_report(product):
            continue
        for video in product.videos:
            video.processing_status = "skipped_report_exists"
            video.matched_product_id = product.id
            video.match_confidence = 1.0
            video.match_reason = "complete_teardown_report_exists_after_import"
            video.site_publish_status = "not_required"

    discovered_videos = session.scalars(
        select(Video).where(Video.processing_status == "discovered").order_by(Video.id)
    ).all()
    for video in discovered_videos:
        payload = {
            **dict(video.payload or {}),
            "id": video.id,
            "source_url": video.source_url,
            "embed_url": video.embed_url,
            "title": video.title,
            "publisher": video.publisher,
            "published_at": video.published_at,
            "brand": video.brand,
            "model": video.model,
            "category": video.category,
        }
        discover_video(session, payload)

    _import_roundup_images(session, stats)
    session.flush()
    return stats
