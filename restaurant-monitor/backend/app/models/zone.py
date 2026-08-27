# جدول zones
from datetime import datetime
from typing import Optional, Any
from sqlalchemy import String, Boolean, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base

class Zone(Base):
    __tablename__ = "zones"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    camera_id: Mapped[str] = mapped_column(String(50), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False)
    parent_zone_id: Mapped[Optional[str]] = mapped_column(String(100), ForeignKey("zones.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    zone_type: Mapped[str] = mapped_column(String(50), nullable=False)
    original_zone_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    polygon_coordinates: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    auto_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    excludes_tables: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    camera = relationship("Camera", back_populates="zones")