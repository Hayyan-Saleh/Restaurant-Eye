# جدول system_settings
from datetime import datetime
from sqlalchemy import Integer, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base

class SystemSetting(Base):
    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    worker_idle_limit: Mapped[int] = mapped_column(Integer, default=60)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())