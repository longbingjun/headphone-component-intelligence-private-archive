from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


JsonType = JSON().with_variant(JSONB, "postgresql")
PrimaryKeyType = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    pass


product_report_links = Table(
    "product_report_links",
    Base.metadata,
    Column("product_id", String(255), ForeignKey("products.id", ondelete="CASCADE"), primary_key=True),
    Column("report_id", String(64), ForeignKey("reports.id", ondelete="CASCADE"), primary_key=True),
)

product_video_links = Table(
    "product_video_links",
    Base.metadata,
    Column("product_id", String(255), ForeignKey("products.id", ondelete="CASCADE"), primary_key=True),
    Column("video_id", String(64), ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True),
)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    brand: Mapped[str] = mapped_column(String(255), default="", index=True)
    model: Mapped[str] = mapped_column(String(500), default="", index=True)
    category: Mapped[str] = mapped_column(String(100), default="", index=True)
    first_seen: Mapped[date | None] = mapped_column(Date)
    latest_published: Mapped[date | None] = mapped_column(Date, index=True)
    launch_date: Mapped[date | None] = mapped_column(Date)
    price_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    price_currency: Mapped[str] = mapped_column(String(12), default="CNY")
    data_completeness: Mapped[float | None] = mapped_column(Float)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    reports: Mapped[list["Report"]] = relationship(
        secondary=product_report_links, back_populates="products"
    )
    videos: Mapped[list["Video"]] = relationship(
        secondary=product_video_links, back_populates="products"
    )
    bom_items: Mapped[list["BomItem"]] = relationship(
        back_populates="product", cascade="all, delete-orphan", order_by="BomItem.ordinal"
    )


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(255), default="")
    published_at: Mapped[date | None] = mapped_column(Date, index=True)
    brand: Mapped[str] = mapped_column(String(255), default="", index=True)
    model: Mapped[str] = mapped_column(String(500), default="")
    category: Mapped[str] = mapped_column(String(100), default="", index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    products: Mapped[list[Product]] = relationship(
        secondary=product_report_links, back_populates="reports"
    )


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), default="audio52", index=True)
    source_url: Mapped[str] = mapped_column(Text, default="")
    embed_url: Mapped[str] = mapped_column(Text, default="")
    source_site: Mapped[str] = mapped_column(String(255), default="")
    title: Mapped[str] = mapped_column(Text, default="")
    publisher: Mapped[str] = mapped_column(String(255), default="")
    published_at: Mapped[date | None] = mapped_column(Date, index=True)
    brand: Mapped[str] = mapped_column(String(255), default="", index=True)
    model: Mapped[str] = mapped_column(String(500), default="")
    category: Mapped[str] = mapped_column(String(100), default="", index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    # Generic video-intelligence lifecycle for creator subtitles or frame OCR.
    processing_status: Mapped[str] = mapped_column(
        String(32), default="discovered", index=True
    )
    processing_attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    processing_last_error: Mapped[str] = mapped_column(Text, default="")
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    candidate_product_id: Mapped[str] = mapped_column(String(255), default="", index=True)
    matched_product_id: Mapped[str] = mapped_column(String(255), default="", index=True)
    match_confidence: Mapped[float | None] = mapped_column(Float)
    match_reason: Mapped[str] = mapped_column(Text, default="")
    subtitle_method: Mapped[str] = mapped_column(String(64), default="")
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    intelligence_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    site_publish_status: Mapped[str] = mapped_column(
        String(32), default="not_required", index=True
    )
    site_publish_last_error: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    products: Mapped[list[Product]] = relationship(
        secondary=product_video_links, back_populates="videos"
    )
    transcript: Mapped["VideoTranscript | None"] = relationship(
        back_populates="video", cascade="all, delete-orphan", uselist=False
    )
    facts: Mapped[list["VideoFact"]] = relationship(
        back_populates="video", cascade="all, delete-orphan", order_by="VideoFact.start_seconds"
    )


class VideoTranscript(Base):
    __tablename__ = "video_transcripts"

    id: Mapped[int] = mapped_column(PrimaryKeyType, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("videos.id", ondelete="CASCADE"), unique=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    method: Mapped[str] = mapped_column(String(100), default="")
    model_name: Mapped[str] = mapped_column(String(100), default="")
    language: Mapped[str] = mapped_column(String(32), default="zh")
    transcript: Mapped[str] = mapped_column(Text, default="")
    transcript_chars: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str] = mapped_column(Text, default="")
    segments: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, default=list)
    source_checksum: Mapped[str] = mapped_column(String(64), default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    video: Mapped[Video] = relationship(back_populates="transcript")


class VideoFact(Base):
    """Auditable text fact and its optional representative video frame."""

    __tablename__ = "video_facts"
    __table_args__ = (
        UniqueConstraint("video_id", "segment_id", "fact_text", name="uq_video_fact_source"),
        Index("ix_video_fact_product_status", "product_id", "status"),
    )

    id: Mapped[int] = mapped_column(PrimaryKeyType, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("videos.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    segment_id: Mapped[int] = mapped_column(Integer)
    start_seconds: Mapped[float] = mapped_column(Float, default=0)
    end_seconds: Mapped[float] = mapped_column(Float, default=0)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    corrected_text: Mapped[str] = mapped_column(Text, default="")
    fact_text: Mapped[str] = mapped_column(Text, default="")
    importance: Mapped[int] = mapped_column(Integer, default=3)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    selection_reason: Mapped[str] = mapped_column(Text, default="")
    keyframe_time_seconds: Mapped[float | None] = mapped_column(Float)
    keyframe_object_key: Mapped[str] = mapped_column(String(500), default="")
    keyframe_sha256: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="needs_review", index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    video: Mapped[Video] = relationship(back_populates="facts")


class BomItem(Base):
    __tablename__ = "bom_items"
    __table_args__ = (
        Index("ix_bom_product_component", "product_id", "component"),
        Index("ix_bom_component_key_role", "component_key", "role"),
    )

    id: Mapped[int] = mapped_column(PrimaryKeyType, primary_key=True, autoincrement=True)
    product_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    side: Mapped[str] = mapped_column(String(100), default="")
    component: Mapped[str] = mapped_column(String(255), default="", index=True)
    component_key: Mapped[str] = mapped_column(String(100), default="", index=True)
    brand: Mapped[str] = mapped_column(String(255), default="", index=True)
    manufacturer: Mapped[str] = mapped_column(String(255), default="", index=True)
    manufacturer_basis: Mapped[str] = mapped_column(String(64), default="")
    manufacturer_evidence_quote: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(Text, default="")
    role: Mapped[str] = mapped_column(String(100), default="")
    quantity_hint: Mapped[str] = mapped_column(String(100), default="")
    material: Mapped[str] = mapped_column(Text, default="")
    source_type: Mapped[str] = mapped_column(String(32), default="report", index=True)
    source_report_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    # Deliberately not a database FK: the stable 0001 migration creates
    # bom_items before the later video tables. Application-level restoration
    # and tests enforce the provenance link without breaking fresh PostgreSQL.
    source_video_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_video_fact_id: Mapped[int | None] = mapped_column(
        PrimaryKeyType,
        nullable=True,
        index=True,
    )
    source_text: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float | None] = mapped_column(Float)
    evidence: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)

    product: Mapped[Product] = relationship(back_populates="bom_items")
    parameters: Mapped[list["BomItemParameter"]] = relationship(
        back_populates="bom_item",
        cascade="all, delete-orphan",
        order_by="BomItemParameter.ordinal",
    )


class BomItemParameter(Base):
    __tablename__ = "bom_item_parameters"
    __table_args__ = (Index("ix_bom_parameter_label", "label"),)

    id: Mapped[int] = mapped_column(PrimaryKeyType, primary_key=True, autoincrement=True)
    bom_item_id: Mapped[int] = mapped_column(
        PrimaryKeyType, ForeignKey("bom_items.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    label: Mapped[str] = mapped_column(String(255), default="")
    value_text: Mapped[str] = mapped_column(Text, default="")
    value_numeric: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(32), default="")
    evidence_quote: Mapped[str] = mapped_column(Text, default="")

    bom_item: Mapped[BomItem] = relationship(back_populates="parameters")


class ImageAsset(Base):
    __tablename__ = "image_assets"
    __table_args__ = (
        UniqueConstraint("owner_type", "owner_id", "source_url", name="uq_image_owner_source"),
        Index("ix_image_owner", "owner_type", "owner_id"),
    )

    id: Mapped[int] = mapped_column(PrimaryKeyType, primary_key=True, autoincrement=True)
    owner_type: Mapped[str] = mapped_column(String(32))
    owner_id: Mapped[str] = mapped_column(String(255))
    image_kind: Mapped[str] = mapped_column(String(100), default="other")
    source_url: Mapped[str] = mapped_column(Text)
    # One optimized object may be referenced by multiple products/reports.
    object_key: Mapped[str] = mapped_column(String(500), index=True)
    original_name: Mapped[str] = mapped_column(String(500), default="")
    caption: Mapped[str] = mapped_column(Text, default="")
    alt_text: Mapped[str] = mapped_column(Text, default="")
    mime_type: Mapped[str] = mapped_column(String(100), default="image/webp")
    byte_size: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    storage_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CrawlRun(Base):
    __tablename__ = "crawl_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    trigger: Mapped[str] = mapped_column(String(32), default="schedule")
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=1)
    new_reports: Mapped[int] = mapped_column(Integer, default=0)
    new_videos: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    details: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    error_message: Mapped[str] = mapped_column(Text, default="")


class DataMigration(Base):
    """One durable marker for each application-level production data migration."""

    __tablename__ = "data_migrations"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    source_commit: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="applied", index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
