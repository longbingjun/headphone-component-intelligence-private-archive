from __future__ import annotations

import json
import logging
import re
import threading
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from server.config import Settings, get_settings
from server.db import get_session_factory
from server.migrate import upgrade_database
from server.models import CrawlRun, ImageAsset, Product, Report, Video, VideoFact, VideoTranscript
from core.component_analytics import COMPONENT_LABELS
from core.release_metadata import (
    DATA_CONTRACT_VERSION,
    DATA_VERSION,
    EXPECTED_ALEMBIC_HEAD,
    RELEASE_COMMIT,
    RELEASE_GENERATED_AT,
)
from server.component_analysis import component_analysis_manifest, component_analysis_payload
from server.scheduler import start_scheduler
from server.storage import get_storage, image_sync_required, verify_ready_image_samples
from server.importer import import_deployment_seeds, import_repository
from server.image_cache import (
    image_sync_progress,
    priority_image_assets,
    requeue_missing_recovered_images,
)
from server.static_site import inspect_static_site
from server.db import session_scope
from server.tasks import (
    rebuild_site_from_database,
    run_full_image_backfill,
    run_image_bootstrap,
    run_refresh,
)
from server.video_intelligence import serialize_product_video_intelligence


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("intel.web")
MEDIA_NAME = re.compile(r"^[a-f0-9]{16}\.webp$")
VIDEO_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
VIDEO_MEDIA_NAME = re.compile(r"^[a-f0-9]{16}\.(?:jpe?g|png|webp)$")


def db_session():
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def run_startup_jobs(
    settings: Settings,
    *,
    rebuild_site: bool,
    sync_images: bool,
    full_sync_images: bool,
    initial_refresh: bool,
) -> None:
    """Serialize optional startup jobs so they never race for files or locks."""
    jobs = (
        (rebuild_site, "site rebuild", lambda: rebuild_site_from_database(settings)),
        (sync_images, "image bootstrap", run_image_bootstrap),
        (full_sync_images, "full image backfill", run_full_image_backfill),
        (initial_refresh, "initial refresh", lambda: run_refresh(trigger="startup")),
    )
    for enabled, label, job in jobs:
        if not enabled:
            continue
        try:
            log.info("starting %s", label)
            result = job()
            log.info("%s complete: %s", label, result)
        except Exception:
            log.exception("%s failed; later startup jobs will still be attempted", label)


def prepare_startup_jobs(settings: Settings) -> None:
    """Inspect durable state and run slow initialization after HTTP is ready.

    ARC applies a short startup probe. Repository reconciliation, MinIO checks,
    and the all-image progress query can exceed that window once the historical
    dataset is populated, so none of them may block FastAPI's lifespan startup.
    """
    try:
        if settings.import_on_start:
            with session_scope() as session:
                product_count = session.scalar(select(func.count()).select_from(Product)) or 0
                if product_count == 0:
                    stats = import_repository(session)
                    log.info("empty database bootstrapped from repository: %s", stats.to_dict())
                else:
                    log.info(
                        "database already contains %s products; bootstrap import skipped",
                        product_count,
                    )
                    seed_stats = import_deployment_seeds(session)
                    log.info("versioned deployment seeds reconciled: %s", seed_stats.to_dict())

        storage = get_storage(settings)
        storage.ensure_ready()
        with session_scope() as session:
            recovered_state = requeue_missing_recovered_images(session, settings, storage)
        if recovered_state["listed"]:
            log.info("recovered image seeds verified: %s", recovered_state)
        needs_image_sync = False
        needs_full_image_sync = False
        if settings.initial_image_sync_on_start:
            with session_scope() as session:
                product_count = session.scalar(select(func.count()).select_from(Product)) or 0
                needs_image_sync, image_state = image_sync_required(
                    session, storage, root=settings.root
                )
            if product_count and needs_image_sync:
                log.info("image synchronization required: %s", image_state)
        if settings.full_image_sync_on_start:
            with session_scope() as session:
                full_image_state = image_sync_progress(session)
            needs_full_image_sync = full_image_state.remaining > 0
            if needs_full_image_sync:
                log.info("full image synchronization required: %s", full_image_state.to_dict())

        needs_initial_refresh = False
        if settings.initial_refresh_on_start:
            with session_scope() as session:
                has_successful_run = bool(
                    session.scalar(
                        select(func.count())
                        .select_from(CrawlRun)
                        .where(
                            CrawlRun.status == "success",
                            CrawlRun.trigger.not_like("image%"),
                        )
                    )
                )
            if not has_successful_run:
                needs_initial_refresh = True
                log.info("no successful base refresh recorded; initial refresh queued")

        run_startup_jobs(
            settings,
            rebuild_site=settings.rebuild_site_on_start,
            sync_images=needs_image_sync,
            full_sync_images=needs_full_image_sync,
            initial_refresh=needs_initial_refresh,
        )
    except Exception:
        log.exception("background startup preparation failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    scheduler = None
    if settings.database_configured:
        if settings.auto_migrate:
            upgrade_database()
        if settings.scheduler_run_in_web:
            scheduler = start_scheduler(settings)
        elif settings.scheduler_enabled:
            log.info("scheduler configured as an external process")
        threading.Thread(
            target=prepare_startup_jobs,
            args=(settings,),
            name="startup-jobs",
            daemon=True,
        ).start()
    else:
        log.warning("DATABASE_URL is missing; API data and scheduler are unavailable")
    yield
    if scheduler is not None:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title="52audio Cost Intelligence",
    version="6.1.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
    settings = get_settings()
    result: dict[str, object] = {
        "status": "ok",
        "database": "not_configured",
        "storage": settings.storage_backend,
        "scheduler": (
            "in_web"
            if settings.scheduler_enabled and settings.scheduler_run_in_web
            else "external"
            if settings.scheduler_enabled
            else False
        ),
        "release": {
            "app_version": app.version,
            "commit_sha": RELEASE_COMMIT,
            "expected_schema_version": EXPECTED_ALEMBIC_HEAD,
            "data_contract_version": DATA_CONTRACT_VERSION,
        },
    }
    if settings.database_configured:
        with get_session_factory()() as session:
            session.execute(text("SELECT 1"))
        result["database"] = "ok"
    return result


def _published_release(settings: Settings) -> dict:
    path = settings.site_dir / "data" / "release-meta.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


@app.get("/version")
def version(session: Session = Depends(db_session)) -> dict:
    """Expose the immutable code, schema and generated-data identity."""

    settings = get_settings()
    actual_schema = session.execute(
        text("SELECT version_num FROM alembic_version")
    ).scalar()
    published = _published_release(settings)
    return {
        "app_version": app.version,
        "commit_sha": RELEASE_COMMIT,
        "expected_schema_version": EXPECTED_ALEMBIC_HEAD,
        "actual_schema_version": actual_schema,
        "data_contract_version": DATA_CONTRACT_VERSION,
        "runtime_data_version": DATA_VERSION,
        "runtime_generated_at": RELEASE_GENERATED_AT,
        "published_data_version": published.get("data_version"),
        "published_generated_at": published.get("generated_at"),
        "versions_match": bool(
            actual_schema == EXPECTED_ALEMBIC_HEAD
            and RELEASE_COMMIT not in {"", "local", "unknown"}
            and published.get("schema_version") == EXPECTED_ALEMBIC_HEAD
            and published.get("contract_version") == DATA_CONTRACT_VERSION
            and str(published.get("data_version") or "").endswith(RELEASE_COMMIT)
        ),
    }


@app.get("/ready")
def ready() -> dict:
    """Readiness including PostgreSQL, object storage and initial image coverage."""
    settings = get_settings()
    if not settings.database_configured:
        raise HTTPException(status_code=503, detail="database_not_configured")
    storage = get_storage(settings)
    storage.ensure_ready()
    static_site = inspect_static_site(
        settings.site_dir,
        expected_base_path=settings.public_base_path,
    )
    if not static_site.ready:
        raise HTTPException(
            status_code=503,
            detail={"status": "static_site_incomplete", **static_site.to_dict()},
        )
    with get_session_factory()() as session:
        session.execute(text("SELECT 1"))
        product_count = session.scalar(select(func.count()).select_from(Product)) or 0
        required_images = priority_image_assets(session, settings.root)
        total = len(required_images)
        ready_images = sum(image.storage_status == "ready" for image in required_images)
        roundup_total = (
            session.scalar(
                select(func.count()).select_from(ImageAsset).where(ImageAsset.owner_type == "roundup")
            )
            or 0
        )
        roundup_ready = (
            session.scalar(
                select(func.count())
                .select_from(ImageAsset)
                .where(
                    ImageAsset.owner_type == "roundup",
                    ImageAsset.storage_status == "ready",
                )
            )
            or 0
        )
        storage_sample = verify_ready_image_samples(session, storage, root=settings.root)
    ratio = ready_images / total if total else 1.0
    if not product_count:
        raise HTTPException(status_code=503, detail="product_data_not_initialized")
    if settings.initial_image_sync_on_start and (
        not total
        or not roundup_total
        or not storage_sample["sampled"]
        or ratio < settings.image_min_ready_ratio
        or roundup_ready < roundup_total
        or storage_sample["missing"]
    ):
        raise HTTPException(
            status_code=503,
            detail={
                "status": "initializing_images",
                "image_assets": total,
                "ready_images": ready_images,
                "ready_ratio": ratio,
                "roundup_images": roundup_total,
                "ready_roundup_images": roundup_ready,
                "storage_sample": storage_sample,
            },
        )
    return {
        "status": "ready",
        "database": "ok",
        "storage": "ok",
        "image_assets": total,
        "ready_images": ready_images,
        "ready_ratio": ratio,
        "roundup_images": roundup_total,
        "ready_roundup_images": roundup_ready,
        "storage_sample": storage_sample,
        "static_site": static_site.to_dict(),
    }


@app.get("/api/products")
def products(
    category: str = "",
    brand: str = "",
    search_text: str = Query("", alias="q"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: Session = Depends(db_session),
) -> dict:
    filters = []
    if category:
        filters.append(Product.category == category)
    if brand:
        filters.append(Product.brand == brand)
    if search_text:
        term = f"%{search_text.strip()}%"
        filters.append((Product.brand.ilike(term)) | (Product.model.ilike(term)))
    total = session.scalar(select(func.count()).select_from(Product).where(*filters)) or 0
    rows = session.scalars(
        select(Product)
        .where(*filters)
        .order_by(Product.latest_published.desc().nullslast(), Product.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "total": total,
        "items": [
            {
                "canonical_id": row.id,
                "brand": row.brand,
                "model": row.model,
                "category": row.category,
                "first_seen": row.first_seen,
                "latest_published": row.latest_published,
                "price": row.price_amount,
                "currency": row.price_currency,
                "data_completeness": row.data_completeness,
            }
            for row in rows
        ],
    }


@app.get("/api/products/{product_id}")
def product_detail(product_id: str, session: Session = Depends(db_session)) -> dict:
    product = session.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="product not found")
    return product.payload


@app.get("/api/reports")
def reports(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: Session = Depends(db_session),
) -> dict:
    total = session.scalar(select(func.count()).select_from(Report)) or 0
    rows = session.scalars(
        select(Report).order_by(Report.published_at.desc().nullslast()).offset(offset).limit(limit)
    ).all()
    return {
        "total": total,
        "items": [
            {
                "id": row.id,
                "title": row.title,
                "url": row.url,
                "author": row.author,
                "published_at": row.published_at,
                "brand": row.brand,
                "category": row.category,
                "summary": row.summary,
            }
            for row in rows
        ],
    }


@app.get("/api/refresh/status")
def refresh_status(session: Session = Depends(db_session)) -> dict:
    run = session.scalar(
        select(CrawlRun)
        .where(CrawlRun.trigger.not_like("image%"))
        .order_by(CrawlRun.started_at.desc())
        .limit(1)
    )
    if run is None:
        return {"status": "never_run"}
    return {
        "id": run.id,
        "trigger": run.trigger,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "attempt_count": run.attempt_count,
        "new_reports": run.new_reports,
        "errors": run.errors,
        "error_message": run.error_message,
    }


@app.get("/api/images/status")
def image_status(session: Session = Depends(db_session)) -> dict:
    progress = image_sync_progress(session)
    run = session.scalar(
        select(CrawlRun)
        .where(CrawlRun.trigger.like("image%"))
        .order_by(CrawlRun.started_at.desc())
        .limit(1)
    )
    payload: dict[str, object] = {"progress": progress.to_dict()}
    if run is None:
        payload["run"] = {"status": "never_run"}
    else:
        payload["run"] = {
            "id": run.id,
            "trigger": run.trigger,
            "status": run.status,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "batch": run.attempt_count,
            "errors": run.errors,
            "error_message": run.error_message,
            "details": run.details,
        }
    return payload


@app.get("/api/videos")
def videos(
    status: str = "",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: Session = Depends(db_session),
) -> dict:
    filters = [Video.processing_status == status] if status else []
    total = session.scalar(select(func.count()).select_from(Video).where(*filters)) or 0
    rows = session.scalars(
        select(Video)
        .where(*filters)
        .order_by(Video.published_at.desc().nullslast(), Video.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "total": total,
        "items": [
            {
                "id": row.id,
                "title": row.title,
                "source_url": row.source_url,
                "published_at": row.published_at,
                "brand": row.brand,
                "model": row.model,
                "category": row.category,
                "processing_status": row.processing_status,
                "candidate_product_id": row.candidate_product_id,
                "matched_product_id": row.matched_product_id,
                "match_confidence": row.match_confidence,
                "match_reason": row.match_reason,
                "subtitle_method": row.subtitle_method,
                "processing_attempt_count": row.processing_attempt_count,
                "processing_last_error": row.processing_last_error,
                "processing_started_at": row.processing_started_at,
                "processing_next_attempt_at": row.processing_next_attempt_at,
                "site_publish_status": row.site_publish_status,
                "site_publish_last_error": row.site_publish_last_error,
            }
            for row in rows
        ],
    }


@app.get("/api/videos/{video_id}/transcript")
def video_transcript(video_id: str, session: Session = Depends(db_session)) -> dict:
    video = session.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="video not found")
    transcript = session.scalar(
        select(VideoTranscript).where(VideoTranscript.video_id == video_id)
    )
    if transcript is None:
        return {"video_id": video_id, "status": video.processing_status, "transcript": ""}
    return {
        "video_id": video_id,
        "status": transcript.status,
        "method": transcript.method,
        "model_name": transcript.model_name,
        "language": transcript.language,
        "transcript": transcript.transcript,
        "summary": transcript.summary,
        "segments": transcript.segments,
        "updated_at": transcript.updated_at,
    }


@app.get("/api/video-intelligence/status")
def video_intelligence_status(session: Session = Depends(db_session)) -> dict:
    queue = dict(
        session.execute(
            select(Video.processing_status, func.count()).group_by(Video.processing_status)
        ).all()
    )
    facts = dict(
        session.execute(select(VideoFact.status, func.count()).group_by(VideoFact.status)).all()
    )
    site_publish = dict(
        session.execute(
            select(Video.site_publish_status, func.count()).group_by(Video.site_publish_status)
        ).all()
    )
    return {
        "queue": queue,
        "facts": facts,
        "site_publish": site_publish,
    }


@app.get("/api/bom-analysis/components")
def bom_analysis_components(session: Session = Depends(db_session)) -> dict:
    """List cost-analysis component families and their current database coverage."""

    return component_analysis_manifest(session)


@app.get("/api/bom-analysis")
def bom_analysis(
    component_key: str = Query("battery"),
    session: Session = Depends(db_session),
) -> dict:
    """Return source-backed component rows at the approved analytical grain."""

    if component_key not in COMPONENT_LABELS:
        raise HTTPException(status_code=400, detail="unsupported component_key")
    return component_analysis_payload(session, component_key)


@app.get("/api/products/{product_id}/video-intelligence")
def product_video_intelligence(
    product_id: str,
    include_superseded: bool = Query(False),
    session: Session = Depends(db_session),
) -> dict:
    if session.get(Product, product_id) is None:
        raise HTTPException(status_code=404, detail="product not found")
    items = serialize_product_video_intelligence(
        session,
        product_id,
        include_superseded=include_superseded,
    )
    return {"product_id": product_id, "total": len(items), "items": items}


@app.get("/media/{filename}")
def media(filename: str):
    if not MEDIA_NAME.fullmatch(filename):
        raise HTTPException(status_code=404, detail="image not found")
    storage = get_storage()
    object_key = f"images/{filename}"
    if not storage.exists(object_key):
        raise HTTPException(status_code=404, detail="image not found")
    chunks, close = storage.open(object_key)
    return StreamingResponse(
        chunks,
        media_type="image/webp",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
        background=BackgroundTask(close),
    )


@app.get("/video-media/{video_id}/{filename}")
def video_media(video_id: str, filename: str):
    if not VIDEO_ID.fullmatch(video_id) or not VIDEO_MEDIA_NAME.fullmatch(filename):
        raise HTTPException(status_code=404, detail="video keyframe not found")
    storage = get_storage()
    object_key = f"video-keyframes/{video_id}/{filename}"
    if not storage.exists(object_key):
        raise HTTPException(status_code=404, detail="video keyframe not found")
    chunks, close = storage.open(object_key)
    suffix = filename.rsplit(".", 1)[-1].lower()
    media_type = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[suffix]
    return StreamingResponse(
        chunks,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
        background=BackgroundTask(close),
    )


settings = get_settings()
app.mount("/", StaticFiles(directory=settings.site_dir, html=True, check_dir=False), name="site")
