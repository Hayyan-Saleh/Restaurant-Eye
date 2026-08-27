from app.core.database import Base

# Import individual models to avoid circular import issues
from app.models import admin
from app.models import camera
from app.models import zone
from app.models import setting
from app.models import customer_session
from app.models import worker_state
from app.models import table_state
from app.models import event_log
from app.models import alert

Admin = admin.Admin
Camera = camera.Camera
CameraStatus = camera.CameraStatus
Zone = zone.Zone
SystemSetting = setting.SystemSetting
CustomerSession = customer_session.CustomerSession
CustomerSessionStatus = customer_session.CustomerSessionStatus
WorkerState = worker_state.WorkerState
WorkerRole = worker_state.WorkerRole
WorkerActivityStatus = worker_state.WorkerActivityStatus
TableState = table_state.TableState
TableStateEnum = table_state.TableStateEnum
EventLog = event_log.EventLog
Alert = alert.Alert
AlertStatusEnum = alert.AlertStatusEnum

__all__ = [
    "Base",
    "CameraStatus",
    "CustomerSessionStatus",
    "WorkerRole",
    "WorkerActivityStatus",
    "TableStateEnum",
    "AlertStatusEnum",
    "Admin",
    "Camera",
    "Zone",
    "SystemSetting",
    "CustomerSession",
    "WorkerState",
    "TableState",
    "EventLog",
    "Alert",
]