"""Add durable leasing, retry and static-site publication state."""

from alembic import op
import sqlalchemy as sa


revision = "0004_video_worker"
down_revision = "0003_video_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {item["name"] for item in inspector.get_columns("videos")}
    columns = [
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "site_publish_status",
            sa.String(length=32),
            nullable=False,
            server_default="not_required",
        ),
        sa.Column("site_publish_last_error", sa.Text(), nullable=False, server_default=""),
    ]
    for column in columns:
        if column.name not in existing:
            op.add_column("videos", column)

    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("videos")}
    for name, fields in (
        ("ix_videos_processing_next_attempt_at", ["processing_next_attempt_at"]),
        ("ix_videos_site_publish_status", ["site_publish_status"]),
    ):
        if name not in indexes:
            op.create_index(name, "videos", fields)


def downgrade() -> None:
    raise RuntimeError("Destructive automatic downgrade is disabled")
