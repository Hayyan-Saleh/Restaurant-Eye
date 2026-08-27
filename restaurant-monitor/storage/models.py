"""
storage/models.py
------------------
SQLAlchemy 2.0 async ORM models. Schema design/rationale: docs/DB_SCHEMA.md.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    SmallInteger,
    String,
    Time,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import TypeDecorator, TIMESTAMP


class Base(DeclarativeBase):
    pass


class TZDateTime(TypeDecorator):
    """Stores tz-aware datetimes as timestamptz."""

    impl = TIMESTAMP(timezone=True)
    cache_ok = True


class Camera(Base):
    __tablename__ = "cameras"

    camera_id: Mapped[str] = mapped_column(String, primary_key=True)
    location: Mapped[str | None] = mapped_column(Text)
    camera_type: Mapped[str | None] = mapped_column(String)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    operating_hours_start: Mapped[dt.time | None] = mapped_column(Time)
    operating_hours_end: Mapped[dt.time | None] = mapped_column(Time)

    zones: Mapped[list["Zone"]] = relationship(back_populates="camera")


class Zone(Base):
    __tablename__ = "zones"

    zone_id: Mapped[str] = mapped_column(String, primary_key=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.camera_id"), index=True)
    zone_type: Mapped[str] = mapped_column(String)
    zone_type_normalized: Mapped[str] = mapped_column(String)
    zone_name: Mapped[str | None] = mapped_column(Text)
    parent_zone_id: Mapped[str | None] = mapped_column(ForeignKey("zones.zone_id"))
    polygon: Mapped[list] = mapped_column(JSON)
    max_capacity: Mapped[int | None] = mapped_column(Integer)

    camera: Mapped["Camera"] = relationship(back_populates="zones")


class Identity(Base):
    __tablename__ = "identities"

    global_id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String)  # worker | customer_session
    display_name: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[dt.datetime] = mapped_column(
        TZDateTime, server_default=func.now()
    )
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        TZDateTime, server_default=func.now(), onupdate=func.now()
    )


class WorkerGalleryEntry(Base):
    __tablename__ = "worker_gallery"

    global_id: Mapped[str] = mapped_column(
        ForeignKey("identities.global_id"), primary_key=True
    )
    # float4[512] fallback representation (portable across Postgres without
    # the pgvector extension). If pgvector is available, database.py swaps
    # this column's DDL for `vector(512)` at init time — see docs/DB_SCHEMA.md.
    embedding: Mapped[list[float]] = mapped_column(JSON)
    enrolled_via: Mapped[str] = mapped_column(String, default="auto_promotion")
    enrolled_at: Mapped[dt.datetime] = mapped_column(
        TZDateTime, server_default=func.now()
    )


class Track(Base):
    __tablename__ = "tracks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.camera_id"), index=True)
    track_id: Mapped[int] = mapped_column(Integer)
    global_id: Mapped[str | None] = mapped_column(ForeignKey("identities.global_id"))
    started_at: Mapped[dt.datetime] = mapped_column(TZDateTime)
    ended_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(TZDateTime, index=True)
    level: Mapped[int] = mapped_column(SmallInteger)
    event_type: Mapped[str] = mapped_column(String, index=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.camera_id"))
    zone_id: Mapped[str | None] = mapped_column(ForeignKey("zones.zone_id"))
    global_id: Mapped[str | None] = mapped_column(ForeignKey("identities.global_id"))
    track_id: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

    __table_args__ = (
        Index("ix_events_camera_ts", "camera_id", "ts"),
        Index("ix_events_zone_ts", "zone_id", "ts"),
        Index("ix_events_global_ts", "global_id", "ts"),
    )


class KPISnapshot(Base):
    __tablename__ = "kpi_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    computed_at: Mapped[dt.datetime] = mapped_column(
        TZDateTime, server_default=func.now()
    )
    window_start: Mapped[dt.datetime] = mapped_column(TZDateTime)
    window_end: Mapped[dt.datetime] = mapped_column(TZDateTime)
    kpi_name: Mapped[str] = mapped_column(String, index=True)
    camera_id: Mapped[str | None] = mapped_column(ForeignKey("cameras.camera_id"))
    zone_id: Mapped[str | None] = mapped_column(ForeignKey("zones.zone_id"))
    global_id: Mapped[str | None] = mapped_column(ForeignKey("identities.global_id"))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String)

    __table_args__ = (
        Index("ix_kpi_name_window", "kpi_name", "window_start", "window_end"),
    )
