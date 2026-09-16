from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.paths import products_dir, reports_dir, videos_dir
from server.models import Product, Report, Video, VideoFact, VideoTranscript
from server.video_intelligence import (
    serialize_product_video_bom,
    serialize_product_video_intelligence,
    serialize_product_video_profile,
)


@dataclass
class ExportStats:
    products: int = 0
    reports: int = 0
    videos: int = 0

    def to_dict(self) -> dict[str, int]:
        return vars(self).copy()


def _safe_filename(identifier: str) -> str:
    if not identifier or Path(identifier).name != identifier or identifier in {".", ".."}:
        raise ValueError(f"invalid repository identifier: {identifier!r}")
    return f"{identifier}.json"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    native_path = path
    if os.name == "nt" and not str(path).startswith("\\\\?\\"):
        native_path = Path(f"\\\\?\\{path.resolve()}")
    native_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def export_repository(
    session: Session,
    *,
    report_output: Path | None = None,
    product_output: Path | None = None,
    video_output: Path | None = None,
) -> ExportStats:
    """Restore crawler/build compatibility JSON from the durable database."""
    report_output = report_output or reports_dir()
    product_output = product_output or products_dir(for_write=True)
    video_output = video_output or videos_dir()
    stats = ExportStats()

    for report in session.scalars(select(Report).order_by(Report.id)):
        _write_json(report_output / _safe_filename(report.id), report.payload)
        stats.reports += 1
    for product in session.scalars(select(Product).order_by(Product.id)):
        payload = dict(product.payload or {})
        video_intelligence = serialize_product_video_intelligence(session, product.id)
        if video_intelligence:
            payload["video_intelligence"] = video_intelligence
        else:
            payload.pop("video_intelligence", None)
        video_bom = serialize_product_video_bom(session, product.id)
        if video_bom:
            profile = serialize_product_video_profile(session, product.id)
            if profile.get("market"):
                payload["market"] = profile["market"]
            if profile.get("unboxing"):
                payload["unboxing"] = profile["unboxing"]
            if profile.get("related_intelligence"):
                payload["related_intelligence"] = profile["related_intelligence"]
            payload["technical_facts"] = video_bom
            payload["teardown_inventory"] = video_bom
            payload["bom_table"] = video_bom
            snapshot = {
                **dict(payload.get("cost_snapshot") or {}),
                **dict(profile.get("cost_snapshot") or {}),
            }
            payload["cost_snapshot"] = snapshot
            payload["profile_source"] = "video_intelligence"
        _write_json(product_output / _safe_filename(product.id), payload)
        stats.products += 1
    for video in session.scalars(select(Video).order_by(Video.id)):
        payload = dict(video.payload or {})
        payload["id"] = video.id
        payload["source_url"] = video.source_url
        payload["url"] = video.source_url
        payload["title"] = video.title
        payload["publisher"] = video.publisher
        payload["published_at"] = video.published_at.isoformat() if video.published_at else None
        payload["processing_status"] = video.processing_status
        payload["candidate_product_id"] = video.candidate_product_id
        payload["matched_product_id"] = video.matched_product_id
        payload["match_confidence"] = video.match_confidence
        payload["match_reason"] = video.match_reason
        payload["subtitle_method"] = video.subtitle_method
        transcript = session.scalar(
            select(VideoTranscript).where(VideoTranscript.video_id == video.id)
        )
        if transcript is not None:
            payload["transcript_record"] = {
                "status": transcript.status,
                "method": transcript.method,
                "model_name": transcript.model_name,
                "language": transcript.language,
                "transcript": transcript.transcript,
                "transcript_chars": transcript.transcript_chars,
                "summary": transcript.summary,
                "segments": transcript.segments,
                "source_checksum": transcript.source_checksum,
                "payload": transcript.payload,
            }
        facts = session.scalars(
            select(VideoFact)
            .where(VideoFact.video_id == video.id)
            .order_by(VideoFact.segment_id, VideoFact.id)
        ).all()
        if facts:
            payload["intelligence_facts"] = [
                {
                    "product_id": fact.product_id,
                    "segment_id": fact.segment_id,
                    "start_seconds": fact.start_seconds,
                    "end_seconds": fact.end_seconds,
                    "raw_text": fact.raw_text,
                    "corrected_text": fact.corrected_text,
                    "fact_text": fact.fact_text,
                    "importance": fact.importance,
                    "ocr_confidence": fact.ocr_confidence,
                    "needs_review": fact.needs_review,
                    "selection_reason": fact.selection_reason,
                    "keyframe_time_seconds": fact.keyframe_time_seconds,
                    "keyframe_object_key": fact.keyframe_object_key,
                    "keyframe_sha256": fact.keyframe_sha256,
                    "status": fact.status,
                    "payload": fact.payload,
                }
                for fact in facts
            ]
        _write_json(video_output / _safe_filename(video.id), payload)
        stats.videos += 1
    return stats
