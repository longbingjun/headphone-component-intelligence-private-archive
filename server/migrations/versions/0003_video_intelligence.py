"""Add the subtitle/OCR video intelligence queue and evidence facts."""

from alembic import op
import sqlalchemy as sa

from server.models import Base


revision = "0003_video_intelligence"
down_revision = "0002_video_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Migration 0002 historically created its tables from Base.metadata.  Fresh
    # installations therefore see current columns already, while upgraded
    # installations do not.  Inspect first so both paths are safe.
    inspector = sa.inspect(bind)
    existing_columns = {item["name"] for item in inspector.get_columns("videos")}
    columns = [
        sa.Column("processing_status", sa.String(length=32), nullable=False, server_default="discovered"),
        sa.Column("processing_attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processing_last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("candidate_product_id", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("matched_product_id", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("match_confidence", sa.Float(), nullable=True),
        sa.Column("match_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("subtitle_method", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("intelligence_published_at", sa.DateTime(timezone=True), nullable=True),
    ]
    for column in columns:
        if column.name not in existing_columns:
            op.add_column("videos", column)

    existing_indexes = {item["name"] for item in sa.inspect(bind).get_indexes("videos")}
    for name, columns in (
        ("ix_videos_processing_status", ["processing_status"]),
        ("ix_videos_candidate_product_id", ["candidate_product_id"]),
        ("ix_videos_matched_product_id", ["matched_product_id"]),
    ):
        if name not in existing_indexes:
            op.create_index(name, "videos", columns)

    Base.metadata.tables["video_facts"].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    raise RuntimeError("Destructive automatic downgrade is disabled")
