"""Add analysis-ready BOM taxonomy, materials, parameters and evidence."""

from alembic import op
import sqlalchemy as sa


revision = "0005_bom_analytics"
down_revision = "0004_video_worker"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {item["name"] for item in inspector.get_columns("bom_items")}
    columns = (
        sa.Column("component_key", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("material", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_report_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("source_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("confidence", sa.Float(), nullable=True),
    )
    for column in columns:
        if column.name not in existing:
            op.add_column("bom_items", column)

    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("bom_items")}
    for name, fields in (
        ("ix_bom_items_component_key", ["component_key"]),
        ("ix_bom_items_source_report_id", ["source_report_id"]),
        ("ix_bom_component_key_role", ["component_key", "role"]),
    ):
        if name not in indexes:
            op.create_index(name, "bom_items", fields)

    if "bom_item_parameters" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "bom_item_parameters",
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
            sa.Column(
                "bom_item_id",
                sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                sa.ForeignKey("bom_items.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("label", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("value_text", sa.Text(), nullable=False, server_default=""),
            sa.Column("value_numeric", sa.Float(), nullable=True),
            sa.Column("unit", sa.String(length=32), nullable=False, server_default=""),
            sa.Column("evidence_quote", sa.Text(), nullable=False, server_default=""),
        )
        op.create_index("ix_bom_item_parameters_bom_item_id", "bom_item_parameters", ["bom_item_id"])
        op.create_index("ix_bom_parameter_label", "bom_item_parameters", ["label"])


def downgrade() -> None:
    raise RuntimeError("Destructive automatic downgrade is disabled")
