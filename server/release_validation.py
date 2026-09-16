"""Post-migration integrity checks for a PostgreSQL/MinIO release candidate."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.earphone_categories import BUSINESS_CATEGORIES, LEGACY_COARSE_CATEGORIES, PENDING_CATEGORY
from server.catalog_reconciliation import CanonicalCatalog, MIGRATION_ID, load_canonical_catalog
from server.models import DataMigration


@dataclass(frozen=True)
class ValidationIssue:
    name: str
    count: int
    sample: list[str]
    blocking: bool


def _issue(
    session: Session,
    *,
    name: str,
    sql: str,
    blocking: bool = True,
    limit: int = 20,
) -> ValidationIssue:
    normalized_sql = sql.strip().rstrip(";")
    count = int(
        session.execute(text(f"SELECT COUNT(*) FROM ({normalized_sql}) release_issue")).scalar_one()
    )
    rows = session.execute(text(f"{normalized_sql} LIMIT {int(limit)}")).all() if count else []
    return ValidationIssue(
        name=name,
        count=count,
        sample=[" | ".join("" if value is None else str(value) for value in row) for row in rows],
        blocking=blocking,
    )


def validate_release_data(
    session: Session,
    *,
    catalog: CanonicalCatalog | None = None,
) -> dict[str, Any]:
    """Return a machine-readable release gate without mutating the database."""

    catalog = catalog or load_canonical_catalog()
    database_product_ids = set(session.execute(text("SELECT id FROM products")).scalars())
    missing_canonical = sorted(set(catalog.payloads) - database_product_ids)
    marker = session.get(DataMigration, MIGRATION_ID)

    issues = [
        _issue(
            session,
            name="orphan_product_report_links",
            sql="""
                SELECT l.product_id, l.report_id
                FROM product_report_links l
                LEFT JOIN products p ON p.id = l.product_id
                LEFT JOIN reports r ON r.id = l.report_id
                WHERE p.id IS NULL OR r.id IS NULL
            """,
        ),
        _issue(
            session,
            name="orphan_product_video_links",
            sql="""
                SELECT l.product_id, l.video_id
                FROM product_video_links l
                LEFT JOIN products p ON p.id = l.product_id
                LEFT JOIN videos v ON v.id = l.video_id
                WHERE p.id IS NULL OR v.id IS NULL
            """,
        ),
        _issue(
            session,
            name="orphan_video_facts",
            sql="""
                SELECT f.id, f.video_id, f.product_id
                FROM video_facts f
                LEFT JOIN videos v ON v.id = f.video_id
                LEFT JOIN products p ON p.id = f.product_id
                WHERE v.id IS NULL OR p.id IS NULL
            """,
        ),
        _issue(
            session,
            name="orphan_bom_items",
            sql="""
                SELECT b.id, b.product_id
                FROM bom_items b LEFT JOIN products p ON p.id = b.product_id
                WHERE p.id IS NULL
            """,
        ),
        _issue(
            session,
            name="orphan_bom_parameters",
            sql="""
                SELECT bp.id, bp.bom_item_id
                FROM bom_item_parameters bp LEFT JOIN bom_items b ON b.id = bp.bom_item_id
                WHERE b.id IS NULL
            """,
        ),
        _issue(
            session,
            name="broken_video_bom_provenance",
            sql="""
                SELECT b.id, b.source_video_id, b.source_video_fact_id
                FROM bom_items b
                LEFT JOIN videos v ON v.id = b.source_video_id
                LEFT JOIN video_facts f ON f.id = b.source_video_fact_id
                WHERE b.source_type = 'video'
                  AND ((COALESCE(b.source_video_id, '') <> '' AND v.id IS NULL)
                    OR (b.source_video_fact_id IS NOT NULL AND f.id IS NULL))
            """,
        ),
        _issue(
            session,
            name="broken_video_product_pointers",
            sql="""
                SELECT v.id, v.candidate_product_id, v.matched_product_id
                FROM videos v
                LEFT JOIN products cp ON cp.id = v.candidate_product_id
                LEFT JOIN products mp ON mp.id = v.matched_product_id
                WHERE (COALESCE(v.candidate_product_id, '') <> '' AND cp.id IS NULL)
                   OR (COALESCE(v.matched_product_id, '') <> '' AND mp.id IS NULL)
            """,
        ),
        _issue(
            session,
            name="orphan_product_images",
            sql="""
                SELECT i.id, i.owner_id
                FROM image_assets i LEFT JOIN products p ON p.id = i.owner_id
                WHERE i.owner_type = 'product' AND p.id IS NULL
            """,
        ),
        _issue(
            session,
            name="orphan_report_images",
            sql="""
                SELECT i.id, i.owner_id
                FROM image_assets i LEFT JOIN reports r ON r.id = i.owner_id
                WHERE i.owner_type = 'report' AND r.id IS NULL
            """,
        ),
        _issue(
            session,
            name="orphan_video_images",
            sql="""
                SELECT i.id, i.owner_id
                FROM image_assets i LEFT JOIN videos v ON v.id = i.owner_id
                WHERE i.owner_type = 'video' AND v.id IS NULL
            """,
        ),
    ]

    allowed = ", ".join(f"'{value.replace(chr(39), chr(39) * 2)}'" for value in sorted(BUSINESS_CATEGORIES))
    issues.extend(
        [
            _issue(
                session,
                name="unsupported_product_categories",
                sql=f"SELECT id, category FROM products WHERE category NOT IN ({allowed})",
                blocking=False,
            ),
            _issue(
                session,
                name="legacy_coarse_product_categories",
                sql=(
                    "SELECT id, category FROM products WHERE category IN ("
                    + ", ".join(
                        f"'{value.replace(chr(39), chr(39) * 2)}'"
                        for value in sorted(LEGACY_COARSE_CATEGORIES)
                    )
                    + ")"
                ),
                blocking=False,
            ),
            _issue(
                session,
                name="pending_product_categories",
                sql=f"SELECT id, category FROM products WHERE category = '{PENDING_CATEGORY}'",
                blocking=False,
            ),
        ]
    )

    blockers = [asdict(item) for item in issues if item.blocking and item.count]
    warnings = [asdict(item) for item in issues if not item.blocking and item.count]
    if missing_canonical:
        blockers.append(
            asdict(
                ValidationIssue(
                    name="missing_canonical_products",
                    count=len(missing_canonical),
                    sample=missing_canonical[:20],
                    blocking=True,
                )
            )
        )
    if marker is None:
        blockers.append(
            asdict(
                ValidationIssue(
                    name="missing_data_migration_marker",
                    count=1,
                    sample=[MIGRATION_ID],
                    blocking=True,
                )
            )
        )

    counts = {
        table: session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
        for table in (
            "products",
            "reports",
            "videos",
            "video_facts",
            "bom_items",
            "bom_item_parameters",
            "image_assets",
        )
    }
    return {
        "passed": not blockers,
        "migration_id": MIGRATION_ID,
        "migration_status": marker.status if marker else "missing",
        "migration_source_commit": marker.source_commit if marker else "",
        "counts": counts,
        "blockers": blockers,
        "warnings": warnings,
        "checks": [asdict(item) for item in issues],
    }
