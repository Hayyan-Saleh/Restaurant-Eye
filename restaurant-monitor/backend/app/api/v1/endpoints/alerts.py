from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.database import get_db
from app.core.security import get_current_admin
from app.models.alert import Alert, AlertStatusEnum

router = APIRouter()


@router.get("/")
async def get_alerts(
    status: AlertStatusEnum | None = None,
    db: AsyncSession = Depends(get_db),
    current_admin=Depends(get_current_admin),
):
    """Retrieve alerts with optional status filtering."""
    query = select(Alert)
    if status is not None:
        query = query.where(Alert.status == status)
    else:
        query = query.where(Alert.status == AlertStatusEnum.ACTIVE)

    result = await db.execute(query.order_by(Alert.created_at.desc()))
    alerts = result.scalars().all()

    return {
        "alerts": [
            {
                "id": alert.id,
                "alert_type": alert.alert_type,
                "entity_id": alert.entity_id,
                "camera_id": alert.camera_id,
                "zone_id": alert.zone_id,
                "message": alert.message,
                "status": alert.status,
                "created_at": alert.created_at,
                "resolved_at": alert.resolved_at,
            }
            for alert in alerts
        ]
    }


@router.post("/{alert_id}/resolve")
async def resolve_alert(
    alert_id: int,
    db: AsyncSession = Depends(get_db),
    current_admin=Depends(get_current_admin),
):
    """Mark an alert as resolved."""
    result = await db.execute(select(Alert).where(Alert.id == alert_id))
    alert = result.scalars().first()

    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")

    if alert.status == AlertStatusEnum.RESOLVED:
        raise HTTPException(status_code=400, detail="Alert already resolved")

    alert.status = AlertStatusEnum.RESOLVED
    alert.resolved_at = datetime.utcnow()
    await db.commit()
    await db.refresh(alert)

    return {
        "id": alert.id,
        "status": alert.status,
        "resolved_at": alert.resolved_at,
    }