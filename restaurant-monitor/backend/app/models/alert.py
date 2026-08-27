# جدول alerts
import enum
from datetime import datetime
from typing import Optional, Any
from sqlalchemy import String, BigInteger, Text, DateTime, ForeignKey, Enum as SQLEnum, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base

class AlertStatusEnum(str, enum.Enum):
    ACTIVE = "ACTIVE"
    RESOLVED = "RESOLVED"

class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    alert_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    camera_id: Mapped[Optional[str]] = mapped_column(String(50), ForeignKey("cameras.id"), nullable=True)
    zone_id: Mapped[Optional[str]] = mapped_column(String(100), ForeignKey("zones.id"), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[AlertStatusEnum] = mapped_column(SQLEnum(AlertStatusEnum), default=AlertStatusEnum.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)