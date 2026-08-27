import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# 1. إعداد مسارات Python للتأكد من التعرف على مجلد app أو backend/app
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
backend_path = os.path.join(BASE_DIR, "backend")
sys.path.insert(0, backend_path)
# إزالة BASE_DIR من sys.path لتجنب تعارض مع مجلد models الرئيسي
if BASE_DIR in sys.path:
    sys.path.remove(BASE_DIR)

# 2. استيراد الإعدادات و Base وكافة الموديلز
from app.core.config import settings
from app.core.database import Base
import app.models  # يضمن تسجيل كافة الجداول تلقائياً في Base.metadata

# 3. إعداد كائن Alembic Config
config = context.config

# إعداد الـ Logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 4. ربط MetaData وقراءة رابط قاعدة البيانات من ملف الـ .env تلقائياً
target_metadata = Base.metadata
config.set_main_option("sqlalchemy.url", settings.SYNC_DATABASE_URL)


def run_migrations_offline() -> None:
    """تشغيل الهجرة في وضع Offline"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """تشغيل الهجرة في وضع Online"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()