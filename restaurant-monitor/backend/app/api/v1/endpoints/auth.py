from datetime import datetime, timedelta, timezone
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.database import get_db
from app.models.admin import Admin
from app.schemas.auth import (
    LoginRequest,
    TokenResponse,
    OTPRequest,
    PasswordResetRequest,
    MessageResponse,
)
from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    decode_token,
    get_token_from_header,
    get_current_admin,
)
from app.services.email_service import send_otp_email
from app.core.redis import redis_client
from app.core.config import settings

# Define router with custom Tag to appear organized in Swagger UI
router = APIRouter()


# --------------------------------------------------------------------------
# 1. Login Endpoint
# --------------------------------------------------------------------------
@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Admin Login",
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "description": "Successfully authenticated and JWT token generated.",
            "content": {
                "application/json": {
                    "example": {"access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."}
                }
            },
        },
        401: {
            "description": "Invalid email or password credentials.",
            "content": {
                "application/json": {
                    "example": {"detail": "Invalid email or password"}
                }
            },
        },
        422: {
            "description": "Validation error - missing or invalid fields.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": [
                            {
                                "loc": ["body", "email"],
                                "msg": "field required",
                                "type": "value_error.missing"
                            }
                        ]
                    }
                }
            },
        },
    },
)
async def login(
    data: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Authenticates an administrator and generates a Bearer JWT access token.

    ### 🔄 Workflow:
    1. Checks if the provided email exists in the system.
    2. Verifies the password hash against the stored database value.
    3. Generates and returns a signed JWT access token.

    ### ⚠️ Possible Errors:
    * **401 Unauthorized**: Returned if email does not exist or password verification fails.
    """
    stmt = select(Admin).where(Admin.email == data.email)
    result = await db.execute(stmt)
    admin = result.scalars().first()

    if not admin:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not verify_password(data.password, admin.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token = create_access_token(str(admin.id))

    return TokenResponse(access_token=token)


# --------------------------------------------------------------------------
# 2. Get Current Admin Profile Endpoint
# --------------------------------------------------------------------------
@router.get(
    "/me",
    summary="Get Current Admin Profile",
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "description": "Profile information retrieved successfully.",
            "content": {
                "application/json": {
                    "example": {
                        "id": "f81d4fae-7dec-11d0-a765-00a0c91e6bf6",
                        "email": "admin@restaurant.com"
                    }
                }
            },
        },
        401: {
            "description": "Unauthorized or missing/invalid Bearer token.",
            "content": {
                "application/json": {
                    "example": {"detail": "Could not validate credentials"}
                }
            },
        },
    },
)
def get_me(
    current_admin: Admin = Depends(get_current_admin),
):
    """
    Retrieves the identity details of the currently authenticated administrator.

    ### 🔑 Requirements:
    * Requires a valid **Bearer JWT Token** in the `Authorization` header.

    ### 📤 Returns:
    * `id`: Admin UUID v4.
    * `email`: Admin registered email address.
    """
    return {
        "id": str(current_admin.id),
        "email": current_admin.email,
    }


# --------------------------------------------------------------------------
# 3. Request OTP Endpoint
# --------------------------------------------------------------------------
@router.post(
    "/password/request-otp",
    response_model=MessageResponse,
    summary="Request Password Reset OTP",
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "description": "OTP generated and email sent successfully.",
            "content": {
                "application/json": {
                    "example": {"message": "OTP code has been generated and sent successfully."}
                }
            },
        },
        404: {
            "description": "Email address not found in the system.",
            "content": {
                "application/json": {
                    "example": {"detail": "Email address not found"}
                }
            },
        },
        422: {
            "description": "Validation error - missing or invalid fields.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": [
                            {
                                "loc": ["body", "email"],
                                "msg": "field required",
                                "type": "value_error.missing"
                            }
                        ]
                    }
                }
            },
        },
    },
)
async def request_password_otp(
    data: OTPRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Generates a 6-digit One-Time Password (OTP) for password reset.

    ### 🔄 Workflow:
    1. Validates admin existence by email.
    2. Generates a secure random 6-digit numeric OTP.
    3. Saves OTP and sets expiration time (**10 minutes** from generation).
    4. Dispatches the OTP to the admin via email.

    ### ⚠️ Possible Errors:
    * **404 Not Found**: Given email address is not registered.
    """
    stmt = select(Admin).where(Admin.email == data.email)
    result = await db.execute(stmt)
    admin = result.scalars().first()

    if not admin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Email address not found",
        )

    otp = f"{secrets.randbelow(1_000_000):06d}"

    admin.otp_code_hash = otp
    admin.otp_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=10)

    await db.commit()

    try:
        send_otp_email(
            recipient=admin.email,
            otp=otp,
        )
    except Exception as e:
        print(f"Warning: Could not send email. Error: {e}")

    # In development mode, return OTP in response for testing
    if settings.DEV_MODE:
        return MessageResponse(
            message=f"OTP code has been generated and sent successfully. DEV MODE: OTP is {otp}"
        )

    return MessageResponse(
        message="OTP code has been generated and sent successfully."
    )


# --------------------------------------------------------------------------
# 4. Reset Password Endpoint
# --------------------------------------------------------------------------
@router.post(
    "/password/reset",
    response_model=MessageResponse,
    summary="Reset Password via OTP",
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "description": "Password changed successfully.",
            "content": {
                "application/json": {
                    "example": {"message": "Password changed successfully"}
                }
            },
        },
        400: {
            "description": "Invalid request, wrong OTP, or expired OTP.",
            "content": {
                "application/json": {
                    "example": {"detail": "OTP expired"}
                }
            },
        },
        422: {
            "description": "Validation error - missing or invalid fields.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": [
                            {
                                "loc": ["body", "email"],
                                "msg": "field required",
                                "type": "value_error.missing"
                            }
                        ]
                    }
                }
            },
        },
    },
)
async def reset_password(
    data: PasswordResetRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Resets administrator password using the sent OTP code.

    ### 🔄 Workflow:
    1. Validates admin existence and verifies active OTP session.
    2. Checks if OTP has expired.
    3. Matches provided OTP with stored value.
    4. Hashes the new password and updates the database.
    5. Clears used OTP fields upon success.

    ### ⚠️ Possible Errors:
    * **400 Bad Request**: Invalid email, missing OTP, wrong OTP, or expired OTP.
    """
    stmt = select(Admin).where(Admin.email == data.email)
    result = await db.execute(stmt)
    admin = result.scalars().first()

    if not admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OTP or email",
        )

    if not admin.otp_code_hash or not admin.otp_expires_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OTP is not valid or expired",
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if admin.otp_expires_at < now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OTP expired",
        )

    if admin.otp_code_hash != data.otp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OTP",
        )

    admin.password_hash = hash_password(data.new_password)
    admin.otp_code_hash = None
    admin.otp_expires_at = None

    await db.commit()

    return MessageResponse(
        message="Password changed successfully"
    )


# --------------------------------------------------------------------------
# 5. Logout Endpoint
# --------------------------------------------------------------------------
@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Logout Admin",
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "description": "Logged out successfully and token revoked.",
            "content": {
                "application/json": {
                    "example": {"message": "Logged out successfully"}
                }
            },
        },
        401: {
            "description": "Invalid or expired JWT token.",
            "content": {
                "application/json": {
                    "example": {"detail": "Invalid token"}
                }
            },
        },
        422: {
            "description": "Invalid authorization header format.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": [
                            {
                                "loc": ["header", "authorization"],
                                "msg": "Invalid authorization header",
                                "type": "value_error"
                            }
                        ]
                    }
                }
            },
        },
    },
)
async def logout(
    token: str = Depends(get_token_from_header),
):
    """
    Revokes the active JWT access token via Redis blacklisting.

    ### 🔄 Workflow:
    1. Extracts `jti` (JWT Unique ID) and `exp` (Expiration timestamp) from token payload.
    2. Calculates remaining TTL (Time-To-Live).
    3. Blacklists the token `jti` in Redis until expiration.

    ### 🔑 Requirements:
    * Requires a valid **Bearer JWT Token** in the `Authorization` header.
    """
    payload = decode_token(token)

    jti = payload.get("jti")
    exp = payload.get("exp")

    if not jti or not exp:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    now = int(datetime.now(timezone.utc).timestamp())
    ttl = max(exp - now, 1)

    await redis_client.setex(
        f"revoked_token:{jti}",
        ttl,
        "1",
    )

    return MessageResponse(
        message="Logged out successfully"
    )