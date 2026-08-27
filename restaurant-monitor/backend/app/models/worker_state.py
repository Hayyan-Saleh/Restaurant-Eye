# جدول worker_states
import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import String, DateTime, ForeignKey, Enum as SQLEnum, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base

class WorkerRole(str, enum.Enum):
    WORKER = "WORKER"
    CUSTOMER = "CUSTOMER"
    UNKNOWN = "UNKNOWN"

class WorkerActivityStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    IDLE = "IDLE"    



class WorkerState(Base):
    __tablename__ = "worker_states"

    entity_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    role: Mapped[WorkerRole] = mapped_column(SQLEnum(WorkerRole), default=WorkerRole.WORKER)
    camera_id: Mapped[str] = mapped_column(String(50), ForeignKey("cameras.id"), nullable=False)
    zone_id: Mapped[Optional[str]] = mapped_column(String(100), ForeignKey("zones.id"), nullable=True)
    status: Mapped[WorkerActivityStatus] = mapped_column(SQLEnum(WorkerActivityStatus), default=WorkerActivityStatus.ACTIVE)
    status_started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())