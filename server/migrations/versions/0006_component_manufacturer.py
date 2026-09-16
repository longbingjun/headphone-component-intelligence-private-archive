"""Add auditable component manufacturer fields and repair battery taxonomy."""

from alembic import op
import sqlalchemy as sa

from core.component_analytics import manufacturer_from_evidence


revision = "0006_component_manufacturer"
down_revision = "0005_bom_analytics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {item["name"] for item in inspector.get_columns("bom_items")}
    columns = (
        sa.Column("manufacturer", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("manufacturer_basis", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("manufacturer_evidence_quote", sa.Text(), nullable=False, server_default=""),
    )
    for column in columns:
        if column.name not in existing:
            op.add_column("bom_items", column)

    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("bom_items")}
    if "ix_bom_items_manufacturer" not in indexes:
        op.create_index("ix_bom_items_manufacturer", "bom_items", ["manufacturer"])

    # Existing rows keep their report-provided component brand as an explicitly
    # labelled fallback.  The normal importer applies stricter source-text
    # extraction whenever a product is refreshed.
    op.execute(
        sa.text(
            "UPDATE bom_items SET manufacturer = brand, "
            "manufacturer_basis = 'reported_component_brand', "
            "manufacturer_evidence_quote = source_text "
            "WHERE manufacturer = '' AND brand <> ''"
        )
    )
    op.execute(
        sa.text(
            "UPDATE bom_items SET component_key = 'battery_protection_ic' "
            "WHERE component LIKE '%电池保护%' OR component LIKE '%锂电保护%'"
        )
    )
    rows = bind.execute(
        sa.text("SELECT id, component_key, brand, source_text FROM bom_items")
    ).mappings().all()
    updates = []
    for row in rows:
        manufacturer, basis, quote = manufacturer_from_evidence(
            row["source_text"], row["brand"], component_key=row["component_key"]
        )
        updates.append(
            {
                "row_id": row["id"],
                "manufacturer": manufacturer,
                "basis": basis,
                "quote": quote,
            }
        )
    if updates:
        bind.execute(
            sa.text(
                "UPDATE bom_items SET manufacturer = :manufacturer, "
                "manufacturer_basis = :basis, manufacturer_evidence_quote = :quote "
                "WHERE id = :row_id"
            ),
            updates,
        )


def downgrade() -> None:
    raise RuntimeError("Destructive automatic downgrade is disabled")
