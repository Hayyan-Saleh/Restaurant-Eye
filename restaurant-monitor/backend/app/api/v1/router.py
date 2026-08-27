# Aggregate all Routers under v1
from fastapi import APIRouter
from app.api.v1.endpoints import zones, cameras, alerts, settings, kpis, auth, video
from app.api.v1.endpoints import live_status

api_router = APIRouter()

# Register all Routers
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(zones.router, prefix="/zones", tags=["Zones"])
api_router.include_router(cameras.router, prefix="/cameras", tags=["Cameras"])
api_router.include_router(alerts.router, prefix="/alerts", tags=["Alerts"])
api_router.include_router(settings.router, prefix="/settings", tags=["Settings"])
api_router.include_router(live_status.router, prefix="/live-status", tags=["Live Status"])
api_router.include_router(kpis.router, prefix="/kpis", tags=["KPIs"])
api_router.include_router(video.router, prefix="/video", tags=["Video"])