import sys
import os
import json
import asyncio
import traceback
from pathlib import Path

# طباعة فورية للكتشف إذا كان السكريبت يعمل أصلاً
print("==================================================")
print("🚀 STARTING CAMERA SEED SCRIPT...")
print("==================================================")

# 1. تحديد المسارات
CURRENT_FILE = Path(__file__).resolve()
SEED_DIR = CURRENT_FILE.parent            # backend/app/seed
APP_DIR = SEED_DIR.parent                 # backend/app
BACKEND_DIR = APP_DIR.parent              # backend
ROOT_DIR = BACKEND_DIR.parent             # restaurant-monitor

# إضافة مجلد backend لـ sys.path
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 2. تجربة استيراد مكتبات الداتابيز مع إظهار الخطأ إن وجد
try:
    from sqlalchemy.future import select
    from app.core.database import AsyncSessionLocal
    from app.models.camera import Camera, CameraStatus
    print("✅ Database models imported successfully!")
except Exception as e:
    print("❌ Failed to import database modules:")
    traceback.print_exc()
    sys.exit(1)

# 3. تحديد مسار ملف الجيسون
JSON_PATH = ROOT_DIR / "config" / "camera_config.json"

async def seed_cameras_from_json():
    print(f"\n🔍 Looking for JSON file at:\n👉 {JSON_PATH}\n")
    
    if not JSON_PATH.exists():
        print(f"❌ ERROR: File NOT found at path:\n   {JSON_PATH}")
        print("Please check if 'config/camera_config.json' exists in the main project folder.")
        return

    try:
        with open(JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        print("✅ JSON file loaded successfully!")
    except Exception as e:
        print(f"❌ Error reading JSON file: {e}")
        return

    cameras_dict = data.get("cameras", {})
    print(f"📸 Found {len(cameras_dict)} cameras in JSON.")

    try:
        async with AsyncSessionLocal() as session:
            added_count = 0
            skipped_count = 0

            for camera_id, cam_info in cameras_dict.items():
                stmt = select(Camera).where(Camera.id == camera_id)
                result = await session.execute(stmt)
                existing_camera = result.scalars().first()

                if existing_camera:
                    skipped_count += 1
                    continue

                camera_name = cam_info.get("description") or f"Camera {camera_id} ({cam_info.get('location', '')})"
                dummy_rtsp_url = f"rtsp://localhost:8554/live/{camera_id}"

                new_camera = Camera(
                    id=camera_id,
                    name=camera_name,
                    rtsp_url=dummy_rtsp_url,
                    status=CameraStatus.OFFLINE
                )
                
                session.add(new_camera)
                added_count += 1

            await session.commit()
            print("\n--------------------------------------------------")
            print(f"🎉 SUCCESS! Added: {added_count} | Skipped: {skipped_count}")
            print("--------------------------------------------------")

    except Exception as e:
        print("\n❌ Error inserting cameras into database:")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(seed_cameras_from_json())