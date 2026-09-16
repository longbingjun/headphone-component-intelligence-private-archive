from __future__ import annotations

import hashlib
import mimetypes
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Iterator, Protocol
from urllib.parse import urlsplit

from minio import Minio
from minio.error import S3Error
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from server.config import Settings, get_settings
from server.models import ImageAsset


class ObjectStorage(Protocol):
    def ensure_ready(self) -> None: ...

    def exists(self, object_key: str) -> bool: ...

    def put_path(self, object_key: str, path: Path, content_type: str) -> None: ...

    def open(self, object_key: str) -> tuple[Iterator[bytes], Callable[[], None]]: ...


def minio_connection_target(settings: Settings) -> tuple[str, bool]:
    """Return the host[:port] and TLS flag expected by the MinIO SDK."""
    raw = settings.minio_endpoint.strip().rstrip("/")
    secure = settings.minio_use_ssl
    if "://" in raw:
        parsed = urlsplit(raw)
        if parsed.path not in {"", "/"}:
            raise ValueError("MINIO_ENDPOINT must not include a path")
        raw = parsed.netloc
        secure = parsed.scheme.lower() == "https"
    if not raw:
        raise ValueError("MINIO_ENDPOINT is required")
    if ":" not in raw:
        raw = f"{raw}:{settings.minio_port}"
    return raw, secure


class MinioObjectStorage:
    def __init__(self, settings: Settings):
        if not settings.minio_configured:
            raise RuntimeError("MINIO_ENDPOINT and MinIO credentials are required")
        self.bucket = settings.minio_bucket
        endpoint, secure = minio_connection_target(settings)
        self.client = Minio(
            endpoint,
            secure=secure,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
        )

    def ensure_ready(self) -> None:
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)

    def exists(self, object_key: str) -> bool:
        try:
            self.client.stat_object(self.bucket, object_key)
            return True
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
                return False
            raise

    def put_path(self, object_key: str, path: Path, content_type: str) -> None:
        self.client.fput_object(self.bucket, object_key, str(path), content_type=content_type)

    def open(self, object_key: str) -> tuple[Iterator[bytes], Callable[[], None]]:
        response = self.client.get_object(self.bucket, object_key)

        def chunks() -> Iterator[bytes]:
            yield from response.stream(64 * 1024)

        def close() -> None:
            response.close()
            response.release_conn()

        return chunks(), close


class LocalObjectStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def _path(self, object_key: str) -> Path:
        candidate = (self.root / object_key).resolve()
        if self.root not in candidate.parents:
            raise ValueError("invalid object key")
        return candidate

    def ensure_ready(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def exists(self, object_key: str) -> bool:
        return self._path(object_key).is_file()

    def put_path(self, object_key: str, path: Path, content_type: str) -> None:
        del content_type
        target = self._path(object_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)

    def open(self, object_key: str) -> tuple[Iterator[bytes], Callable[[], None]]:
        handle: BinaryIO = self._path(object_key).open("rb")

        def chunks() -> Iterator[bytes]:
            while data := handle.read(64 * 1024):
                yield data

        return chunks(), handle.close


def get_storage(settings: Settings | None = None) -> ObjectStorage:
    settings = settings or get_settings()
    if settings.storage_backend == "minio":
        return MinioObjectStorage(settings)
    return LocalObjectStorage(settings.local_storage_path)


@dataclass
class ImageSyncStats:
    uploaded: int = 0
    existing: int = 0
    missing: int = 0
    failed: int = 0

    def to_dict(self) -> dict[str, int]:
        return vars(self).copy()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _required_images(session: Session, root: Path | None) -> list[ImageAsset]:
    if root is None:
        return session.scalars(select(ImageAsset).order_by(ImageAsset.id)).all()
    # Local import avoids a module cycle: image_cache uses ObjectStorage.
    from server.image_cache import priority_image_assets

    return priority_image_assets(session, root)


def image_sync_required(
    session: Session,
    storage: ObjectStorage,
    *,
    root: Path | None = None,
) -> tuple[bool, dict[str, int]]:
    """Detect missing priority images and stale DB status after storage loss/switch."""
    required_images = _required_images(session, root)
    # A generated manifest is intentionally absent from ARC's fresh runtime
    # layer; queue bootstrap so Python regenerates it before selecting images.
    not_ready = sum(image.storage_status != "ready" for image in required_images)
    if root is not None and not required_images:
        not_ready = 1
    roundup_not_ready = len(
        session.scalars(
            select(ImageAsset.id)
            .where(
                ImageAsset.owner_type == "roundup",
                ImageAsset.storage_status != "ready",
            )
            .limit(1)
        ).all()
    )
    samples = [image for image in required_images if image.storage_status == "ready"][:5]
    sample_missing = sum(1 for image in samples if not storage.exists(image.object_key))
    if sample_missing:
        required_keys = {image.object_key for image in required_images}
        for image in session.scalars(select(ImageAsset).where(ImageAsset.object_key.in_(required_keys))):
            image.storage_status = "pending"
            image.last_error = "object storage verification failed; queued for resync"
        session.flush()
    return bool(not_ready or roundup_not_ready or sample_missing), {
        "not_ready": not_ready,
        "roundup_not_ready": roundup_not_ready,
        "sampled": len(samples),
        "sample_missing": sample_missing,
    }


def verify_ready_image_samples(
    session: Session,
    storage: ObjectStorage,
    *,
    root: Path | None = None,
) -> dict[str, int]:
    """Read-only object verification used by readiness checks."""
    samples = [image for image in _required_images(session, root) if image.storage_status == "ready"][:5]
    missing = sum(1 for image in samples if not storage.exists(image.object_key))
    return {"sampled": len(samples), "missing": missing}


def sync_cached_images(
    session: Session,
    *,
    storage: ObjectStorage | None = None,
    cache_dir: Path | None = None,
) -> ImageSyncStats:
    settings = get_settings()
    storage = storage or get_storage(settings)
    cache_dir = cache_dir or settings.image_cache_dir
    storage.ensure_ready()
    stats = ImageSyncStats()

    images = session.scalars(select(ImageAsset).order_by(ImageAsset.id)).all()
    for image in images:
        filename = Path(image.object_key).name
        source = cache_dir / filename
        if storage.exists(image.object_key):
            image.storage_status = "ready"
            image.last_error = ""
            stats.existing += 1
            continue
        if not source.is_file():
            image.storage_status = "missing"
            image.last_error = f"cache file not found: {filename}"
            stats.missing += 1
            continue
        try:
            content_type = mimetypes.guess_type(source.name)[0] or "image/webp"
            storage.put_path(image.object_key, source, content_type)
            image.storage_status = "ready"
            image.mime_type = content_type
            image.byte_size = source.stat().st_size
            image.sha256 = _sha256(source)
            image.last_error = ""
            stats.uploaded += 1
        except Exception as exc:
            image.storage_status = "failed"
            image.last_error = str(exc)[:2000]
            stats.failed += 1
    session.flush()
    return stats
