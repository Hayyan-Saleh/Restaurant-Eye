from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select

from app.core.database import get_db
from app.core.redis import get_redis_client
from app.core.security import get_current_admin
from app.models.customer_session import CustomerSession, CustomerSessionStatus
from datetime import datetime, timedelta, timezone
from typing import Literal
from fastapi import Query
from sqlalchemy import cast, Float
from app.models.alert import Alert
from app.models.event_log import EventLog
# (CustomerSession, CustomerSessionStatus مستوردين أصلاً بأعلى الملف)

from fastapi import Query
from sqlalchemy import cast, Float

from app.models.alert import Alert
from app.models.event_log import EventLog
# (CustomerSession, CustomerSessionStatus مستوردين أصلاً بأعلى الملف)


router = APIRouter()


@router.get("/realtime")
async def get_realtime_kpis(
    redis_client=Depends(get_redis_client),
    db: AsyncSession = Depends(get_db),
    current_admin=Depends(get_current_admin),
):
    table_keys = await redis_client.keys("table:*:status")
    tables_occupied = 0
    tables_dirty = 0
    for key in table_keys:
        status = await redis_client.get(key)
        if status == "OCCUPIED":
            tables_occupied += 1
        elif status == "DIRTY":
            tables_dirty += 1

    worker_keys = await redis_client.keys("worker:*:status")
    workers_current = 0
    workers_active = 0
    workers_idle = 0
    worker_statuses = []  
    for key in worker_keys:
        entity_id = key.split(":", 2)[1]
        status = await redis_client.get(key)
        workers_current += 1
        if status == "ACTIVE":
            workers_active += 1
        elif status == "IDLE":
            workers_idle += 1
        worker_statuses.append({"entity_id": entity_id, "status": status})

    customers_result = await db.execute(
        select(func.count(CustomerSession.id)).where(
            CustomerSession.status == CustomerSessionStatus.ACTIVE
        )
    )
    customers_current = customers_result.scalar() or 0

    return {
        "tables_occupied": tables_occupied,
        "tables_dirty": tables_dirty,
        "customers_current": customers_current,
        "workers_current": workers_current,
        "workers_active": workers_active,
        "workers_idle": workers_idle,
        "worker_statuses": worker_statuses,
    }
    
    
    
    
@router.get("/historical")
async def get_historical_kpis(
    period: Literal["weekly", "monthly"] = Query("weekly", description="Bucket granularity"),
    buckets: int = Query(8, ge=1, le=52, description="How many periods back to include"),
    db: AsyncSession = Depends(get_db),
    current_admin=Depends(get_current_admin),
):
    """
    Historical KPIs aggregated into weekly or monthly buckets.

    NOTE: worker idle time is derived from events_log's WORKER_ACTIVE events,
    NOT from worker_states (which only holds the current state, not history).
    Each WORKER_ACTIVE event closes out exactly one completed idle episode
    and carries that episode's full duration in details.idle_duration_seconds
    (see src/events/event_engine.py). A still-open idle episode at the end of
    the requested range is not counted yet -- it has no WORKER_ACTIVE event.
    """
    trunc_unit = "week" if period == "weekly" else "month"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    range_start = now - (
        timedelta(weeks=buckets) if period == "weekly" else timedelta(days=31 * buckets)
    )

    # 1) Completed customer sessions: count + average stay duration per bucket
    cs_bucket = func.date_trunc(trunc_unit, CustomerSession.started_at).label("bucket")
    customer_rows = (await db.execute(
        select(
            cs_bucket,
            func.count(CustomerSession.id).label("customers_served"),
            func.avg(CustomerSession.total_stay_sec).label("avg_stay_sec"),
        )
        .where(
            CustomerSession.status == CustomerSessionStatus.COMPLETED,
            CustomerSession.started_at >= range_start,
        )
        .group_by(cs_bucket)
    )).all()

    # 2) Alert counts per type per bucket
    al_bucket = func.date_trunc(trunc_unit, Alert.created_at).label("bucket")
    alert_rows = (await db.execute(
        select(al_bucket, Alert.alert_type, func.count(Alert.id).label("count"))
        .where(Alert.created_at >= range_start)
        .group_by(al_bucket, Alert.alert_type)
    )).all()

    # 3) Total worker idle seconds per bucket -- from WORKER_ACTIVE events only
    ev_bucket = func.date_trunc(trunc_unit, EventLog.event_timestamp).label("bucket")
    idle_rows = (await db.execute(
        select(
            ev_bucket,
            func.sum(cast(EventLog.details["idle_duration_seconds"].astext, Float)).label("total_idle_sec"),
        )
        .where(
            EventLog.event_type == "WORKER_ACTIVE",
            EventLog.event_timestamp >= range_start,
        )
        .group_by(ev_bucket)
    )).all()

    # -- Merge all three result sets, keyed by bucket start timestamp --
    merged: dict[str, dict] = {}

    def bucket_key(dt):
        return dt.isoformat()

    for r in customer_rows:
        merged.setdefault(bucket_key(r.bucket), {"period_start": r.bucket.isoformat()}).update({
            "customers_served": r.customers_served,
            "avg_stay_minutes": round((r.avg_stay_sec or 0) / 60, 1),
        })

    for r in alert_rows:
        entry = merged.setdefault(bucket_key(r.bucket), {"period_start": r.bucket.isoformat()})
        entry.setdefault("alerts_by_type", {})[r.alert_type] = r.count

    for r in idle_rows:
        entry = merged.setdefault(bucket_key(r.bucket), {"period_start": r.bucket.isoformat()})
        entry["total_worker_idle_seconds"] = round(r.total_idle_sec or 0, 1)

    results = sorted(merged.values(), key=lambda b: b["period_start"])
    for entry in results:
        entry.setdefault("customers_served", 0)
        entry.setdefault("avg_stay_minutes", 0)
        entry.setdefault("alerts_by_type", {})
        entry.setdefault("total_worker_idle_seconds", 0)

    return {"period": period, "buckets": results}