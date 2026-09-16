"""数据目录单点配置（ETL Phase 1：兼容旧路径 + 双写新布局）。

目标布局见 docs/ETL_ARCHITECTURE.md
  raw/      ← reports, videos
  staging/  ← channel, official, unboxing enrich
  curated/  ← products, matrix, compare
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
WEB_DATA = ROOT / "web" / "public" / "data"

# --- 目标布局 ---
RAW = DATA / "raw"
STAGING = DATA / "staging"
CURATED = DATA / "curated"

# --- 遗留布局（迁移期仍可读）---
LEGACY_REPORTS = DATA / "reports"
LEGACY_VIDEOS = DATA / "videos"
LEGACY_PRODUCTS = DATA / "products"
LEGACY_ENRICH = DATA / "enrich"
LEGACY_COMPARE = DATA / "compare"
LEGACY_MATRIX = DATA / "matrix"
LEGACY_CONFIG = DATA / "config"


def identity_overrides_path(*, for_write: bool = False) -> Path:
    """LLM-reviewed report-to-product identities, separate from raw source data."""
    path = STAGING / "identity" / "report_product_overrides.json"
    if for_write:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def catalog_manual_overrides_path() -> Path:
    """Reviewed catalog decisions normalized for production consumption.

    The editable human-review worksheets are deliberately local-only.  This
    versioned file contains only the accepted, machine-readable outcome needed
    by the catalog builder (source exclusions, identity corrections and the
    latest form-factor taxonomy).
    """

    return DATA / "config" / "catalog_manual_overrides.json"

MANIFEST_PATH = DATA / "manifest.json"


def reports_dir() -> Path:
    if LEGACY_REPORTS.exists() and any(LEGACY_REPORTS.glob("*.json")):
        return LEGACY_REPORTS
    return RAW / "reports"


def videos_dir() -> Path:
    if LEGACY_VIDEOS.exists() and any(LEGACY_VIDEOS.glob("*.json")):
        return LEGACY_VIDEOS
    return RAW / "videos"


def channel_enrich_dir(*, for_write: bool = False) -> Path:
    primary = STAGING / "channel"
    if for_write:
        primary.mkdir(parents=True, exist_ok=True)
        return primary
    if primary.exists() and any(primary.glob("*.json")):
        return primary
    return LEGACY_ENRICH / "channel"


def official_enrich_dir(*, for_write: bool = False) -> Path:
    primary = STAGING / "official"
    if for_write:
        primary.mkdir(parents=True, exist_ok=True)
        return primary
    if primary.exists() and any(primary.glob("*.json")):
        return primary
    return LEGACY_ENRICH / "official"


def unboxing_enrich_dir(*, for_write: bool = False) -> Path:
    primary = STAGING / "unboxing"
    legacy = LEGACY_ENRICH / "unboxing"
    if for_write:
        primary.mkdir(parents=True, exist_ok=True)
        return primary
    if primary.exists() and any(primary.glob("*.json")):
        return primary
    return legacy


def consumer_claims_enrich_dir(*, for_write: bool = False) -> Path:
    """消费者卖点 enrich，按 canonical product ID 缓存。"""
    return staging_enrich_dir("consumer_claims", for_write=for_write)


def unboxing_summary_enrich_dir(*, for_write: bool = False) -> Path:
    """开箱/外观展示摘要，按拆解报告 ID 缓存。"""
    return staging_enrich_dir("unboxing_summaries", for_write=for_write)


def bom_parameters_enrich_dir(*, for_write: bool = False) -> Path:
    """BOM 参数语义 enrich，按 canonical product ID 缓存。"""
    return staging_enrich_dir("bom_parameters", for_write=for_write)


def component_manufacturers_enrich_dir(*, for_write: bool = False) -> Path:
    """Source-grounded LLM fallback results for unresolved component vendors."""
    return staging_enrich_dir("component_manufacturers", for_write=for_write)


def earphone_category_overrides_path(*, for_write: bool = False) -> Path:
    """Versioned product-level business taxonomy derived from report evidence."""

    directory = STAGING / "earphone_categories"
    if for_write:
        directory.mkdir(parents=True, exist_ok=True)
    return directory / "classifications.json"


def launch_enrich_dir(*, for_write: bool = False) -> Path:
    """Launch-date evidence keyed by canonical product ID."""
    primary = STAGING / "launch"
    legacy = LEGACY_ENRICH / "launch"
    if for_write:
        primary.mkdir(parents=True, exist_ok=True)
        return primary
    if primary.exists() and any(primary.glob("*.json")):
        return primary
    return legacy


def staging_enrich_dir(name: str, *, for_write: bool = False) -> Path:
    """Return a named staging area with a read-only legacy fallback."""
    primary = STAGING / name
    legacy = LEGACY_ENRICH / name
    if for_write:
        primary.mkdir(parents=True, exist_ok=True)
        return primary
    if primary.exists() and any(primary.iterdir()):
        return primary
    return legacy


def video_enrich_dir(*, for_write: bool = False) -> Path:
    return staging_enrich_dir("videos", for_write=for_write)


def roundup_insights_path() -> Path:
    """Generated annual/category insight payload consumed by the homepage."""
    return WEB_DATA / "roundup_insights.json"


def ocr_enrich_dir(*, for_write: bool = False) -> Path:
    return staging_enrich_dir("ocr", for_write=for_write)


def price_enrich_dir(*, for_write: bool = False) -> Path:
    return staging_enrich_dir("prices", for_write=for_write)


def commerce_hints_path() -> Path:
    staged = STAGING / "config" / "commerce_hints.json"
    legacy = LEGACY_CONFIG / "commerce_hints.json"
    if staged.exists():
        return staged
    return legacy


def products_dir(*, for_write: bool = False) -> Path:
    primary = CURATED / "products"
    if for_write:
        primary.mkdir(parents=True, exist_ok=True)
        return primary
    if primary.exists() and (primary / "index.json").exists():
        return primary
    return LEGACY_PRODUCTS


def products_index_path(*, for_write: bool = False) -> Path:
    return products_dir(for_write=for_write) / "index.json"


def compare_dir(*, for_write: bool = False) -> Path:
    staged = CURATED / "compare"
    if for_write:
        staged.mkdir(parents=True, exist_ok=True)
        return staged
    if staged.exists() and any(staged.glob("*.json")):
        return staged
    return LEGACY_COMPARE


def matrix_dir(*, for_write: bool = False) -> Path:
    staged = CURATED / "matrix"
    if for_write:
        staged.mkdir(parents=True, exist_ok=True)
        return staged
    if staged.exists() and any(staged.glob("*.json")):
        return staged
    return LEGACY_MATRIX


def write_json(primary: Path, payload: dict) -> None:
    """只写入规范化 ETL 路径；遗留路径仅在迁移期作为读取兜底。"""
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    primary.parent.mkdir(parents=True, exist_ok=True)
    primary.write_text(text, encoding="utf-8")


def write_product_json(canonical_id: str, product: dict) -> None:
    """写出 curated 产品 JSON。"""
    primary = products_dir(for_write=True) / f"{canonical_id}.json"
    write_json(primary, product)


def write_products_index(index: dict) -> None:
    primary = products_index_path(for_write=True)
    write_json(primary, index)


def write_channel_enrich(canonical_id: str, payload: dict) -> None:
    primary = channel_enrich_dir(for_write=True) / f"{canonical_id}.json"
    write_json(primary, payload)


def write_official_enrich(canonical_id: str, payload: dict) -> None:
    primary = official_enrich_dir(for_write=True) / f"{canonical_id}.json"
    write_json(primary, payload)


def write_unboxing_enrich(report_id: str, payload: dict) -> None:
    primary = unboxing_enrich_dir(for_write=True) / f"{report_id}.json"
    write_json(primary, payload)


def write_launch_enrich(canonical_id: str, payload: dict) -> None:
    primary = launch_enrich_dir(for_write=True) / f"{canonical_id}.json"
    write_json(primary, payload)


def last_step_stats(step: str) -> dict | None:
    """读取 data/manifest.json 中指定 step 最近一次记录的 stats（用于构建前后对比校验）。"""
    if not MANIFEST_PATH.exists():
        return None
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    for entry in reversed(manifest.get("steps", [])):
        if entry.get("step") == step:
            return entry.get("stats")
    return None


def update_manifest(*, step: str, stats: dict) -> None:
    """追加构建步骤到 data/manifest.json。"""
    manifest: dict = {}
    if MANIFEST_PATH.exists():
        try:
            manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    steps: list = manifest.setdefault("steps", [])
    steps.append(
        {
            "step": step,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "stats": stats,
        }
    )
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
