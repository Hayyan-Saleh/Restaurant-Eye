from datetime import datetime, timedelta, timezone
import uuid

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pwdlib import PasswordHash
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.config import settings
from app.core.database import get_db
from app.models.admin import Admin
from app.core.redis import redis_client


# 1️⃣ Password hashing
password_hash = PasswordHash.recommended()

# 2️⃣ Bearer Token protection system for Swagger UI (direct token paste box)
security_scheme = HTTPBearer()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return password_hash.verify(plain_password, hashed_password)


def create_access_token(admin_id: str) -> str:
    now = datetime.now(timezone.utc)

    payload = {
        "sub": str(admin_id),
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        ),
        "type": "access",
    }

    return jwt.encode(
        payload,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )

        if payload.get("type") != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type",
            )

        if not payload.get("sub"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )

        return payload

    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )


# 🟢 3️⃣ Function to extract Token from header (with Swagger)
async def get_token_from_header(
    auth: HTTPAuthorizationCredentials = Depends(security_scheme),
) -> str:
    return auth.credentials


# 🟢 4️⃣ Function to get current admin (fully Async)
async def get_current_admin(
    token: str = Depends(get_token_from_header),
    db: AsyncSession = Depends(get_db), # 👈 Async session
) -> Admin:

    payload = decode_token(token)
    jti = payload.get("jti")

    # 🟢 Check revoked token in Redis using await
    if jti and await redis_client.exists(f"revoked_token:{jti}"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
        )

    admin_id_str = payload["sub"]

    # Convert admin ID to UUID type for PostgreSQL compatibility
    try:
        admin_id_uuid = uuid.UUID(admin_id_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin ID format",
        )

    # 🟢 Database query in Async Select format
    stmt = select(Admin).where(Admin.id == admin_id_uuid)
    result = await db.execute(stmt)
    admin = result.scalars().first()

    if not admin:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin not found",
        )

    return admin