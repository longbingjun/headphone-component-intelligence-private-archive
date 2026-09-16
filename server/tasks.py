from __future__ import annotations

import gc
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from server.config import Settings, get_settings
from server.db import scheduler_lock, session_scope
from server.exporter import export_repository
from server.image_cache import fetch_and_sync_images, image_sync_progress, priority_image_assets
from server.importer import import_deployment_seeds, import_repository
from server.models import CrawlRun
from server.static_site import assert_static_site_complete
from server.storage import get_storage


log = logging.getLogger("intel.refresh")
RETRY_DELAYS_SECONDS = (300, 900, 1800)


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    child_env = dict(os.environ)
    child_env.update(env or {})
    child_env.setdefault("PYTHONIOENCODING", "utf-8")
    log.info("running command: %s", " ".join(command))
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=child_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.stdout:
        log.info(completed.stdout.rstrip())
    if completed.stderr:
        log.warning(completed.stderr.rstrip())
    if completed.returncode:
        raise RuntimeError(f"command failed with exit code {completed.returncode}: {' '.join(command)}")
    return completed.stdout.strip()


def _json_output(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text or "{}")
    except json.JSONDecodeError:
        return {"stdout": text[-4000:]}
    return payload if isinstance(payload, dict) else {"result": payload}


def _npm(*, required: bool = True) -> str | None:
    executable = shutil.which("npm") or shutil.which("npm.cmd")
    if not executable and required:
        raise RuntimeError("npm was not found in the application image")
    return executable


def _build_site_atomically(settings: Settings) -> None:
    """Build beside the live site and swap only after Astro succeeds."""
    site_dir = settings.site_dir.resolve()
    parent = site_dir.parent
    build_dir = parent / f".{site_dir.name}-build-{uuid.uuid4().hex}"
    backup_dir = parent / f".{site_dir.name}-backup-{uuid.uuid4().hex}"
    build_env = {
        "PUBLIC_BASE_PATH": settings.public_base_path,
        "PUBLIC_IMAGE_BASE": settings.public_image_base,
        "ASTRO_OUT_DIR": str(build_dir),
    }
    moved_live_site = False
    try:
        npm = _npm()
        assert npm is not None
        _run([npm, "run", "build:minio"], cwd=settings.root / "web", env=build_env)
        if not (build_dir / "index.html").is_file():
            raise RuntimeError(f"Astro build did not produce {build_dir / 'index.html'}")
        assert_static_site_complete(build_dir)
        if site_dir.exists():
            site_dir.replace(backup_dir)
            moved_live_site = True
        build_dir.replace(site_dir)
    except Exception:
        if moved_live_site and backup_dir.exists() and not site_dir.exists():
            backup_dir.replace(site_dir)
        raise
    finally:
        if build_dir.exists():
            shutil.rmtree(build_dir)
        if backup_dir.exists():
            shutil.rmtree(backup_dir)


def _publish_runtime_data(settings: Settings) -> None:
    """Publish generated JSON without requiring an Astro rebuild at runtime."""
    source = settings.root / "web" / "public" / "data"
    target = settings.site_dir / "data"
    if not source.is_dir():
        raise RuntimeError(f"generated web data is missing: {source}")
    temporary = settings.site_dir / f".data-{uuid.uuid4().hex}"
    backup = settings.site_dir / f".data-backup-{uuid.uuid4().hex}"
    shutil.copytree(source, temporary)
    try:
        if target.exists():
            target.replace(backup)
        temporary.replace(target)
    except Exception:
        if backup.exists() and not target.exists():
            backup.replace(target)
        raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
        if backup.exists():
            shutil.rmtree(backup)


def _execute_refresh(settings: Settings) -> dict[str, Any]:
    root = settings.root
    python = sys.executable
    details: dict[str, Any] = {}

    # Container files are ephemeral. Restore the last durable snapshot before
    # appending newly crawled data so a restart cannot regress the dataset.
    with session_scope() as session:
        details["database_export"] = export_repository(session).to_dict()

    crawl_output = _run(
        [python, "scripts/crawl_v2.py", "--mode", "daily", "--skip-rebuild"], cwd=root
    )
    details["crawl"] = _json_output(crawl_output)

    if settings.crawl_with_ai:
        # Product identity must be resolved before ``build_products`` runs;
        # otherwise a valid staged brand remains stranded under unknown-- IDs.
        details["identity_repair"] = _json_output(
            _run(
                [
                    python,
                    "scripts/repair_product_identities.py",
                    "--all",
                    "--write-overrides",
                    "--title-only",
                    "--missing-brand-only",
                    "--workers",
                    "2",
                ],
                cwd=root,
            )
        )

    derive_command = [python, "scripts/pipeline.py", "derive"]
    details["derive"] = _json_output(_run(derive_command, cwd=root))

    if settings.crawl_with_ai:
        # Only newly discovered coarse-category products are classified. The
        # reviewed overlay is reused, so historical products do not spend API
        # calls on every daily refresh.
        details["earphone_categories_incremental"] = _json_output(
            _run(
                [
                    python,
                    "scripts/classify_earphone_categories.py",
                    "--resume",
                    "--workers",
                    "2",
                ],
                cwd=root,
            )
        )
        details["products_after_categories"] = _json_output(
            _run([python, "scripts/build_products.py"], cwd=root)
        )

    # Rebuild source-grounded product details only for new/changed records.
    # Existing products are cache hits, so the daily task does not respend API
    # calls on the historical corpus.
    details["unboxing_incremental"] = _json_output(
        _run([python, "scripts/enrich_unboxing.py", "--headphones"], cwd=root)
    )
    detail_command = [
        python,
        "scripts/enrich_product_details.py",
        "--changed-only",
        "--workers",
        "2",
    ]
    if settings.crawl_with_ai:
        detail_command.append("--use-llm")
    details["product_details_incremental"] = _json_output(_run(detail_command, cwd=root))
    if settings.crawl_with_ai:
        details["roundup_summaries"] = _json_output(
            _run([python, "scripts/summarize_roundup_insights.py"], cwd=root)
        )
    details["products_after_details"] = _json_output(
        _run([python, "scripts/build_products.py"], cwd=root)
    )

    if settings.price_refresh_enabled:
        details["price_refresh"] = _json_output(
            _run(
                [
                    python,
                    "scripts/enrich_commerce.py",
                    "--priority-unpriced",
                    "--limit",
                    str(settings.price_refresh_batch_size),
                    "--workers",
                    str(settings.price_refresh_workers),
                ],
                cwd=root,
            )
        )
        # Price enrich is a source layer. Rebuild only the affected derived
        # product, priority and comparison data before publishing JSON.
        details["products_after_price"] = _json_output(
            _run([python, "scripts/build_products.py"], cwd=root)
        )
        details["priority_after_price"] = _json_output(
            _run([python, "scripts/build_product_priority.py"], cwd=root)
        )
        details["matrix_after_price"] = _json_output(
            _run([python, "scripts/build_matrix.py"], cwd=root)
        )

    details["prepare_web_data"] = _json_output(
        _run([python, "scripts/prepare_web_data.py"], cwd=root)
    )
    with session_scope() as session:
        details["database_import"] = import_repository(session).to_dict()
        details["priority_image_sync"] = fetch_and_sync_images(
            session, settings, priority_only=True
        ).to_dict()
        details["image_backfill"] = fetch_and_sync_images(
            session,
            settings,
            priority_only=False,
            backfill_limit=settings.image_backfill_batch_size,
        ).to_dict()

    _publish_runtime_data(settings)

    if settings.refresh_static_site:
        if _npm(required=False):
            _build_site_atomically(settings)
            details["site_build"] = {"status": "complete"}
        else:
            details["site_build"] = {
                "status": "skipped",
                "reason": "npm_unavailable_in_runtime_image",
                "data_publish": "complete",
            }
    return details


def _execute_image_bootstrap(settings: Settings) -> dict[str, Any]:
    """Download priority images with Python and publish them to object storage."""
    prepared = _json_output(
        _run([sys.executable, "scripts/prepare_web_data.py"], cwd=settings.root)
    )
    with session_scope() as session:
        imported = import_deployment_seeds(session).to_dict()
        synced = fetch_and_sync_images(session, settings, priority_only=True).to_dict()
        required = priority_image_assets(session, settings.root)
        total = len(required)
        ready = sum(image.storage_status == "ready" for image in required)
    _publish_runtime_data(settings)
    ready_ratio = ready / total if total else 1.0
    details = {
        "prepare_web_data": prepared,
        "database_import": imported,
        "image_sync": synced,
        "image_assets": total,
        "ready_images": ready,
        "ready_ratio": ready_ratio,
    }
    if ready_ratio < settings.image_min_ready_ratio:
        raise RuntimeError(
            f"only {ready}/{total} image assets are ready ({ready_ratio:.2%}); "
            f"required {settings.image_min_ready_ratio:.2%}"
        )
    return details


def run_image_bootstrap(*, trigger: str = "image_bootstrap") -> dict[str, Any]:
    """Run the independent, restart-safe first image synchronization."""
    settings = get_settings()
    if not settings.database_configured:
        raise RuntimeError("DATABASE_URL is required for image bootstrap")

    with scheduler_lock() as acquired:
        if not acquired:
            return {"status": "skipped", "reason": "lock_not_acquired"}
        run_id = str(uuid.uuid4())
        with session_scope() as session:
            session.add(CrawlRun(id=run_id, trigger=trigger, status="running", attempt_count=1))
        try:
            details = _execute_image_bootstrap(settings)
            with session_scope() as session:
                record = session.get(CrawlRun, run_id)
                if record:
                    record.status = "success"
                    record.finished_at = datetime.now(timezone.utc)
                    record.details = details
            return {"status": "success", "run_id": run_id, "details": details}
        except Exception as exc:
            log.exception("image bootstrap failed")
            with session_scope() as session:
                record = session.get(CrawlRun, run_id)
                if record:
                    record.status = "failed"
                    record.finished_at = datetime.now(timezone.utc)
                    record.error_message = str(exc)[:4000]
                    record.errors = 1
            raise


def run_full_image_backfill(*, trigger: str = "image_full_sync") -> dict[str, Any]:
    """Fill every historical image in restart-safe, committed batches."""
    settings = get_settings()
    if not settings.database_configured:
        raise RuntimeError("DATABASE_URL is required for full image synchronization")

    with scheduler_lock() as acquired:
        if not acquired:
            return {"status": "skipped", "reason": "lock_not_acquired"}

        # Reuse one MinIO client and its bounded HTTP connection pool for the
        # complete run. Creating a fresh client per batch left many pools for
        # Python's GC and exhausted ARC's small application container.
        storage = get_storage(settings)
        storage.ensure_ready()
        run_id = str(uuid.uuid4())
        with session_scope() as session:
            initial = image_sync_progress(session)
            session.add(
                CrawlRun(
                    id=run_id,
                    trigger=trigger,
                    status="running",
                    attempt_count=0,
                    details={"progress": initial.to_dict()},
                )
            )

        totals = {"selected": 0, "uploaded": 0, "existing": 0, "cached": 0, "seeded": 0, "failed": 0}
        attempted_failed_keys: set[str] = set()
        batch_number = 0
        final_progress = initial
        try:
            while final_progress.remaining:
                batch_number += 1
                with session_scope() as session:
                    batch = fetch_and_sync_images(
                        session,
                        settings,
                        priority_only=False,
                        include_priority=True,
                        backfill_limit=settings.image_full_sync_batch_size,
                        exclude_object_keys=attempted_failed_keys,
                        storage=storage,
                    )
                    attempted_failed_keys.update(batch.failed_object_keys)
                    after = image_sync_progress(session)
                    record = session.get(CrawlRun, run_id)
                    for key, value in batch.to_dict().items():
                        totals[key] += value
                    if record:
                        record.attempt_count = batch_number
                        record.details = {
                            "batch": batch.to_dict(),
                            "totals": totals.copy(),
                            "progress": after.to_dict(),
                        }
                final_progress = after
                log.info(
                    "full image sync batch %s complete: %s/%s ready, %s unresolved",
                    batch_number,
                    after.ready,
                    after.total,
                    after.remaining,
                )
                if (
                    after.remaining == 0
                    or batch.selected == 0
                ):
                    break
                if settings.image_full_sync_pause_seconds > 0:
                    time.sleep(settings.image_full_sync_pause_seconds)
                gc.collect()

            status = "success" if final_progress.remaining == 0 else "partial"
            with session_scope() as session:
                record = session.get(CrawlRun, run_id)
                if record:
                    record.status = status
                    record.finished_at = datetime.now(timezone.utc)
                    record.errors = final_progress.remaining
                    record.details = {
                        "totals": totals,
                        "progress": final_progress.to_dict(),
                    }
                    record.error_message = (
                        ""
                        if status == "success"
                        else f"{final_progress.remaining} image objects remain unresolved"
                    )
            return {
                "status": status,
                "run_id": run_id,
                "totals": totals,
                "progress": final_progress.to_dict(),
            }
        except Exception as exc:
            log.exception("full image synchronization failed")
            with session_scope() as session:
                record = session.get(CrawlRun, run_id)
                if record:
                    record.status = "failed"
                    record.finished_at = datetime.now(timezone.utc)
                    record.details = {
                        "totals": totals,
                        "progress": final_progress.to_dict(),
                    }
                    record.error_message = str(exc)[:4000]
                    record.errors = max(1, final_progress.remaining)
            raise


def rebuild_site_from_database(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    with session_scope() as session:
        exported = export_repository(session).to_dict()
    prepared = _json_output(
        _run([sys.executable, "scripts/prepare_web_data.py"], cwd=settings.root)
    )
    _build_site_atomically(settings)
    return {"database_export": exported, "prepare_web_data": prepared, "site_build": "complete"}


def run_refresh(*, trigger: str = "schedule", retry_delays: tuple[int, ...] = RETRY_DELAYS_SECONDS) -> dict[str, Any]:
    """Run one complete refresh with cross-replica exclusion and retries."""
    settings = get_settings()
    if not settings.database_configured:
        raise RuntimeError("DATABASE_URL is required for the refresh job")

    with scheduler_lock() as acquired:
        if not acquired:
            log.info("another instance already owns the refresh lock; skipping")
            return {"status": "skipped", "reason": "lock_not_acquired"}

        run_id = str(uuid.uuid4())
        with session_scope() as session:
            session.add(CrawlRun(id=run_id, trigger=trigger, status="running", attempt_count=0))

        last_error = ""
        details: dict[str, Any] = {}
        attempts = len(retry_delays) + 1
        for attempt in range(1, attempts + 1):
            try:
                with session_scope() as session:
                    record = session.get(CrawlRun, run_id)
                    if record:
                        record.attempt_count = attempt
                details = _execute_refresh(settings)
                crawl = details.get("crawl") or {}
                with session_scope() as session:
                    record = session.get(CrawlRun, run_id)
                    if record:
                        record.status = "success"
                        record.finished_at = datetime.now(timezone.utc)
                        record.new_reports = int(crawl.get("new_reports") or 0)
                        record.new_videos = int(crawl.get("new_videos") or 0)
                        record.skipped = int(crawl.get("skipped") or 0)
                        record.errors = int(crawl.get("errors") or 0)
                        record.details = details
                        record.error_message = ""
                return {"status": "success", "run_id": run_id, "details": details}
            except Exception as exc:
                last_error = str(exc)
                log.exception("refresh attempt %s/%s failed", attempt, attempts)
                if attempt <= len(retry_delays):
                    delay = retry_delays[attempt - 1]
                    log.warning("retrying refresh in %s seconds", delay)
                    time.sleep(delay)

        with session_scope() as session:
            record = session.get(CrawlRun, run_id)
            if record:
                record.status = "failed"
                record.finished_at = datetime.now(timezone.utc)
                record.details = details
                record.error_message = last_error[:4000]
        raise RuntimeError(f"refresh failed after {attempts} attempts: {last_error}")
