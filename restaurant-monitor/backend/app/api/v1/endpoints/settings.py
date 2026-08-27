# System Settings Management
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.database import get_db
from app.core.security import get_current_admin
from app.models.admin import Admin
from app.models.setting import SystemSetting
from app.schemas.setting import (
    SystemSettingResponse,
    SystemSettingUpdate,
    SystemSettingDetailResponse
)

router = APIRouter()


@router.get(
    "/",
    response_model=SystemSettingResponse,
    summary="Get current system settings",
    description="""
    Retrieve current system settings, including the worker idle limit.
    
    These settings control the monitoring behavior and time-based analyses in the system.
    """,
    responses={
        200: {"description": "Settings retrieved successfully"},
        404: {"description": "Settings not found"}
    }
)
async def get_system_settings(
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieve current system settings.
    """
    # 1. Retrieve settings from database
    stmt = select(SystemSetting).order_by(SystemSetting.id)
    result = await db.execute(stmt)
    settings = result.scalars().first()
    
    if not settings:
        # Create default settings if not exist
        settings = SystemSetting(worker_idle_limit=60)
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    
    return settings


@router.get(
    "/detail",
    response_model=SystemSettingDetailResponse,
    summary="Get detailed system settings",
    description="""
    Retrieve system settings with additional formatted information.
    
    Includes time values in different formats (seconds, minutes, hours) for easier reading.
    """,
    responses={
        200: {"description": "Detailed settings retrieved successfully"},
        404: {"description": "Settings not found"}
    }
)
async def get_system_settings_detail(
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieve detailed system settings with additional information.
    """
    # 1. Retrieve settings from database
    stmt = select(SystemSetting).order_by(SystemSetting.id)
    result = await db.execute(stmt)
    settings = result.scalars().first()
    
    if not settings:
        # Create default settings if not exist
        settings = SystemSetting(worker_idle_limit=60)
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    
    # 2. Convert to detailed response
    return SystemSettingDetailResponse.from_model(settings)


@router.put(
    "/",
    response_model=SystemSettingResponse,
    summary="Update system settings",
    description="""
    Update current system settings.
    
    Can modify the worker idle time limit.
    Value must be between 10 and 3600 seconds (10 seconds to 60 minutes).
    
    All fields are optional to support partial updates.
    """,
    responses={
        200: {"description": "Settings updated successfully"},
        400: {"description": "Invalid data"},
        404: {"description": "Settings not found"}
    }
)
async def update_system_settings(
    settings_data: SystemSettingUpdate,
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Update current system settings.
    """
    # 1. Retrieve current settings
    stmt = select(SystemSetting).order_by(SystemSetting.id)
    result = await db.execute(stmt)
    settings = result.scalars().first()
    
    if not settings:
        # Create new settings if not exist
        settings = SystemSetting(worker_idle_limit=settings_data.worker_idle_limit or 60)
        db.add(settings)
    else:
        # 2. Update only sent fields
        update_data = settings_data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(settings, field, value)
    
    await db.commit()
    await db.refresh(settings)
    
    return settings


@router.post(
    "/reset",
    response_model=SystemSettingResponse,
    summary="Reset settings to default values",
    description="""
    Reset all system settings to default values.
    
    This action will reset the idle limit to 60 seconds.
    """,
    responses={
        200: {"description": "Settings reset successfully"},
        404: {"description": "Settings not found"}
    }
)
async def reset_system_settings(
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Reset settings to default values.
    """
    # 1. Retrieve current settings
    stmt = select(SystemSetting).order_by(SystemSetting.id)
    result = await db.execute(stmt)
    settings = result.scalars().first()
    
    if not settings:
        # Create default settings
        settings = SystemSetting(worker_idle_limit=60)
        db.add(settings)
    else:
        # 2. Reset to default values
        settings.worker_idle_limit = 60
    
    await db.commit()
    await db.refresh(settings)
    
    return settings