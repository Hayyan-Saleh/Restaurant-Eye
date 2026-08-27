# جدول cameras
import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, DateTime, Enum as SQLEnum, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base

class CameraStatus(str, enum.Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    rtsp_url: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[CameraStatus] = mapped_column(SQLEnum(CameraStatus), default=CameraStatus.OFFLINE)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    frame_width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    frame_height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    zones = relationship("Zone", back_populates="camera", cascade="all, delete-orphan")