from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def load_local_env(path: Path | None = None) -> None:
    """Load ignored local settings without overriding injected environment secrets."""

    source = path or (ROOT / ".env.local")
    if not source.is_file():
        return
    for raw_line in source.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name and name.replace("_", "").isalnum():
            os.environ.setdefault(name, value.strip().strip('"').strip("'"))


def normalize_database_url(value: str) -> str:
    """Select psycopg 3 when the platform injects a generic PostgreSQL URL."""
    value = value.strip()
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+psycopg://", 1)
    if value.startswith("postgres://"):
        return value.replace("postgres://", "postgresql+psycopg://", 1)
    return value


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _nonnegative_int(name: str, default: int) -> int:
    value = _int(name, default)
    if value < 0:
        raise ValueError(f"{name} must be zero or greater")
    return value


def _float(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def _ratio(name: str, default: float) -> float:
    value = _float(name, default)
    if not 0 <= value <= 1:
        raise ValueError(f"{name} must be between 0 and 1")
    return value


@dataclass(frozen=True)
class Settings:
    root: Path
    database_url: str
    auto_migrate: bool
    import_on_start: bool
    initial_refresh_on_start: bool
    scheduler_enabled: bool
    scheduler_hour: int
    scheduler_minute: int
    scheduler_timezone: str
    scheduler_misfire_grace_seconds: int
    crawl_with_ai: bool
    refresh_static_site: bool
    storage_backend: str
    local_storage_path: Path
    minio_endpoint: str
    minio_port: int
    minio_use_ssl: bool
    minio_access_key: str
    minio_secret_key: str
    minio_bucket: str
    site_dir: Path
    image_cache_dir: Path
    public_base_path: str
    public_image_base: str
    price_refresh_enabled: bool = True
    price_refresh_batch_size: int = 10
    price_refresh_workers: int = 2
    rebuild_site_on_start: bool = False
    initial_image_sync_on_start: bool = False
    image_min_ready_ratio: float = 0.95
    image_fetch_timeout_seconds: int = 20
    image_fetch_retries: int = 1
    image_download_workers: int = 6
    image_backfill_batch_size: int = 100
    image_max_bytes: int = 25 * 1024 * 1024
    full_image_sync_on_start: bool = False
    image_seed_dir: Path = ROOT / "web" / "public" / "images"
    image_full_sync_batch_size: int = 50
    image_full_sync_pause_seconds: float = 1.0
    image_full_sync_max_stagnant_batches: int = 2
    video_cookies_file: str = ""
    # Keep the same scheduling code in every environment. A positive test
    # interval replaces the daily cron trigger and must never be set in prod.
    scheduler_test_interval_seconds: int = 0
    # Backward-compatible default. Container deployments set this to false and
    # run ``python -m server.scheduler_service`` as a separate process.
    scheduler_run_in_web: bool = False
    video_worker_poll_seconds: int = 30
    video_worker_max_attempts: int = 3
    video_worker_retry_seconds: int = 300
    video_worker_lease_seconds: int = 7200
    video_worker_temp_root: Path = ROOT / "runtime" / "video-temp"
    video_worker_ocr_interval_seconds: float = 0.5
    video_worker_rebuild_site: bool = True

    @property
    def database_configured(self) -> bool:
        return bool(self.database_url)

    @property
    def minio_configured(self) -> bool:
        return bool(self.minio_endpoint and self.minio_access_key and self.minio_secret_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_local_env()
    minio_endpoint = os.getenv("MINIO_ENDPOINT", "").strip()
    storage_backend = os.getenv("STORAGE_BACKEND", "").strip().lower()
    if not storage_backend:
        storage_backend = "minio" if minio_endpoint else "local"
    if storage_backend not in {"local", "minio"}:
        raise ValueError("STORAGE_BACKEND must be 'local' or 'minio'")

    public_base_path = os.getenv("PUBLIC_BASE_PATH", "/").strip() or "/"
    if not public_base_path.startswith("/"):
        public_base_path = f"/{public_base_path}"
    if not public_base_path.endswith("/"):
        public_base_path = f"{public_base_path}/"

    public_image_base = os.getenv("PUBLIC_IMAGE_BASE", "/media").strip() or "/media"
    if not public_image_base.startswith("/"):
        public_image_base = f"/{public_image_base}"

    return Settings(
        root=ROOT,
        database_url=normalize_database_url(os.getenv("DATABASE_URL", "")),
        # Schema changes are an explicit deployment step.  Web and Scheduler
        # may start concurrently from the same environment, so neither should
        # race Alembic migrations when AUTO_MIGRATE is omitted.
        auto_migrate=_bool("AUTO_MIGRATE", False),
        import_on_start=_bool("IMPORT_ON_START", False),
        initial_refresh_on_start=_bool("INITIAL_REFRESH_ON_START", False),
        scheduler_enabled=_bool("SCHEDULER_ENABLED", True),
        scheduler_hour=_int("SCHEDULER_HOUR", 9),
        scheduler_minute=_int("SCHEDULER_MINUTE", 10),
        scheduler_timezone=os.getenv("SCHEDULER_TIMEZONE", "Asia/Shanghai"),
        scheduler_misfire_grace_seconds=_int("SCHEDULER_MISFIRE_GRACE_SECONDS", 21600),
        scheduler_test_interval_seconds=_nonnegative_int(
            "SCHEDULER_TEST_INTERVAL_SECONDS", 0
        ),
        scheduler_run_in_web=_bool("SCHEDULER_RUN_IN_WEB", False),
        video_worker_poll_seconds=_nonnegative_int("VIDEO_WORKER_POLL_SECONDS", 30),
        video_worker_max_attempts=max(1, _int("VIDEO_WORKER_MAX_ATTEMPTS", 3)),
        video_worker_retry_seconds=_nonnegative_int("VIDEO_WORKER_RETRY_SECONDS", 300),
        video_worker_lease_seconds=max(60, _int("VIDEO_WORKER_LEASE_SECONDS", 7200)),
        video_worker_temp_root=Path(
            os.getenv("VIDEO_WORKER_TEMP_ROOT", str(ROOT / "runtime" / "video-temp"))
        ),
        video_worker_ocr_interval_seconds=max(
            0.1, _float("VIDEO_WORKER_OCR_INTERVAL_SECONDS", 0.5)
        ),
        video_worker_rebuild_site=_bool("VIDEO_WORKER_REBUILD_SITE", True),
        crawl_with_ai=_bool("CRAWL_WITH_AI", False),
        refresh_static_site=_bool("REFRESH_STATIC_SITE", True),
        storage_backend=storage_backend,
        local_storage_path=Path(os.getenv("LOCAL_STORAGE_PATH", str(ROOT / "runtime" / "objects"))),
        minio_endpoint=minio_endpoint,
        minio_port=_int("MINIO_PORT", 9000),
        minio_use_ssl=_bool("MINIO_USE_SSL", False),
        minio_access_key=os.getenv("MINIO_ACCESS_KEY", "").strip(),
        minio_secret_key=os.getenv("MINIO_SECRET_KEY", "").strip(),
        minio_bucket=os.getenv("MINIO_BUCKET", "product-images").strip() or "product-images",
        site_dir=Path(os.getenv("SITE_DIR", str(ROOT / "site"))),
        image_cache_dir=Path(os.getenv("IMAGE_CACHE_DIR", str(ROOT / "web" / "public" / "images"))),
        public_base_path=public_base_path,
        public_image_base=public_image_base.rstrip("/"),
        price_refresh_enabled=_bool("PRICE_REFRESH_ENABLED", True),
        price_refresh_batch_size=max(1, _int("PRICE_REFRESH_BATCH_SIZE", 10)),
        price_refresh_workers=max(1, _int("PRICE_REFRESH_WORKERS", 2)),
        # ARC mounts the published site on durable storage.  Rebuild from the
        # database after every container replacement so a stale mounted site
        # cannot mask the UI bundled with the new application image.  The
        # builder swaps directories atomically, keeping the previous site live
        # until the replacement is complete.
        rebuild_site_on_start=_bool("REBUILD_SITE_ON_START", False),
        initial_image_sync_on_start=_bool("INITIAL_IMAGE_SYNC_ON_START", False),
        image_min_ready_ratio=_ratio("IMAGE_MIN_READY_RATIO", 0.95),
        image_fetch_timeout_seconds=_int("IMAGE_FETCH_TIMEOUT_SECONDS", 20),
        image_fetch_retries=_int("IMAGE_FETCH_RETRIES", 1),
        image_download_workers=_int("IMAGE_DOWNLOAD_WORKERS", 6),
        image_backfill_batch_size=_int("IMAGE_BACKFILL_BATCH_SIZE", 100),
        image_max_bytes=_int("IMAGE_MAX_BYTES", 25 * 1024 * 1024),
        full_image_sync_on_start=_bool("FULL_IMAGE_SYNC_ON_START", False),
        image_seed_dir=Path(
            os.getenv("IMAGE_SEED_DIR", str(ROOT / "web" / "public" / "images"))
        ),
        image_full_sync_batch_size=_int("IMAGE_FULL_SYNC_BATCH_SIZE", 50),
        image_full_sync_pause_seconds=_float("IMAGE_FULL_SYNC_PAUSE_SECONDS", 1.0),
        image_full_sync_max_stagnant_batches=_int(
            "IMAGE_FULL_SYNC_MAX_STAGNANT_BATCHES", 2
        ),
        video_cookies_file=os.getenv("VIDEO_COOKIES_FILE", "").strip(),
    )
