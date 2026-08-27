# جدول customer_sessions
import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, BigInteger, DateTime, ForeignKey, Enum as SQLEnum, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class CustomerSessionStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"


class CustomerSession(Base):
    __tablename__ = "customer_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    table_zone_id: Mapped[str] = mapped_column(String(100), ForeignKey("zones.id"), nullable=False)
    camera_id: Mapped[str] = mapped_column(String(50), ForeignKey("cameras.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    left_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    total_stay_sec: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[CustomerSessionStatus] = mapped_column(SQLEnum(CustomerSessionStatus), default=CustomerSessionStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())