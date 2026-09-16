"""Video discovery gate and publication of subtitle/keyframe intelligence.

The production rule is deliberately cheap-first: persist video metadata, match
the title identity against the product library, and only queue videos whose
product is not covered.  Subtitle extraction uses an accessible subtitle track
or hard-subtitle OCR.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from core.bom_taxonomy import material_hint, numeric_value
from core.component_analytics import canonical_manufacturer, manufacturer_from_evidence
from core.products import (
    canonical_product_id,
    guess_brand_from_text,
    normalize_brand,
    normalize_model,
)
from sources.audio52.lexicon import BRAND_ALIASES, PRODUCT_TYPE_SUFFIXES
from server.models import BomItem, BomItemParameter, Product, Video, VideoFact, VideoTranscript
from server.storage import ObjectStorage
from server.video_bom import structured_rows_from_video_fact


ACTIVE_PRODUCT_STATUSES = {"queued", "processing", "needs_review", "published"}
PUBLISHED_FACT_STATUS = "published"


def product_has_complete_teardown_report(product: Product | None) -> bool:
    """Return whether report evidence is strong enough to suppress video OCR.

    A bare product row is not sufficient. We require an associated report with
    both a detailed inventory/BOM and report imagery. This keeps the cheap gate
    aligned with the business rule: videos only fill products missing from the
    teardown-report library.
    """

    if product is None:
        return False
    for report in product.reports:
        payload = report.payload if isinstance(report.payload, dict) else {}
        views = payload.get("views") if isinstance(payload, dict) else {}
        views = views if isinstance(views, dict) else {}
        cost = views.get("cost") if isinstance(views.get("cost"), dict) else {}
        structure = views.get("structure") if isinstance(views.get("structure"), dict) else {}
        inventory = cost.get("teardown_inventory") or cost.get("bom_table") or []
        images = structure.get("report_image_urls") or structure.get("key_image_urls") or []
        if len(inventory) >= 5 and len(images) >= 1:
            return True
    return False


def _identity_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    return "".join(char for char in value if char.isalnum())


def _date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _video_id(payload: dict[str, Any]) -> str:
    identifier = str(payload.get("id") or payload.get("video_id") or "").strip()
    if not identifier or len(identifier) > 64 or not re.fullmatch(r"[A-Za-z0-9_.-]+", identifier):
        raise ValueError("video id must contain only letters, numbers, '.', '_' or '-'")
    return identifier


def infer_identity_from_title(title: str) -> tuple[str, str]:
    """Infer a conservative brand/model pair from a teardown-video title."""

    brand = guess_brand_from_text(title)
    if not brand:
        return "", ""
    candidate = unicodedata.normalize("NFKC", title or "")
    candidate = re.sub(
        r"^\s*(?:视频拆解|拆解视频|深度拆解|拆解|拆机视频|拆机|视频)\s*[：:丨|·-]*\s*",
        "",
        candidate,
        flags=re.IGNORECASE,
    )
    aliases = next((items for display, items in BRAND_ALIASES if display == brand), [])
    for alias in sorted({brand, *aliases}, key=len, reverse=True):
        candidate = re.sub(re.escape(alias), "", candidate, flags=re.IGNORECASE)
    candidate = re.sub(r"(?:深度)?(?:拆解|拆机|评测|测评|体验)$", "", candidate).strip()
    for suffix in sorted(PRODUCT_TYPE_SUFFIXES, key=len, reverse=True):
        if candidate.casefold().endswith(suffix.casefold()):
            candidate = candidate[: -len(suffix)].strip(" ：:丨|·-、，,")
            break
    model = normalize_model(candidate.strip(" ：:丨|·-、，,"), brand)
    if len(_identity_text(model)) < 2:
        return brand, ""
    return brand, model


@dataclass(frozen=True)
class ProductMatch:
    product: Product | None
    confidence: float
    reason: str


@dataclass(frozen=True)
class DiscoveryResult:
    video_id: str
    status: str
    candidate_product_id: str
    matched_product_id: str
    confidence: float | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return vars(self).copy()


@dataclass(frozen=True)
class PublishResult:
    video_id: str
    status: str
    product_id: str
    subtitle_segments: int
    facts_total: int
    facts_published: int
    facts_needing_review: int
    keyframes_uploaded: int

    def to_dict(self) -> dict[str, Any]:
        return vars(self).copy()


def match_product(
    session: Session,
    *,
    title: str,
    brand: str = "",
    model: str = "",
) -> ProductMatch:
    """Conservatively identify an existing product.

    High-confidence matches may skip expensive video processing.  Model-only
    evidence is intentionally returned below the automatic threshold so that a
    common model token cannot silently discard a useful video.
    """

    norm_brand = normalize_brand(brand)
    norm_model = normalize_model(model, norm_brand)
    candidate_id = canonical_product_id(norm_brand, norm_model) if norm_brand and norm_model else ""
    if candidate_id:
        product = session.get(Product, candidate_id)
        if product is not None:
            return ProductMatch(product, 1.0, "canonical_product_id_exact")

    title_key = _identity_text(title)
    brand_key = _identity_text(norm_brand)
    model_key = _identity_text(norm_model)
    best = ProductMatch(None, 0.0, "no_product_match")
    for product in session.scalars(select(Product).order_by(Product.id)):
        product_brand = normalize_brand(product.brand)
        product_model = normalize_model(product.model, product_brand)
        product_brand_key = _identity_text(product_brand)
        product_model_key = _identity_text(product_model)
        if len(product_model_key) < 3:
            continue

        same_brand = bool(brand_key and brand_key == product_brand_key)
        same_model = bool(model_key and model_key == product_model_key)
        model_in_title = product_model_key in title_key
        brand_in_title = bool(product_brand_key and product_brand_key in title_key)

        confidence = 0.0
        reason = ""
        if same_brand and same_model:
            confidence, reason = 1.0, "normalized_brand_model_exact"
        elif model_in_title and (same_brand or brand_in_title):
            confidence, reason = 0.98, "brand_and_model_in_title"
        elif same_model or model_in_title:
            confidence, reason = 0.82, "model_only_needs_review"

        if confidence > best.confidence:
            best = ProductMatch(product, confidence, reason)
    return best


def discover_video(session: Session, payload: dict[str, Any]) -> DiscoveryResult:
    """Register metadata and decide whether an expensive worker job is allowed."""

    identifier = _video_id(payload)
    video = session.get(Video, identifier)
    if video is not None and video.processing_status not in {"discovered", "failed"}:
        return DiscoveryResult(
            video.id,
            video.processing_status,
            video.candidate_product_id,
            video.matched_product_id,
            video.match_confidence,
            "video_already_registered",
        )
    if video is None:
        video = Video(id=identifier)

    video.source_id = str(payload.get("source_id") or "bilibili")[:64]
    video.source_url = str(payload.get("source_url") or payload.get("url") or payload.get("webpage_url") or "")
    video.embed_url = str(payload.get("embed_url") or payload.get("video_embed_url") or "")
    video.source_site = str(payload.get("source_site") or "Bilibili")[:255]
    video.title = str(payload.get("title") or "")
    video.publisher = str(payload.get("publisher") or payload.get("uploader") or "")[:255]
    video.published_at = _date(payload.get("published_at") or payload.get("upload_date"))
    video.brand = normalize_brand(str(payload.get("brand") or ""))
    video.model = normalize_model(str(payload.get("model") or ""), video.brand)
    if not video.brand or not video.model:
        inferred_brand, inferred_model = infer_identity_from_title(video.title)
        video.brand = video.brand or inferred_brand
        video.model = video.model or inferred_model
    video.category = str(payload.get("category") or "")[:100]
    video.payload = _json_safe(dict(payload))
    video.processing_last_error = ""

    candidate_id = (
        canonical_product_id(video.brand, video.model) if video.brand and video.model else ""
    )
    video.candidate_product_id = candidate_id
    match = match_product(
        session, title=video.title, brand=video.brand, model=video.model
    )
    video.match_confidence = match.confidence or None
    video.match_reason = match.reason

    if (
        match.product is not None
        and match.confidence >= 0.92
        and product_has_complete_teardown_report(match.product)
    ):
        video.processing_status = "skipped_report_exists"
        video.matched_product_id = match.product.id
        video.match_reason = "complete_teardown_report_exists"
        if video not in match.product.videos:
            match.product.videos.append(video)
    elif match.product is not None and match.confidence >= 0.92:
        existing_primary = session.scalar(
            select(Video)
            .where(
                Video.id != identifier,
                Video.candidate_product_id == candidate_id,
                Video.processing_status.in_(ACTIVE_PRODUCT_STATUSES),
            )
            .order_by(Video.created_at, Video.id)
            .limit(1)
        )
        video.processing_status = "candidate_secondary_video" if existing_primary else "queued"
        video.matched_product_id = match.product.id
        video.match_reason = (
            f"primary_video:{existing_primary.id}"
            if existing_primary
            else "existing_product_missing_complete_teardown_report"
        )
    elif match.product is not None:
        video.processing_status = "match_needs_review"
        video.matched_product_id = match.product.id
    elif not candidate_id:
        video.processing_status = "match_needs_review"
        video.match_reason = "brand_or_model_missing"
    else:
        existing_primary = session.scalar(
            select(Video)
            .where(
                Video.id != identifier,
                Video.candidate_product_id == candidate_id,
                Video.processing_status.in_(ACTIVE_PRODUCT_STATUSES),
            )
            .order_by(Video.created_at, Video.id)
            .limit(1)
        )
        if existing_primary is not None:
            video.processing_status = "candidate_secondary_video"
            video.match_reason = f"primary_video:{existing_primary.id}"
        else:
            video.processing_status = "queued"
            video.match_reason = "product_not_found_in_library"
        video.matched_product_id = ""

    session.add(video)
    session.flush()
    return DiscoveryResult(
        video.id,
        video.processing_status,
        video.candidate_product_id,
        video.matched_product_id,
        video.match_confidence,
        video.match_reason,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    root = root.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"artifact path escapes output directory: {relative!r}")
    if not candidate.is_file():
        raise FileNotFoundError(candidate)
    return candidate


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    import json

    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError(f"expected a JSON list: {path}")
    return [item for item in value if isinstance(item, dict)]


def _load_run_stats(root: Path) -> dict[str, Any]:
    import json

    path = root / "run_stats.json"
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _ensure_product_for_video(session: Session, video: Video) -> Product:
    if not video.candidate_product_id or not video.brand or not video.model:
        raise ValueError("confirmed brand and model are required before publishing")
    product = session.get(Product, video.candidate_product_id)
    if product is None:
        payload = {
            "canonical_id": video.candidate_product_id,
            "brand": video.brand,
            "model": video.model,
            "category": video.category,
            "first_seen": video.published_at.isoformat() if video.published_at else None,
            "latest_published": video.published_at.isoformat() if video.published_at else None,
            "report_ids": [],
            "video_ids": [video.id],
            "bom_table": [],
            "summary_image_urls": [],
            "cost_snapshot": {},
            "data_sources": ["video_intelligence"],
        }
        product = Product(
            id=video.candidate_product_id,
            brand=video.brand,
            model=video.model,
            category=video.category,
            first_seen=video.published_at,
            latest_published=video.published_at,
            payload=payload,
        )
        session.add(product)
        session.flush()
    if video not in product.videos:
        product.videos.append(video)
    payload = dict(product.payload or {})
    video_ids = list(dict.fromkeys([*(payload.get("video_ids") or []), video.id]))
    payload["video_ids"] = video_ids
    payload.setdefault("data_sources", [])
    if "video_intelligence" not in payload["data_sources"]:
        payload["data_sources"].append("video_intelligence")
    product.payload = payload
    return product


def delete_video_bom(session: Session, video_id: str) -> None:
    existing_ids = select(BomItem.id).where(
        BomItem.source_type == "video", BomItem.source_video_id == video_id
    )
    session.execute(
        delete(BomItemParameter).where(BomItemParameter.bom_item_id.in_(existing_ids))
    )
    session.execute(
        delete(BomItem).where(
            BomItem.source_type == "video", BomItem.source_video_id == video_id
        )
    )


def add_video_bom_for_fact(
    session: Session,
    *,
    video: Video,
    fact: VideoFact,
    artifact: dict[str, Any],
    ordinal_start: int,
) -> int:
    created = 0
    for offset, row in enumerate(structured_rows_from_video_fact(artifact)):
        source_text = str(artifact.get("bom_context_text") or fact.raw_text)
        parameters = row.get("parameters") if isinstance(row.get("parameters"), list) else []
        manufacturer, manufacturer_basis, manufacturer_quote = manufacturer_from_evidence(
            source_text,
            row.get("manufacturer") or row.get("brand"),
            component_key=str(row.get("component_key") or ""),
        )
        evidence = {
            "source_type": "video_intelligence",
            "video_id": video.id,
            "video_fact_id": fact.id,
            "timecode_seconds": fact.start_seconds,
            "keyframe_object_key": fact.keyframe_object_key,
            "text": source_text,
            "confidence": row.get("confidence"),
            "narrative_side_evidence": str(artifact.get("narrative_side_evidence") or ""),
        }
        item = BomItem(
            product_id=fact.product_id,
            ordinal=ordinal_start + offset,
            side=str(row.get("side") or ""),
            component=str(row.get("component") or ""),
            component_key=str(row.get("component_key") or ""),
            brand=str(row.get("brand") or ""),
            manufacturer=manufacturer,
            manufacturer_basis=manufacturer_basis,
            manufacturer_evidence_quote=manufacturer_quote,
            model=str(row.get("model") or ""),
            role=str(row.get("role") or ""),
            quantity_hint=str(row.get("qty_hint") or ""),
            material=material_hint(parameters, source_text, row.get("component")),
            source_type="video",
            source_report_id="",
            source_video_id=video.id,
            source_video_fact_id=fact.id,
            source_text=source_text,
            confidence=float(row.get("confidence") or 0.0) or None,
            evidence=evidence,
        )
        session.add(item)
        session.flush()
        for parameter_ordinal, parameter in enumerate(parameters):
            if not isinstance(parameter, dict):
                continue
            value_text = str(parameter.get("value") or parameter.get("value_text") or "")
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
        created += 1
    return created


def publish_artifacts(
    session: Session,
    *,
    video_id: str,
    artifacts_dir: Path,
    storage: ObjectStorage,
) -> PublishResult:
    """Import one completed subtitle/OCR worker output and publish reviewed facts."""

    video = session.get(Video, video_id)
    if video is None:
        raise ValueError(f"video is not registered: {video_id}")
    if video.processing_status not in {"queued", "processing", "needs_review", "failed"}:
        raise ValueError(f"video status does not permit processing: {video.processing_status}")

    # The product library may have been refreshed while this slow job waited in
    # the queue. Re-check immediately before any artifact upload so a newly
    # covered product still wins the cost gate.
    newly_covered = (
        session.get(Product, video.candidate_product_id)
        if video.candidate_product_id
        else None
    )
    if product_has_complete_teardown_report(newly_covered):
        video.processing_status = "skipped_report_exists"
        video.matched_product_id = newly_covered.id
        video.match_confidence = 1.0
        video.match_reason = "complete_teardown_report_appeared_before_processing"
        if video not in newly_covered.videos:
            newly_covered.videos.append(video)
        session.flush()
        return PublishResult(video.id, video.processing_status, newly_covered.id, 0, 0, 0, 0, 0)

    root = artifacts_dir.resolve()
    facts = _load_json_list(_artifact_path(root, "facts.json"))
    segment_name = (
        "subtitle_segments.json"
        if (root / "subtitle_segments.json").is_file()
        else "ocr_segments.json"
    )
    segments = _load_json_list(_artifact_path(root, segment_name))
    run_stats = _load_run_stats(root)
    subtitle_method = str(run_stats.get("subtitle_method") or "hard_subtitle_ocr")
    if subtitle_method not in {"creator_subtitle_track", "hard_subtitle_ocr"}:
        raise ValueError(f"unsupported subtitle method: {subtitle_method}")
    product = _ensure_product_for_video(session, video)
    storage.ensure_ready()

    transcript = session.scalar(
        select(VideoTranscript).where(VideoTranscript.video_id == video.id)
    ) or VideoTranscript(video_id=video.id)
    transcript.status = "done"
    transcript.method = subtitle_method
    transcript.model_name = "RapidOCR" if subtitle_method == "hard_subtitle_ocr" else "source_track"
    transcript.language = "zh"
    transcript.segments = segments
    transcript.transcript = "\n".join(str(item.get("raw_text") or "") for item in segments)
    transcript.transcript_chars = len(transcript.transcript)
    transcript.payload = {
        "source": subtitle_method,
        "audio_transcription_used": False,
        "segment_count": len(segments),
        "run_stats": run_stats,
    }
    session.add(transcript)

    delete_video_bom(session, video.id)
    session.execute(delete(VideoFact).where(VideoFact.video_id == video.id))
    uploaded: set[str] = set()
    published = 0
    review = 0
    video_bom_ordinal = 0
    for ordinal, item in enumerate(facts, 1):
        fact_text = str(item.get("fact") or item.get("corrected_text") or "").strip()
        if not fact_text:
            continue
        importance = max(1, min(5, int(item.get("importance") or 3)))
        needs_review = bool(item.get("needs_review")) or importance < 3
        fact_status = "needs_review" if needs_review else PUBLISHED_FACT_STATUS
        keyframe_object_key = ""
        keyframe_sha256 = ""
        relative_keyframe = str(item.get("keyframe") or "").replace("\\", "/")
        if relative_keyframe and not needs_review:
            source = _artifact_path(root, relative_keyframe)
            keyframe_sha256 = _sha256(source)
            suffix = source.suffix.lower() if source.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} else ".jpg"
            keyframe_object_key = f"video-keyframes/{video.id}/{keyframe_sha256[:16]}{suffix}"
            if keyframe_object_key not in uploaded and not storage.exists(keyframe_object_key):
                storage.put_path(
                    keyframe_object_key,
                    source,
                    mimetypes.guess_type(source.name)[0] or "image/jpeg",
                )
                uploaded.add(keyframe_object_key)

        record = VideoFact(
            video_id=video.id,
            product_id=product.id,
            segment_id=int(item.get("segment_id") or ordinal),
            start_seconds=float(item.get("start") or 0),
            end_seconds=float(item.get("end") or 0),
            raw_text=str(item.get("raw_text") or ""),
            corrected_text=str(item.get("corrected_text") or ""),
            fact_text=fact_text,
            importance=importance,
            ocr_confidence=(
                float(item["ocr_confidence"]) if item.get("ocr_confidence") is not None else None
            ),
            needs_review=needs_review,
            selection_reason=str(item.get("selection_reason") or ""),
            keyframe_time_seconds=(
                float(item["keyframe_time"]) if item.get("keyframe_time") is not None else None
            ),
            keyframe_object_key=keyframe_object_key,
            keyframe_sha256=keyframe_sha256,
            status=fact_status,
            payload=dict(item),
        )
        session.add(record)
        session.flush()
        if needs_review:
            review += 1
        else:
            published += 1
            # Packaging labels and appearance captions can mention a battery,
            # microphone opening or connector without being teardown BOM
            # evidence.  Only the BOM phase (or an explicit model proposal)
            # contributes analytical rows.  Missing ``section`` is retained as
            # a backward-compatible path for pre-schema artifacts.
            if item.get("section") == "bom" or item.get("bom_items") or not item.get("section"):
                video_bom_ordinal += add_video_bom_for_fact(
                    session,
                    video=video,
                    fact=record,
                    artifact=item,
                    ordinal_start=video_bom_ordinal,
                )

    now = datetime.now(timezone.utc)
    video.subtitle_method = subtitle_method
    video.processed_at = now
    video.processing_last_error = ""
    if published:
        video.processing_status = "published"
        video.intelligence_published_at = now
        video.matched_product_id = product.id
        video.site_publish_status = "pending"
        video.site_publish_last_error = ""
    else:
        video.processing_status = "needs_review"
    session.flush()
    return PublishResult(
        video.id,
        video.processing_status,
        product.id,
        len(segments),
        published + review,
        published,
        review,
        len(uploaded),
    )


def serialize_product_video_intelligence(
    session: Session,
    product_id: str,
    *,
    include_superseded: bool = False,
) -> list[dict[str, Any]]:
    """Return reviewed facts that are safe for the product page.

    ``include_superseded`` is reserved for the local stakeholder-comparison
    preview. Normal product pages continue to suppress video evidence after a
    complete teardown report covers the same product.
    """

    # Report evidence is the primary source. Historical video facts may remain
    # in the audit database, but they must not be presented once a complete
    # teardown report covers the same product.
    if not include_superseded and product_has_complete_teardown_report(session.get(Product, product_id)):
        return []

    allowed_video_statuses = (
        ["published", "skipped_report_exists"] if include_superseded else ["published"]
    )

    rows = session.execute(
        select(VideoFact, Video)
        .join(Video, Video.id == VideoFact.video_id)
        .where(
            VideoFact.product_id == product_id,
            VideoFact.status == PUBLISHED_FACT_STATUS,
            VideoFact.needs_review.is_(False),
            VideoFact.importance >= 3,
            Video.processing_status.in_(allowed_video_statuses),
        )
        .order_by(Video.published_at.desc().nullslast(), VideoFact.start_seconds, VideoFact.id)
    ).all()
    result: list[dict[str, Any]] = []
    for fact, video in rows:
        media_path = ""
        if fact.keyframe_object_key:
            filename = Path(fact.keyframe_object_key).name
            media_path = f"/video-media/{video.id}/{filename}"
        result.append(
            {
                "id": fact.id,
                "video_id": video.id,
                "video_title": video.title,
                "source_url": video.source_url,
                "publisher": video.publisher,
                "published_at": video.published_at.isoformat() if video.published_at else None,
                "subtitle_method": video.subtitle_method,
                "fact": fact.fact_text,
                "raw_text": fact.raw_text,
                "corrected_text": fact.corrected_text,
                "importance": fact.importance,
                "start_seconds": fact.start_seconds,
                "end_seconds": fact.end_seconds,
                "timecode": _timecode(fact.start_seconds),
                "ocr_confidence": fact.ocr_confidence,
                "keyframe_time_seconds": fact.keyframe_time_seconds,
                "keyframe_path": media_path,
            }
        )
    return result


def serialize_product_video_bom(session: Session, product_id: str) -> list[dict[str, Any]]:
    """Serialize active video-derived BOM rows for repository/site compatibility."""

    product = session.get(Product, product_id)
    if product_has_complete_teardown_report(product):
        return []
    rows = session.scalars(
        select(BomItem)
        .where(
            BomItem.product_id == product_id,
            BomItem.source_type == "video",
        )
        .order_by(BomItem.ordinal, BomItem.id)
    ).all()
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for item in rows:
        video = session.get(Video, item.source_video_id) if item.source_video_id else None
        keyframe_path = ""
        object_key = str((item.evidence or {}).get("keyframe_object_key") or "")
        if video and object_key:
            keyframe_path = f"/video-media/{video.id}/{Path(object_key).name}"
        raw_manufacturer = item.manufacturer or item.brand
        manufacturer = canonical_manufacturer(raw_manufacturer)
        identity = (
            item.component_key or item.component,
            manufacturer.casefold(),
            item.model.casefold(),
            item.side,
        )
        serialized = {
                "component": item.component,
                "component_key": item.component_key,
                "brand": manufacturer,
                "manufacturer": manufacturer,
                "model": item.model,
                "side": item.side,
                "classification": item.role,
                "role": item.role,
                "qty_hint": item.quantity_hint,
                "fact_text": item.source_text,
                "classification_reason": "根据字幕中的器件功能按统一 BOM 词典归类",
                "side_reason": (
                    f"字幕明确提及使用位置：{item.side}"
                    if item.side
                    else "字幕未明确披露使用位置"
                ),
                "parameters": [
                    {
                        "label": parameter.label,
                        "value": parameter.value_text,
                        "evidence_quote": parameter.evidence_quote,
                    }
                    for parameter in item.parameters
                    if parameter.label and parameter.value_text
                ],
                "evidence_images": (
                    [{"url": keyframe_path, "caption": f"{item.component}对应视频关键帧"}]
                    if keyframe_path
                    else []
                ),
                "evidence": {
                    **dict(item.evidence or {}),
                    "source_type": "video_intelligence",
                    "video_id": item.source_video_id,
                    "video_fact_id": item.source_video_fact_id,
                    "keyframe_path": keyframe_path,
                    "text": item.source_text,
                    "confidence": item.confidence,
                },
            }
        current = grouped.get(identity)
        if current is None:
            grouped[identity] = serialized
            continue
        facts = [
            value.strip()
            for value in [current.get("fact_text"), serialized.get("fact_text")]
            if str(value or "").strip()
        ]
        current["fact_text"] = "；".join(dict.fromkeys(facts))
        current["parameters"] = list(
            {
                (str(value.get("label") or ""), str(value.get("value") or "")): value
                for value in [*(current.get("parameters") or []), *(serialized.get("parameters") or [])]
                if isinstance(value, dict)
            }.values()
        )
        current["evidence_images"] = list(
            {
                str(value.get("url") or ""): value
                for value in [*(current.get("evidence_images") or []), *(serialized.get("evidence_images") or [])]
                if isinstance(value, dict) and value.get("url")
            }.values()
        )
    return list(grouped.values())


def serialize_product_video_profile(session: Session, product_id: str) -> dict[str, Any]:
    """Build report-shaped product sections from published video facts.

    The profile is only active when no complete teardown report exists.  It
    intentionally exposes the same ``market``, ``unboxing`` and
    ``cost_snapshot`` keys used by report-derived product pages instead of a
    second video-only presentation model.
    """

    product = session.get(Product, product_id)
    if product_has_complete_teardown_report(product):
        return {}
    fact_rows = session.scalars(
        select(VideoFact)
        .join(Video, Video.id == VideoFact.video_id)
        .where(
            VideoFact.product_id == product_id,
            VideoFact.status == PUBLISHED_FACT_STATUS,
            VideoFact.needs_review.is_(False),
            VideoFact.importance >= 3,
            Video.processing_status == "published",
        )
        .order_by(VideoFact.start_seconds, VideoFact.id)
    ).all()
    if not fact_rows:
        return {}

    allowed_topics = {
        "佩戴体验", "音质体验", "通话体验", "续航充电",
        "连接与智能", "耐用防护", "外观设计",
    }
    unboxing_sections: dict[str, list[VideoFact]] = {
        "packaging": [],
        "charging_case": [],
        "earbuds": [],
    }
    consumer_claims: list[dict[str, Any]] = []
    scenarios: list[str] = []

    def inferred_section(fact: VideoFact) -> str:
        explicit = str((fact.payload or {}).get("section") or "")
        text = fact.fact_text or fact.raw_text
        if explicit == "market" or explicit.startswith("unboxing_"):
            return explicit
        # Stable page semantics win over occasional model section drift.  The
        # model may label an appearance sentence as ``specification`` because
        # it contains a material or structure; the product page still needs it
        # under the same unboxing/appearance contract as report-derived data.
        if re.search(r"包装|配件|充电线|音频线", text):
            return "unboxing_packaging"
        if re.search(r"充电盒|充电仓|盒体", text):
            return "unboxing_charging_case"
        if re.search(r"外观|头梁|头垫|耳罩|佩戴|倾斜结构|表面材质", text):
            return "unboxing_earbuds"
        if re.search(r"兼容|游戏|音效|连接方式|便捷|舒适|人体|人耳", text):
            return "market"
        if explicit:
            return explicit
        return "specification"

    def media(fact: VideoFact) -> dict[str, str] | None:
        if not fact.keyframe_object_key:
            return None
        return {
            "url": f"/video-media/{fact.video_id}/{Path(fact.keyframe_object_key).name}",
            "caption": fact.fact_text,
            "alt": fact.fact_text,
        }

    for fact in fact_rows:
        section = inferred_section(fact)
        if section == "market":
            topic = str((fact.payload or {}).get("topic") or "").strip()
            if topic not in allowed_topics:
                text = fact.fact_text
                if re.search(r"佩戴|舒适|人体|人耳", text):
                    topic = "佩戴体验"
                elif re.search(r"音效|音质|声", text):
                    topic = "音质体验"
                elif re.search(r"通话|麦克风", text):
                    topic = "通话体验"
                elif re.search(r"续航|充电|电量", text):
                    topic = "续航充电"
                elif re.search(r"防水|防尘|耐用|IPX", text, re.IGNORECASE):
                    topic = "耐用防护"
                elif re.search(r"连接|兼容|游戏|接口", text):
                    topic = "连接与智能"
                else:
                    topic = "外观设计"
            consumer_claims.append(
                {
                    "text": fact.fact_text,
                    "category": topic,
                    "evidence_quote": fact.raw_text,
                    "evidence": {
                        "confidence": fact.ocr_confidence,
                        "source_type": "video_intelligence",
                        "video_id": fact.video_id,
                    },
                }
            )
            if "游戏" in fact.fact_text and "游戏" not in scenarios:
                scenarios.append("游戏")
        elif section.startswith("unboxing_"):
            key = section.removeprefix("unboxing_")
            if key in unboxing_sections:
                unboxing_sections[key].append(fact)

    unboxing: dict[str, Any] = {}
    populated = 0
    for key, facts in unboxing_sections.items():
        if not facts:
            continue
        populated += 1
        images = [value for fact in facts if (value := media(fact))]
        unboxing[key] = {
            "description": "；".join(dict.fromkeys(fact.fact_text for fact in facts)),
            "accessories": [],
            "appearance_images": images,
            "image_count": len(images),
            "display_bullets": [
                {
                    "text": fact.fact_text,
                    "topic": "视频图文事实",
                    "evidence_quote": fact.raw_text,
                    "priority": fact.importance,
                }
                for fact in facts
            ],
            "summary_meta": {
                "method": "video_text_to_report_schema",
                "validator": "source_verbatim_and_numeric_guard",
            },
        }
    if unboxing:
        expected_sections = 3 if product and product.category in {
            "耳夹式耳机", "耳挂式耳机", "全入耳式耳机", "半入耳式耳机"
        } else 2
        unboxing["completeness"] = min(1.0, populated / expected_sections)

    bom_rows = session.scalars(
        select(BomItem).where(
            BomItem.product_id == product_id,
            BomItem.source_type == "video",
        )
    ).all()

    def bom_display(keys: set[str], side: str = "") -> str:
        for item in bom_rows:
            if item.component_key not in keys:
                continue
            if side and side not in item.side:
                continue
            parameters = [p.value_text for p in item.parameters if p.value_text]
            supplier = canonical_manufacturer(item.manufacturer or item.brand)
            values = [supplier, item.model, *parameters]
            display = " · ".join(dict.fromkeys(value for value in values if value))
            if display:
                return display
        return ""

    snapshot = {
        "main_chip": bom_display({"bluetooth_audio_soc", "wireless_audio_ic", "audio_codec_dsp", "mcu"}),
        "pmic_case": bom_display({"power_management_ic", "charging_ic"}, "充电盒"),
        "battery_ear": bom_display({"battery"}, "耳机") or bom_display({"battery"}),
        "battery_case": bom_display({"battery"}, "充电盒"),
        "speaker": bom_display({"speaker_driver"}),
        "materials": "、".join(dict.fromkeys(item.material for item in bom_rows if item.material)),
        "bom_row_count": len(serialize_product_video_bom(session, product_id)),
        "best_report_id": None,
        "best_video_id": fact_rows[0].video_id,
    }
    completeness_fields = ("main_chip", "pmic_case", "battery_ear", "battery_case", "speaker")
    snapshot["data_completeness"] = sum(bool(snapshot[key]) for key in completeness_fields) / len(
        completeness_fields
    )
    related_intelligence: list[dict[str, Any]] = []
    for video_id in dict.fromkeys(fact.video_id for fact in fact_rows):
        video = session.get(Video, video_id)
        if video is None:
            continue
        related_intelligence.append(
            {
                "kind": "video",
                "id": video.id,
                "title": video.title,
                "published_at": video.published_at.isoformat() if video.published_at else None,
                "publisher": video.publisher,
                "video_url": video.source_url,
            }
        )
    return {
        "market": {
            "consumer_claims": consumer_claims,
            "selling_points": consumer_claims,
            "scenarios": scenarios,
        } if consumer_claims else {},
        "unboxing": unboxing,
        "cost_snapshot": snapshot,
        "related_intelligence": related_intelligence,
        "profile_source": "video_intelligence",
    }


def _timecode(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
