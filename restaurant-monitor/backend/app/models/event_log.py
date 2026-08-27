# جدول events_log
from datetime import datetime
from typing import Optional, Any
from sqlalchemy import String, BigInteger, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base

class EventLog(Base):
    __tablename__ = "events_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    role: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    camera_id: Mapped[Optional[str]] = mapped_column(String(50), ForeignKey("cameras.id"), nullable=True)
    zone_id: Mapped[Optional[str]] = mapped_column(String(100), ForeignKey("zones.id"), nullable=True)
    previous_state: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    new_state: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    event_timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())