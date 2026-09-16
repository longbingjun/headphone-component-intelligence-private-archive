from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from server.config import Settings
from server.models import ImageAsset
from server.storage import ObjectStorage, get_storage


log = logging.getLogger("intel.images")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def repair_legacy_image_url(source_url: str) -> str:
    """Undo UTF-8 bytes that older extraction data decoded as Latin-1."""
    try:
        repaired = source_url.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return source_url
    return repaired if repaired.startswith(("http://", "https://")) else source_url


def image_request_headers(source_url: str) -> dict[str, str]:
    host = (urlsplit(source_url).hostname or "").lower()
    if host.endswith("sinaimg.cn"):
        referer = "https://weibo.com/"
    elif host == "note.youdao.com":
        referer = "https://note.youdao.com/"
    else:
        referer = "https://www.52audio.com/"
    return {"User-Agent": USER_AGENT, "Referer": referer}


@dataclass
class RemoteImageSyncStats:
    selected: int = 0
    uploaded: int = 0
    existing: int = 0
    cached: int = 0
    seeded: int = 0
    failed: int = 0
    failed_object_keys: set[str] = field(default_factory=set, repr=False)

    def to_dict(self) -> dict[str, int]:
        return {
            "selected": self.selected,
            "uploaded": self.uploaded,
            "existing": self.existing,
            "cached": self.cached,
            "seeded": self.seeded,
            "failed": self.failed,
        }


@dataclass
class ImageSyncProgress:
    total: int = 0
    ready: int = 0
    pending: int = 0
    failed: int = 0

    @property
    def remaining(self) -> int:
        return self.total - self.ready

    @property
    def ready_ratio(self) -> float:
        return self.ready / self.total if self.total else 1.0

    def to_dict(self) -> dict[str, int | float]:
        return {
            "total": self.total,
            "ready": self.ready,
            "pending": self.pending,
            "failed": self.failed,
            "remaining": self.remaining,
            "ready_ratio": self.ready_ratio,
        }


def image_sync_progress(session: Session) -> ImageSyncProgress:
    """Return progress by unique MinIO object key rather than duplicate DB rows."""
    # Keep aggregation inside PostgreSQL. Materializing every object key and its
    # status in Python on every batch caused the long-running ARC worker's RSS to
    # grow until the low-memory application container was killed.
    per_key = (
        select(
            ImageAsset.object_key.label("object_key"),
            func.max(
                case((ImageAsset.storage_status == "ready", 1), else_=0)
            ).label("is_ready"),
            func.max(
                case(
                    (ImageAsset.storage_status.in_(("failed", "missing")), 1),
                    else_=0,
                )
            ).label("is_failed"),
        )
        .group_by(ImageAsset.object_key)
        .subquery()
    )
    total, ready, failed = session.execute(
        select(
            func.count(),
            func.coalesce(func.sum(per_key.c.is_ready), 0),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (per_key.c.is_ready == 0)
                            & (per_key.c.is_failed == 1),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
        ).select_from(per_key)
    ).one()
    total = int(total or 0)
    ready = int(ready or 0)
    failed = int(failed or 0)
    return ImageSyncProgress(
        total=total,
        ready=ready,
        failed=failed,
        pending=max(0, total - ready - failed),
    )


def _object_key_from_public_path(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    filename = value.rsplit("/", 1)[-1]
    if len(filename) == 21 and filename.endswith(".webp"):
        return f"images/{filename}"
    return None


def priority_image_object_keys(root: Path) -> set[str]:
    """Load the images needed by cards and annual insight reports."""
    data_dir = root / "web" / "public" / "data"
    keys: set[str] = set()
    try:
        index = json.loads((data_dir / "products" / "index.json").read_text(encoding="utf-8"))
        for product in index.get("products") or []:
            key = _object_key_from_public_path(product.get("card_image_path"))
            if key:
                keys.add(key)
    except (OSError, json.JSONDecodeError, AttributeError):
        log.warning("product card image manifest is unavailable")

    try:
        roundup = json.loads((data_dir / "roundup_insights.json").read_text(encoding="utf-8"))
        for report in roundup.get("reports") or []:
            for image in (report.get("digest") or {}).get("image_highlights") or []:
                key = _object_key_from_public_path(image.get("local_path"))
                if key:
                    keys.add(key)
    except (OSError, json.JSONDecodeError, AttributeError):
        log.warning("roundup image manifest is unavailable")
    return keys


def priority_image_assets(session: Session, root: Path) -> list[ImageAsset]:
    keys = priority_image_object_keys(root)
    if not keys:
        return session.scalars(
            select(ImageAsset).where(ImageAsset.owner_type == "roundup").order_by(ImageAsset.id)
        ).all()
    return session.scalars(
        select(ImageAsset)
        .where((ImageAsset.object_key.in_(keys)) | (ImageAsset.owner_type == "roundup"))
        .order_by(ImageAsset.id)
    ).all()


def requeue_missing_recovered_images(
    session: Session,
    settings: Settings,
    storage: ObjectStorage,
) -> dict[str, int]:
    """Verify only the versioned recovery batch and requeue missing MinIO keys."""
    manifest = settings.root / "data" / "seed" / "recovered_image_keys.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"listed": 0, "missing": 0, "requeued_rows": 0}
    keys = {
        str(value).strip()
        for value in payload
        if isinstance(value, str) and str(value).startswith("images/")
    } if isinstance(payload, list) else set()
    missing = {key for key in keys if not storage.exists(key)}
    rows = (
        session.scalars(select(ImageAsset).where(ImageAsset.object_key.in_(missing))).all()
        if missing
        else []
    )
    for row in rows:
        row.storage_status = "pending"
        row.last_error = "recovered seed is absent from object storage; queued for upload"
    session.flush()
    return {"listed": len(keys), "missing": len(missing), "requeued_rows": len(rows)}


def _download_image(source_url: str, target: Path, settings: Settings) -> tuple[int, int, int, str]:
    import requests
    from PIL import Image, ImageFile, ImageOps

    ImageFile.LOAD_TRUNCATED_IMAGES = True
    request_url = repair_legacy_image_url(source_url)
    last_error: Exception | None = None
    for attempt in range(settings.image_fetch_retries + 1):
        try:
            with requests.get(
                request_url,
                headers=image_request_headers(request_url),
                timeout=(5, settings.image_fetch_timeout_seconds),
                stream=True,
            ) as response:
                response.raise_for_status()
                payload = io.BytesIO()
                for chunk in response.iter_content(64 * 1024):
                    if not chunk:
                        continue
                    if payload.tell() + len(chunk) > settings.image_max_bytes:
                        raise RuntimeError("source image exceeds IMAGE_MAX_BYTES")
                    payload.write(chunk)
            payload.seek(0)
            with Image.open(payload) as opened:
                image = ImageOps.exif_transpose(opened)
                if getattr(image, "is_animated", False):
                    image.seek(0)
                image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                if image.mode not in {"RGB", "L"}:
                    background = Image.new("RGB", image.size, "white")
                    if "A" in image.getbands():
                        background.paste(image, mask=image.getchannel("A"))
                    else:
                        background.paste(image)
                    image = background
                elif image.mode == "L":
                    image = image.convert("RGB")
                width, height = image.size
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_suffix(f".{os.getpid()}.part")
                image.save(temporary, "WEBP", quality=78, method=4)
                temporary.replace(target)
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            return width, height, target.stat().st_size, digest
        except Exception as exc:
            last_error = exc
            if attempt < settings.image_fetch_retries:
                time.sleep(1 + attempt)
    raise RuntimeError(str(last_error or "image download failed"))


def _deduplicate_assets(assets: Iterable[ImageAsset]) -> dict[str, str]:
    result: dict[str, str] = {}
    for image in assets:
        result.setdefault(image.object_key, image.source_url)
    return result


def _available_local_image(settings: Settings, object_key: str) -> tuple[Path, bool] | None:
    """Prefer the versioned historical WebP seed, then the runtime cache."""
    filename = Path(object_key).name
    seed_directories = (
        settings.image_seed_dir,
        settings.root / "web" / "public" / "images",
    )
    checked: set[Path] = set()
    for directory in seed_directories:
        seed = directory / filename
        if seed in checked:
            continue
        checked.add(seed)
        if seed.is_file():
            return seed, True
    cached = settings.image_cache_dir / filename
    if cached.is_file():
        return cached, False
    return None


def fetch_and_sync_images(
    session: Session,
    settings: Settings,
    *,
    priority_only: bool,
    include_priority: bool = False,
    backfill_limit: int | None = None,
    exclude_object_keys: set[str] | None = None,
    owner_type: str | None = None,
    owner_id: str | None = None,
    storage: ObjectStorage | None = None,
) -> RemoteImageSyncStats:
    """Fetch optimized WebP derivatives with Python and publish them to storage."""
    storage = storage or get_storage(settings)
    storage.ensure_ready()
    priority = priority_image_assets(session, settings.root)
    if priority_only and not owner_type and not owner_id:
        assets = priority
    else:
        priority_keys = {item.object_key for item in priority}
        filters = [ImageAsset.storage_status != "ready"]
        if priority_keys and not include_priority and not owner_type and not owner_id:
            filters.append(ImageAsset.object_key.not_in(priority_keys))
        if exclude_object_keys:
            filters.append(ImageAsset.object_key.not_in(exclude_object_keys))
        if owner_type:
            filters.append(ImageAsset.owner_type == owner_type)
        if owner_id:
            filters.append(ImageAsset.owner_id == owner_id)
        assets = session.scalars(
            select(ImageAsset)
            .where(*filters)
            # New/pending objects must not be starved by permanently failed URLs.
            .order_by(
                case(
                    (ImageAsset.storage_status == "pending", 0),
                    (ImageAsset.storage_status == "missing", 1),
                    else_=2,
                ),
                ImageAsset.id,
            )
            .limit(backfill_limit or settings.image_backfill_batch_size)
        ).all()

    selected = _deduplicate_assets(assets)
    stats = RemoteImageSyncStats(selected=len(selected))
    rows_by_key: dict[str, list[ImageAsset]] = {}
    for image in session.scalars(select(ImageAsset).where(ImageAsset.object_key.in_(selected))).all() if selected else []:
        rows_by_key.setdefault(image.object_key, []).append(image)

    pending: dict[str, tuple[str, Path]] = {}
    local_sources: dict[str, Path] = {}
    metadata: dict[str, tuple[int | None, int | None, int, str]] = {}
    for object_key, source_url in selected.items():
        target = settings.image_cache_dir / Path(object_key).name
        if storage.exists(object_key):
            stats.existing += 1
            metadata[object_key] = (None, None, 0, "")
        else:
            available = _available_local_image(settings, object_key)
            if available:
                source, is_seed = available
                local_sources[object_key] = source
                if is_seed:
                    stats.seeded += 1
                else:
                    stats.cached += 1
                metadata[object_key] = (
                    None,
                    None,
                    source.stat().st_size,
                    hashlib.sha256(source.read_bytes()).hexdigest(),
                )
            else:
                pending[object_key] = (source_url, target)

    failures: dict[str, str] = {}
    if pending:
        with ThreadPoolExecutor(max_workers=max(1, settings.image_download_workers)) as executor:
            future_keys = {
                executor.submit(_download_image, url, target, settings): key
                for key, (url, target) in pending.items()
            }
            for number, future in enumerate(as_completed(future_keys), start=1):
                key = future_keys[future]
                try:
                    width, height, byte_size, digest = future.result()
                    local_sources[key] = settings.image_cache_dir / Path(key).name
                    metadata[key] = (width, height, byte_size, digest)
                except Exception as exc:
                    failures[key] = str(exc)[:2000]
                if number % 25 == 0 or number == len(future_keys):
                    log.info("image download progress %s/%s", number, len(future_keys))

    for object_key in selected:
        rows = rows_by_key.get(object_key, [])
        if object_key in failures:
            for row in rows:
                row.storage_status = "failed"
                row.last_error = failures[object_key]
            stats.failed += 1
            stats.failed_object_keys.add(object_key)
            continue
        source = local_sources.get(object_key)
        try:
            if not storage.exists(object_key):
                if source is None or not source.is_file():
                    raise RuntimeError(f"local image is unavailable: {Path(object_key).name}")
                storage.put_path(object_key, source, "image/webp")
                stats.uploaded += 1
            width, height, byte_size, digest = metadata.get(object_key, (None, None, 0, ""))
            for row in rows:
                row.storage_status = "ready"
                row.mime_type = "image/webp"
                row.last_error = ""
                if width:
                    row.width = width
                if height:
                    row.height = height
                if byte_size:
                    row.byte_size = byte_size
                if digest:
                    row.sha256 = digest
        except Exception as exc:
            for row in rows:
                row.storage_status = "failed"
                row.last_error = str(exc)[:2000]
            stats.failed += 1
            stats.failed_object_keys.add(object_key)
    session.flush()
    return stats
