"""Track application-level data migrations independently from crawler runs."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0008_data_migration_history"
down_revision = "0007_video_bom_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "data_migrations" not in inspector.get_table_names():
        op.create_table(
            "data_migrations",
            sa.Column("id", sa.String(length=100), primary_key=True),
            sa.Column("source_commit", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="applied"),
            sa.Column(
                "details",
                sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
                nullable=False,
            ),
            sa.Column(
                "applied_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index("ix_data_migrations_status", "data_migrations", ["status"])


def downgrade() -> None:
    raise RuntimeError("Destructive automatic downgrade is disabled")
