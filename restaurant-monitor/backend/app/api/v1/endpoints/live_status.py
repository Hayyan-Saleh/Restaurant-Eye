from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.database import get_db
from app.core.redis import get_redis_client
from app.core.security import get_current_admin
from app.models.zone import Zone
from app.models.worker_state import WorkerState
from app.models.customer_session import CustomerSession, CustomerSessionStatus

router = APIRouter()


@router.get("/tables/status")
async def get_tables_status(
    redis_client=Depends(get_redis_client),
    db: AsyncSession = Depends(get_db),
    current_admin=Depends(get_current_admin),
):
    """Get real-time status of all tables from Redis."""
    keys = await redis_client.keys("table:*:status")

    zones_result = await db.execute(select(Zone))
    zone_id_to_camera = {zone.id: zone.camera_id for zone in zones_result.scalars().all()}

    tables = []
    for key in keys:
        zone_id = key.split(":", 2)[1]
        status = await redis_client.get(key)
        tables.append({
            "zone_id": zone_id,
            "camera_id": zone_id_to_camera.get(zone_id),
            "status": status,
        })

    return {"tables": tables}


@router.get("/workers/status")
async def get_workers_status(
    redis_client=Depends(get_redis_client),
    db: AsyncSession = Depends(get_db),
    current_admin=Depends(get_current_admin),
):
    """Get real-time status of all workers from Redis."""
    keys = await redis_client.keys("worker:*:status")

    worker_states_result = await db.execute(select(WorkerState))
    entity_id_to_zone = {ws.entity_id: ws.zone_id for ws in worker_states_result.scalars().all()}

    zones_result = await db.execute(select(Zone))
    zone_id_to_camera = {zone.id: zone.camera_id for zone in zones_result.scalars().all()}

    workers = []
    for key in keys:
        entity_id = key.split(":", 2)[1]
        status = await redis_client.get(key)
        zone_id = entity_id_to_zone.get(entity_id)
        workers.append({
            "entity_id": entity_id,
            "camera_id": zone_id_to_camera.get(zone_id) if zone_id else None,
            "zone_id": zone_id,
            "status": status,
        })

    return {"workers": workers}


@router.get("/zones-occupancy")
async def get_zones_occupancy(
    redis_client=Depends(get_redis_client),
    db: AsyncSession = Depends(get_db),
    current_admin=Depends(get_current_admin),
):
    """Get real-time occupancy count for all zones from Redis."""
    keys = await redis_client.keys("zone:*:count")

    zones_result = await db.execute(select(Zone))
    zone_id_to_name = {zone.id: zone.name for zone in zones_result.scalars().all()}

    occupancy = []
    for key in keys:
        parts = key.split(":")
        camera_id = parts[1]
        zone_id = parts[2]
        count = await redis_client.get(key)
        occupancy.append({
            "camera_id": camera_id,
            "zone_id": zone_id,
            "zone_name": zone_id_to_name.get(zone_id),
            "count": int(count) if count is not None else 0,
        })

    return {"zones": occupancy}


@router.get("/customer-sessions")
async def get_customer_sessions(
    status: CustomerSessionStatus | None = None,
    db: AsyncSession = Depends(get_db),
    current_admin=Depends(get_current_admin),
):
    """Get customer sessions with optional status filtering."""
    query = select(CustomerSession)
    if status is not None:
        query = query.where(CustomerSession.status == status)
    else:
        query = query.where(CustomerSession.status == CustomerSessionStatus.ACTIVE)

    result = await db.execute(query.order_by(CustomerSession.started_at.desc()))
    sessions = result.scalars().all()

    return {
        "customer_sessions": [
            {
                "id": session.id,
                "entity_id": session.entity_id,
                "table_zone_id": session.table_zone_id,
                "camera_id": session.camera_id,
                "started_at": session.started_at,
                "last_seen_at": session.last_seen_at,
                "left_at": session.left_at,
                "total_stay_sec": session.total_stay_sec,
                "status": session.status,
            }
            for session in sessions
        ]
    }