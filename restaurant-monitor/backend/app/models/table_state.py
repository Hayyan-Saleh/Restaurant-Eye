# جدول table_states
import enum
from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, Enum as SQLEnum, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base

class TableStateEnum(str, enum.Enum):
    FREE = "FREE"
    OCCUPIED = "OCCUPIED"
    DIRTY = "DIRTY"



class TableState(Base):
    __tablename__ = "table_states"

    zone_id: Mapped[str] = mapped_column(String(100), ForeignKey("zones.id", ondelete="CASCADE"), primary_key=True)
    camera_id: Mapped[str] = mapped_column(String(50), ForeignKey("cameras.id"), nullable=False)
    state: Mapped[TableStateEnum] = mapped_column(SQLEnum(TableStateEnum), default=TableStateEnum.FREE)
    state_started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())