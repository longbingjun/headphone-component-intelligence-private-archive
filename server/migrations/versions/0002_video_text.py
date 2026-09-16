"""Persist video metadata, subtitle/OCR text and product links."""

from alembic import op

from server.models import Base


revision = "0002_video_text"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for name in ("videos", "video_transcripts", "product_video_links"):
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    raise RuntimeError("Destructive automatic downgrade is disabled")
