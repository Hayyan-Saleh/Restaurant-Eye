# Create initial admin user script
# Run from project root: python -m backend.app.seed.create_admin
# Alternative: cd backend && python -m app.seed.create_admin

import sys
import os

# Ensure backend directory is in path
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(os.path.dirname(current_dir))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.core.database import AsyncSessionLocal
from app.core.security import hash_password
from app.models.admin import Admin
from app.core.config import settings
import asyncio
from sqlalchemy.future import select

async def create_admin():
    """Create initial admin user from environment configuration."""
    async with AsyncSessionLocal() as db:
        try:
            email = settings.INITIAL_ADMIN_EMAIL
            password = settings.INITIAL_ADMIN_PASSWORD

            print(f"Creating admin with email: {email}")

            stmt = select(Admin).where(Admin.email == email)
            result = await db.execute(stmt)
            existing_admin = result.scalars().first()

            if existing_admin:
                print("Admin already exists.")
                return

            admin = Admin(
                email=email,
                password_hash=hash_password(password),
            )

            db.add(admin)
            await db.commit()

            print("Admin created successfully.")
            print(f"Email: {email}")
            print(f"Password: {password}")
            print("Please change the password after first login.")

        except Exception as e:
            await db.rollback()
            print(f"Error creating admin: {e}")
            print("Make sure .env file is configured correctly.")

if __name__ == "__main__":
    asyncio.run(create_admin())