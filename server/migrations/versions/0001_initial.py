"""Initial PostgreSQL schema for product intelligence data."""

from alembic import op

from server.models import Base


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep the first migration stable as new models are added later.
    initial_tables = [
        Base.metadata.tables[name]
        for name in (
            "products",
            "reports",
            "product_report_links",
            "bom_items",
            "image_assets",
            "crawl_runs",
        )
    ]
    Base.metadata.create_all(bind=op.get_bind(), tables=initial_tables)


def downgrade() -> None:
    raise RuntimeError("Destructive automatic downgrade is disabled")
