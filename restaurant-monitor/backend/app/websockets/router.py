import uuid

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.database import get_db
from app.core.redis import redis_client
from app.core.security import decode_token
from app.models.admin import Admin
from app.websockets.manager import manager

router = APIRouter()


async def _authenticate_websocket(token: str | None, db: AsyncSession) -> bool:
    if not token:
        return False

    try:
        payload = decode_token(token)
    except Exception:
        return False

    jti = payload.get("jti")
    if jti and await redis_client.exists(f"revoked_token:{jti}"):
        return False

    admin_id_str = payload.get("sub")
    try:
        admin_id_uuid = uuid.UUID(admin_id_str)
    except (ValueError, TypeError):
        return False

    stmt = select(Admin).where(Admin.id == admin_id_uuid)
    result = await db.execute(stmt)
    admin = result.scalars().first()
    return admin is not None


@router.websocket("/ws/dashboard")
async def websocket_dashboard(
    websocket: WebSocket,
    token: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    is_valid = await _authenticate_websocket(token, db)
    if not is_valid:
        await websocket.close(code=1008)
        return

    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)