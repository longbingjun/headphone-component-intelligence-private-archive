"""Make reviewed video facts first-class, auditable BOM evidence."""

from alembic import op
import sqlalchemy as sa


revision = "0007_video_bom_source"
down_revision = "0006_component_manufacturer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {item["name"] for item in inspector.get_columns("bom_items")}
    columns = (
        sa.Column(
            "source_type",
            sa.String(length=32),
            nullable=False,
            server_default="report",
        ),
        sa.Column(
            "source_video_id",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "source_video_fact_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            nullable=True,
        ),
    )
    for column in columns:
        if column.name not in existing:
            op.add_column("bom_items", column)

    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("bom_items")}
    for name, fields in (
        ("ix_bom_items_source_type", ["source_type"]),
        ("ix_bom_items_source_video_id", ["source_video_id"]),
        ("ix_bom_items_source_video_fact_id", ["source_video_fact_id"]),
    ):
        if name not in indexes:
            op.create_index(name, "bom_items", fields)

    bind.execute(
        sa.text(
            "UPDATE bom_items SET source_type = 'report' "
            "WHERE source_type IS NULL OR source_type = ''"
        )
    )


def downgrade() -> None:
    raise RuntimeError("Destructive automatic downgrade is disabled")
