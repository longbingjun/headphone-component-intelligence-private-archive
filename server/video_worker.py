"""Durable single-concurrency worker for subtitle/keyframe intelligence."""

from __future__ import annotations

import html
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from server.config import Settings, get_settings
from server.db import scheduler_lock, session_scope
from server.models import Product, Video
from server.storage import get_storage
from server.video_intelligence import (
    PublishResult,
    product_has_complete_teardown_report,
    publish_artifacts,
)
from server.video_pipeline import run_pipeline


log = logging.getLogger("intel.video-worker")


@dataclass(frozen=True)
class AcquiredVideo:
    video_path: Path
    subtitle_path: Path | None


@dataclass(frozen=True)
class WorkerResult:
    status: str
    video_id: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return vars(self).copy()


def claim_next_video(session: Session, settings: Settings, now: datetime | None = None) -> str:
    """Atomically lease one eligible row; PostgreSQL skips rows leased elsewhere."""

    now = now or datetime.now(timezone.utc)
    stale_before = now - timedelta(seconds=settings.video_worker_lease_seconds)
    ready = and_(
        Video.processing_status == "queued",
        or_(
            Video.processing_next_attempt_at.is_(None),
            Video.processing_next_attempt_at <= now,
        ),
    )
    stale = and_(
        Video.processing_status == "processing",
        or_(Video.processing_started_at.is_(None), Video.processing_started_at <= stale_before),
    )
    statement = (
        select(Video)
        .where(
            or_(ready, stale),
            Video.processing_attempt_count < settings.video_worker_max_attempts,
        )
        .order_by(Video.processing_next_attempt_at.nullsfirst(), Video.created_at, Video.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    video = session.scalar(statement)
    if video is None:
        return ""
    video.processing_status = "processing"
    video.processing_attempt_count += 1
    video.processing_started_at = now
    video.processing_next_attempt_at = None
    video.processing_last_error = ""
    session.flush()
    return video.id


def _skip_if_report_appeared(session: Session, video: Video) -> bool:
    product = session.get(Product, video.candidate_product_id) if video.candidate_product_id else None
    if not product_has_complete_teardown_report(product):
        return False
    video.processing_status = "skipped_report_exists"
    video.matched_product_id = product.id
    video.match_confidence = 1.0
    video.match_reason = "complete_teardown_report_appeared_before_worker_download"
    video.processing_started_at = None
    if video not in product.videos:
        product.videos.append(video)
    session.flush()
    return True


def _snapshot(session: Session, video_id: str) -> dict[str, Any]:
    video = session.get(Video, video_id)
    if video is None:
        raise RuntimeError(f"video disappeared after claim: {video_id}")
    if _skip_if_report_appeared(session, video):
        return {"skip": True, "video_id": video.id}
    return {
        "skip": False,
        "video_id": video.id,
        "source_url": video.source_url,
        "embed_url": video.embed_url,
        "title": video.title,
        "payload": dict(video.payload or {}),
    }


def _manual_subtitle(candidates: list[Path]) -> Path | None:
    language_order = ("zh-cn", "zh-hans", "zh-hant", ".zh.", "chinese")
    for marker in language_order:
        match = next((path for path in candidates if marker in path.name.lower()), None)
        if match:
            return match
    return candidates[0] if candidates else None


def _normalized_download_url(snapshot: dict[str, Any]) -> str:
    """Resolve an actual video page instead of the surrounding 52audio article.

    52audio video records use the article URL as ``source_url`` and keep the
    embedded Bilibili player in ``embed_url``.  yt-dlp cannot infer the player
    from the article page, so normalize a Bilibili bvid/aid to its canonical
    video URL before download.
    """

    payload = snapshot.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    candidates = (
        payload.get("webpage_url"),
        payload.get("video_url"),
        snapshot.get("embed_url"),
        payload.get("embed_url"),
        payload.get("video_embed_url"),
        snapshot.get("source_url"),
    )
    for raw_value in candidates:
        value = html.unescape(str(raw_value or "").strip())
        if not value:
            continue
        if value.startswith("//"):
            value = f"https:{value}"
        parsed = urlparse(value)
        host = parsed.hostname.casefold() if parsed.hostname else ""
        query = parse_qs(parsed.query)

        if host == "b23.tv" or host.endswith("youtube.com") or host == "youtu.be":
            return value
        if host == "bilibili.com" or host.endswith(".bilibili.com"):
            path_match = re.search(r"/video/((?:BV)[A-Za-z0-9]+|av\d+)", parsed.path, re.I)
            if path_match:
                return f"https://www.bilibili.com/video/{path_match.group(1)}/"
            bvid = next(iter(query.get("bvid", [])), "")
            if re.fullmatch(r"BV[A-Za-z0-9]+", bvid, re.I):
                return f"https://www.bilibili.com/video/{bvid}/"
            aid = next(iter(query.get("aid", [])), "")
            if aid.isdigit():
                return f"https://www.bilibili.com/video/av{aid}/"

        if parsed.scheme in {"http", "https"} and Path(parsed.path).suffix.casefold() in {
            ".mp4",
            ".mkv",
            ".webm",
            ".flv",
            ".mov",
        }:
            return value

    raise ValueError(
        "queued video has no supported downloadable URL; expected a Bilibili "
        "bvid/aid in embed_url or an explicit video page URL"
    )


def acquire_video(snapshot: dict[str, Any], work_dir: Path, settings: Settings) -> AcquiredVideo:
    """Download at most 1080p video and creator subtitles into a temporary directory."""

    source_url = _normalized_download_url(snapshot)
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("yt-dlp is not installed in the Video Worker image") from exc

    cookie_file = settings.video_session_file.strip()
    if cookie_file and not Path(cookie_file).is_file():
        raise FileNotFoundError(f"APP_VIDEO_SESSION_FILE does not exist: {cookie_file}")
    options: dict[str, Any] = {
        "outtmpl": str(work_dir / "source.%(ext)s"),
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "noplaylist": True,
        "writesubtitles": True,
        "writeautomaticsub": False,
        "subtitleslangs": ["zh-CN", "zh-Hans", "zh-Hant", "zh"],
        "subtitlesformat": "vtt/srt/best",
        "writeinfojson": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "fragment_retries": 3,
        "overwrites": True,
    }
    if cookie_file:
        options["cookiefile"] = cookie_file
    with yt_dlp.YoutubeDL(options) as downloader:
        downloader.download([source_url])

    video_extensions = {".mp4", ".mkv", ".webm", ".flv", ".mov"}
    videos = [
        path
        for path in work_dir.iterdir()
        if path.is_file() and path.suffix.lower() in video_extensions
    ]
    if not videos:
        raise RuntimeError("yt-dlp completed but no video file was produced")
    video_path = max(videos, key=lambda path: path.stat().st_size)
    subtitle_candidates = [
        path
        for path in work_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() in {".vtt", ".srt", ".json"}
        and ".info." not in path.name.lower()
    ]
    return AcquiredVideo(video_path, _manual_subtitle(subtitle_candidates))


def _record_failure(video_id: str, settings: Settings, error: Exception) -> None:
    with session_scope() as session:
        video = session.get(Video, video_id)
        if video is None:
            return
        video.processing_started_at = None
        video.processing_last_error = f"{type(error).__name__}: {error}"[:4000]
        if video.processing_attempt_count >= settings.video_worker_max_attempts:
            video.processing_status = "failed"
            video.processing_next_attempt_at = None
        else:
            video.processing_status = "queued"
            video.processing_next_attempt_at = datetime.now(timezone.utc) + timedelta(
                seconds=settings.video_worker_retry_seconds
            )


def _publish(video_id: str, artifacts: Path) -> PublishResult:
    with session_scope() as session:
        result = publish_artifacts(
            session,
            video_id=video_id,
            artifacts_dir=artifacts,
            storage=get_storage(),
        )
        video = session.get(Video, video_id)
        if video is not None:
            video.processing_started_at = None
            video.processing_next_attempt_at = None
        return result


def process_next_video(settings: Settings | None = None) -> WorkerResult:
    settings = settings or get_settings()
    with session_scope() as session:
        video_id = claim_next_video(session, settings)
    if not video_id:
        return WorkerResult("idle")

    settings.video_worker_temp_root.mkdir(parents=True, exist_ok=True)
    temp_path = Path(tempfile.mkdtemp(prefix=f"{video_id}-", dir=settings.video_worker_temp_root))
    try:
        with session_scope() as session:
            snapshot = _snapshot(session, video_id)
        if snapshot["skip"]:
            return WorkerResult("skipped_report_exists", video_id)
        acquired = acquire_video(snapshot, temp_path, settings)
        artifacts = temp_path / "artifacts"
        run_pipeline(
            acquired.video_path,
            artifacts,
            subtitle_path=acquired.subtitle_path,
            sample_interval=settings.video_worker_ocr_interval_seconds,
        )
        published = _publish(video_id, artifacts)
        return WorkerResult(published.status, video_id, published.product_id)
    except Exception as exc:
        log.exception("video processing failed for %s", video_id)
        _record_failure(video_id, settings, exc)
        return WorkerResult("failed_or_requeued", video_id, f"{type(exc).__name__}: {exc}")
    finally:
        # The source video, subtitle download, OCR cache and rejected frames all
        # live below this directory and are never copied to durable storage.
        shutil.rmtree(temp_path, ignore_errors=False)


def rebuild_pending_video_products(settings: Settings | None = None) -> WorkerResult:
    settings = settings or get_settings()
    if not settings.video_worker_rebuild_site:
        return WorkerResult("site_rebuild_disabled")
    with session_scope() as session:
        video_ids = list(
            session.scalars(
                select(Video.id)
                .where(
                    Video.processing_status == "published",
                    Video.site_publish_status.in_({"pending", "failed"}),
                )
                .order_by(Video.intelligence_published_at, Video.id)
            )
        )
    if not video_ids:
        return WorkerResult("idle")
    try:
        from server.tasks import rebuild_site_from_database

        with scheduler_lock() as acquired:
            if not acquired:
                return WorkerResult("site_rebuild_deferred", detail="scheduler lock busy")
            rebuild_site_from_database(settings)
        with session_scope() as session:
            for video in session.scalars(select(Video).where(Video.id.in_(video_ids))):
                video.site_publish_status = "complete"
                video.site_publish_last_error = ""
        return WorkerResult("site_rebuilt", detail=",".join(video_ids))
    except Exception as exc:
        log.exception("static site rebuild failed after video publication")
        with session_scope() as session:
            for video in session.scalars(select(Video).where(Video.id.in_(video_ids))):
                video.site_publish_status = "failed"
                video.site_publish_last_error = f"{type(exc).__name__}: {exc}"[:4000]
        return WorkerResult("site_rebuild_failed", detail=f"{type(exc).__name__}: {exc}")


def run_once(settings: Settings | None = None) -> list[WorkerResult]:
    settings = settings or get_settings()
    results: list[WorkerResult] = []
    site_result = rebuild_pending_video_products(settings)
    if site_result.status != "idle":
        results.append(site_result)
    video_result = process_next_video(settings)
    results.append(video_result)
    if video_result.status == "published":
        results.append(rebuild_pending_video_products(settings))
    return results
