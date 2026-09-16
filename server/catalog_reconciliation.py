"""Versioned reconciliation of a live catalog with the reviewed release seed.

The production database can contain newer crawler records than the repository.
This module therefore never treats "missing from Git" as permission to delete a
row.  It only merges an old product identity when report/video provenance maps
that row to exactly one reviewed canonical product.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from core.paths import products_dir, reports_dir, videos_dir
from server.importer import import_repository
from server.models import (
    BomItem,
    BomItemParameter,
    DataMigration,
    ImageAsset,
    Product,
    Report,
    Video,
    VideoFact,
)


MIGRATION_ID = "catalog-normalization-20260915-v1"


@dataclass(frozen=True)
class IdentityMove:
    source_id: str
    target_id: str
    basis: str
    source_ids: tuple[str, ...] = ()


@dataclass
class ReconciliationPlan:
    migration_id: str = MIGRATION_ID
    database_products: int = 0
    canonical_products: int = 0
    moves: list[IdentityMove] = field(default_factory=list)
    conflicts: dict[str, list[str]] = field(default_factory=dict)
    retired_legacy_products: list[str] = field(default_factory=list)
    preserved_runtime_products: list[str] = field(default_factory=list)

    def to_dict(self, *, include_all: bool = False) -> dict[str, Any]:
        moves = [asdict(item) for item in self.moves]
        preserved = self.preserved_runtime_products
        retired = self.retired_legacy_products
        conflicts = self.conflicts
        return {
            "migration_id": self.migration_id,
            "database_products": self.database_products,
            "canonical_products": self.canonical_products,
            "planned_identity_moves": len(self.moves),
            "conflicts": len(self.conflicts),
            "retired_legacy_products": len(retired),
            "preserved_runtime_products": len(self.preserved_runtime_products),
            "move_rows": moves if include_all else moves[:50],
            "conflict_rows": conflicts if include_all else dict(list(conflicts.items())[:50]),
            "retired_legacy_product_ids": retired if include_all else retired[:50],
            "preserved_runtime_product_ids": preserved if include_all else preserved[:50],
            "details_truncated": not include_all
            and (
                len(moves) > 50
                or len(conflicts) > 50
                or len(retired) > 50
                or len(preserved) > 50
            ),
        }


@dataclass
class ReconciliationResult:
    mode: str
    migration_id: str
    already_applied: bool
    plan: dict[str, Any]
    imported: dict[str, int] | None = None
    moved_products: int = 0
    moved_report_links: int = 0
    moved_video_links: int = 0
    moved_video_facts: int = 0
    moved_video_bom_items: int = 0
    deleted_report_bom_items: int = 0
    moved_product_images: int = 0
    merged_product_images: int = 0
    updated_video_pointers: int = 0
    retired_legacy_products: int = 0
    unresolved_identity_conflicts: int = 0
    product_count_before: int = 0
    product_count_after: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CanonicalCatalog:
    payloads: dict[str, dict[str, Any]]
    report_targets: dict[str, frozenset[str]]
    video_targets: dict[str, frozenset[str]]
    repository_report_ids: frozenset[str] = frozenset()
    repository_video_ids: frozenset[str] = frozenset()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def _target_index(values: dict[str, set[str]]) -> dict[str, frozenset[str]]:
    return {key: frozenset(sorted(targets)) for key, targets in values.items()}


def load_canonical_catalog(directory: Path | None = None) -> CanonicalCatalog:
    directory = directory or products_dir()
    payloads: dict[str, dict[str, Any]] = {}
    report_targets: dict[str, set[str]] = {}
    video_targets: dict[str, set[str]] = {}
    for path in sorted(directory.glob("*.json")):
        if path.name == "index.json":
            continue
        payload = _read_json(path)
        product_id = str(payload.get("canonical_id") or "").strip()
        if not product_id:
            continue
        payloads[product_id] = payload
        for report_id in payload.get("report_ids") or []:
            report_targets.setdefault(str(report_id), set()).add(product_id)
        for video_id in payload.get("video_ids") or payload.get("related_video_ids") or []:
            video_targets.setdefault(str(video_id), set()).add(product_id)
    return CanonicalCatalog(
        payloads=payloads,
        report_targets=_target_index(report_targets),
        video_targets=_target_index(video_targets),
        repository_report_ids=frozenset(path.stem for path in reports_dir().glob("*.json")),
        repository_video_ids=frozenset(path.stem for path in videos_dir().glob("*.json")),
    )


def _product_source_ids(product: Product) -> tuple[set[str], set[str]]:
    payload = product.payload if isinstance(product.payload, dict) else {}
    report_ids = {str(item.id) for item in product.reports}
    report_ids.update(str(value) for value in payload.get("report_ids") or [] if value)
    report_ids.update(
        str(item.source_report_id)
        for item in product.bom_items
        if item.source_report_id
    )
    video_ids = {str(item.id) for item in product.videos}
    video_ids.update(str(value) for value in payload.get("video_ids") or [] if value)
    video_ids.update(str(value) for value in payload.get("related_video_ids") or [] if value)
    return report_ids, video_ids


def build_reconciliation_plan(
    session: Session,
    catalog: CanonicalCatalog | None = None,
) -> ReconciliationPlan:
    catalog = catalog or load_canonical_catalog()
    products = session.scalars(select(Product).order_by(Product.id)).unique().all()
    plan = ReconciliationPlan(
        database_products=len(products),
        canonical_products=len(catalog.payloads),
    )
    canonical_ids = set(catalog.payloads)
    for product in products:
        if product.id in canonical_ids:
            continue
        report_ids, video_ids = _product_source_ids(product)
        report_matches = {
            target
            for report_id in report_ids
            for target in catalog.report_targets.get(report_id, ())
        }
        video_matches = {
            target
            for video_id in video_ids
            for target in catalog.video_targets.get(video_id, ())
        }
        # Complete teardown evidence is authoritative. Video provenance is used
        # only when the source row has no report-backed canonical destination.
        matches = report_matches or video_matches
        basis = "report_provenance" if report_matches else "video_provenance"
        source_ids: Iterable[str] = report_ids if report_matches else video_ids
        if len(matches) == 1:
            plan.moves.append(
                IdentityMove(
                    source_id=product.id,
                    target_id=next(iter(matches)),
                    basis=basis,
                    source_ids=tuple(sorted(source_ids)),
                )
            )
        elif len(matches) > 1:
            # A repository-known source that now belongs to several reviewed
            # canonical products is an obsolete combined identity. Its source
            # report/video remains; only the derived legacy product is retired.
            known_source = bool(
                report_ids.intersection(catalog.repository_report_ids)
                or video_ids.intersection(catalog.repository_video_ids)
            )
            if known_source:
                plan.retired_legacy_products.append(product.id)
            else:
                plan.conflicts[product.id] = sorted(matches)
        else:
            # A source that exists in this release but no longer produces a
            # canonical product is a reviewed legacy artifact. A source absent
            # from the release is normally a newer server-side increment and
            # must be preserved.
            known_source = bool(
                report_ids.intersection(catalog.repository_report_ids)
                or video_ids.intersection(catalog.repository_video_ids)
            )
            if known_source:
                plan.retired_legacy_products.append(product.id)
            else:
                plan.preserved_runtime_products.append(product.id)
    return plan


def _max_date(left: date | None, right: date | None) -> date | None:
    values = [value for value in (left, right) if value is not None]
    return max(values) if values else None


def _min_date(left: date | None, right: date | None) -> date | None:
    values = [value for value in (left, right) if value is not None]
    return min(values) if values else None


def _merge_payload_ids(target: Product, source: Product) -> None:
    payload = dict(target.payload or {})
    source_payload = dict(source.payload or {})
    report_ids = {
        str(value)
        for value in [
            *(payload.get("report_ids") or []),
            *(source_payload.get("report_ids") or []),
            *(report.id for report in target.reports),
            *(report.id for report in source.reports),
        ]
        if value
    }
    video_ids = {
        str(value)
        for value in [
            *(payload.get("video_ids") or []),
            *(payload.get("related_video_ids") or []),
            *(source_payload.get("video_ids") or []),
            *(source_payload.get("related_video_ids") or []),
            *(video.id for video in target.videos),
            *(video.id for video in source.videos),
        ]
        if value
    }
    payload["canonical_id"] = target.id
    payload["report_ids"] = sorted(report_ids)
    payload["related_report_ids"] = sorted(report_ids)
    payload["video_ids"] = sorted(video_ids)
    payload["related_video_ids"] = sorted(video_ids)
    target.payload = payload


def _merge_product(
    session: Session,
    source: Product,
    target: Product,
    result: ReconciliationResult,
) -> None:
    report_ids = {item.id for item in target.reports}
    for report in list(source.reports):
        if report.id not in report_ids:
            target.reports.append(report)
            report_ids.add(report.id)
            result.moved_report_links += 1
    video_ids = {item.id for item in target.videos}
    for video in list(source.videos):
        if video.id not in video_ids:
            target.videos.append(video)
            video_ids.add(video.id)
            result.moved_video_links += 1

    for video in session.scalars(
        select(Video).where(
            (Video.candidate_product_id == source.id)
            | (Video.matched_product_id == source.id)
        )
    ):
        changed = False
        if video.candidate_product_id == source.id:
            video.candidate_product_id = target.id
            changed = True
        if video.matched_product_id == source.id:
            video.matched_product_id = target.id
            changed = True
        if changed:
            result.updated_video_pointers += 1

    facts = session.scalars(select(VideoFact).where(VideoFact.product_id == source.id)).all()
    for fact in facts:
        fact.product_id = target.id
    result.moved_video_facts += len(facts)

    report_bom = session.scalars(
        select(BomItem).where(
            BomItem.product_id == source.id,
            BomItem.source_type != "video",
        )
    ).all()
    report_bom_ids = [item.id for item in report_bom]
    if report_bom_ids:
        session.execute(
            delete(BomItemParameter).where(
                BomItemParameter.bom_item_id.in_(report_bom_ids)
            )
        )
        for item in report_bom:
            session.delete(item)
    result.deleted_report_bom_items += len(report_bom)

    target_video_keys = {
        (item.source_video_id, item.source_video_fact_id, item.component_key, item.model)
        for item in session.scalars(
            select(BomItem).where(
                BomItem.product_id == target.id,
                BomItem.source_type == "video",
            )
        )
    }
    source_video_bom = session.scalars(
        select(BomItem).where(
            BomItem.product_id == source.id,
            BomItem.source_type == "video",
        )
    ).all()
    for item in source_video_bom:
        key = (item.source_video_id, item.source_video_fact_id, item.component_key, item.model)
        if key in target_video_keys:
            session.delete(item)
            continue
        item.product = target
        target_video_keys.add(key)
        result.moved_video_bom_items += 1

    target_images = {
        item.source_url: item
        for item in session.scalars(
            select(ImageAsset).where(
                ImageAsset.owner_type == "product",
                ImageAsset.owner_id == target.id,
            )
        )
    }
    source_images = session.scalars(
        select(ImageAsset).where(
            ImageAsset.owner_type == "product",
            ImageAsset.owner_id == source.id,
        )
    ).all()
    for image in source_images:
        duplicate = target_images.get(image.source_url)
        if duplicate is None:
            image.owner_id = target.id
            target_images[image.source_url] = image
            result.moved_product_images += 1
            continue
        if duplicate.storage_status != "ready" and image.storage_status == "ready":
            for field_name in (
                "object_key",
                "mime_type",
                "byte_size",
                "sha256",
                "width",
                "height",
                "storage_status",
                "last_error",
            ):
                setattr(duplicate, field_name, getattr(image, field_name))
        session.delete(image)
        result.merged_product_images += 1

    if target.price_amount is None and source.price_amount is not None:
        target.price_amount = Decimal(source.price_amount)
        target.price_currency = source.price_currency
    target.first_seen = _min_date(target.first_seen, source.first_seen)
    target.latest_published = _max_date(target.latest_published, source.latest_published)
    target.launch_date = _min_date(target.launch_date, source.launch_date)
    if target.data_completeness is None:
        target.data_completeness = source.data_completeness
    _merge_payload_ids(target, source)
    session.flush()
    session.delete(source)
    result.moved_products += 1


def _retire_legacy_product(
    session: Session,
    product: Product,
    result: ReconciliationResult,
) -> None:
    """Remove one obsolete derived identity while retaining its source records."""

    session.execute(
        delete(ImageAsset).where(
            ImageAsset.owner_type == "product",
            ImageAsset.owner_id == product.id,
        )
    )
    session.execute(delete(VideoFact).where(VideoFact.product_id == product.id))
    for video in session.scalars(
        select(Video).where(
            (Video.candidate_product_id == product.id)
            | (Video.matched_product_id == product.id)
        )
    ):
        if video.candidate_product_id == product.id:
            video.candidate_product_id = ""
        if video.matched_product_id == product.id:
            video.matched_product_id = ""
        result.updated_video_pointers += 1
    session.delete(product)
    result.retired_legacy_products += 1


def _database_counts(session: Session) -> dict[str, int]:
    return {
        "products": session.scalar(select(func.count()).select_from(Product)) or 0,
        "reports": session.scalar(select(func.count()).select_from(Report)) or 0,
        "videos": session.scalar(select(func.count()).select_from(Video)) or 0,
        "video_facts": session.scalar(select(func.count()).select_from(VideoFact)) or 0,
        "bom_items": session.scalar(select(func.count()).select_from(BomItem)) or 0,
        "images": session.scalar(select(func.count()).select_from(ImageAsset)) or 0,
    }


def reconcile_release_catalog(
    session: Session,
    *,
    apply: bool,
    catalog: CanonicalCatalog | None = None,
    source_commit: str | None = None,
) -> ReconciliationResult:
    """Plan or apply the reviewed release seed without dropping runtime-only rows."""

    catalog = catalog or load_canonical_catalog()
    marker = session.get(DataMigration, MIGRATION_ID)
    before = _database_counts(session)
    initial_plan = build_reconciliation_plan(session, catalog)
    result = ReconciliationResult(
        mode="apply" if apply else "dry-run",
        migration_id=MIGRATION_ID,
        already_applied=marker is not None,
        plan=initial_plan.to_dict(include_all=not apply),
        product_count_before=before["products"],
        product_count_after=before["products"],
        unresolved_identity_conflicts=len(initial_plan.conflicts),
    )
    if not apply or marker is not None:
        return result
    imported = import_repository(session, preserve_runtime_evidence=True)
    result.imported = imported.to_dict()
    session.flush()
    plan = build_reconciliation_plan(session, catalog)
    # Preserve ambiguous legacy rows instead of guessing. A later explicitly
    # versioned migration may resolve them after human review.
    result.unresolved_identity_conflicts = len(plan.conflicts)
    for move in plan.moves:
        source = session.get(Product, move.source_id)
        target = session.get(Product, move.target_id)
        if source is None or target is None or source.id == target.id:
            continue
        _merge_product(session, source, target, result)
        session.flush()

    for product_id in plan.retired_legacy_products:
        product = session.get(Product, product_id)
        if product is None:
            continue
        _retire_legacy_product(session, product, result)
        session.flush()

    after = _database_counts(session)
    result.product_count_after = after["products"]
    details = {
        "before": before,
        "after": after,
        "result": result.to_dict(),
    }
    session.add(
        DataMigration(
            id=MIGRATION_ID,
            source_commit=(source_commit or os.getenv("APP_COMMIT_SHA", "")).strip(),
            status="applied_with_conflicts" if plan.conflicts else "applied",
            details=details,
        )
    )
    session.flush()
    return result
