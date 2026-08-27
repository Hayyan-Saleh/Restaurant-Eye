import asyncio
import json
import sys
from pathlib import Path
from sqlalchemy.future import select

# 1. ضبط مسارات بيثون للوصول للباك إند والملفات الخارجية
CURRENT_FILE = Path(__file__).resolve()
BACKEND_DIR = CURRENT_FILE.parent.parent.parent  # C:\...\backend
PROJECT_ROOT = BACKEND_DIR.parent                # C:\...\restaurant-monitor

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

JSON_CONFIG_PATH = PROJECT_ROOT / "config" / "zones_config.json"

# استيراد الجلسة والموديلات
from app.core.database import AsyncSessionLocal  # تأكدي من تطابق مسار الجلسة لديك
from app.models.zone import Zone
from app.models.camera import Camera


async def seed_zones():
    print(f"🔍 Reading JSON config file from: {JSON_CONFIG_PATH}")

    if not JSON_CONFIG_PATH.exists():
        print(f"❌ Error: Config file not found at '{JSON_CONFIG_PATH}'")
        return

    # 2. قراءة ملف JSON
    with open(JSON_CONFIG_PATH, "r", encoding="utf-8") as file:
        raw_data = json.load(file)

    # 3. استخراج جميع المناطق من كل الكاميرات الموجودة بالملف
    all_zones_to_insert = []

    for camera_key, camera_data in raw_data.items():
        # تحويل اسم المفتاح (مثلاً camera_01 -> cam01 أو إبقائه كما هو حسب ID الكاميرا لديك)
        # يمكنك تعديل طريقة استخراج camera_id لتطابق ID الكاميرا المسجل بالداتابيز لديك
        camera_id = camera_key  

        zones_list = camera_data.get("zones", [])
        print(f"📹 Camera '{camera_key}' found with {len(zones_list)} zones.")

        for zone_item in zones_list:
            # تحويل النقاط [ [x, y], ... ] إلى الشكل المطلوب للباك اند {"points": [{"x": x, "y": y}, ...]}
            raw_points = zone_item.get("points", [])
            formatted_points = [
                {"x": pt[0], "y": pt[1]} for pt in raw_points if len(pt) == 2
            ]

            all_zones_to_insert.append({
                "id": zone_item.get("zone_id"),
                "camera_id": camera_id,
                "parent_zone_id": zone_item.get("parent_zone_id"),
                "name": zone_item.get("zone_name"),
                "zone_type": zone_item.get("zone_type"),
                "polygon_coordinates": {"points": formatted_points},
                "auto_generated": zone_item.get("auto_generated", False),
                "excludes_tables": zone_item.get("excludes_tables", False)
            })

    print(f"\n📊 Total extracted zones to process: {len(all_zones_to_insert)}")

    if not all_zones_to_insert:
        print("⚠️ Warning: No valid zones found in JSON file!")
        return

    # 4. إدخال المناطق لقاعدة البيانات
    async with AsyncSessionLocal() as session:
        added_count = 0
        skipped_count = 0

        for zone_dict in all_zones_to_insert:
            zone_id = zone_dict["id"]

            # أ) التاكد إن كانت المنطقة موجودة مسبقاً
            if zone_id:
                stmt = select(Zone).where(Zone.id == zone_id)
                result = await session.execute(stmt)
                if result.scalars().first():
                    print(f"⏭️ Skipped: Zone '{zone_dict['name']}' ({zone_id}) already exists.")
                    skipped_count += 1
                    continue

            # ب) إنشاء كائن المنطقة وإضافته
            new_zone = Zone(
                id=zone_dict["id"],
                camera_id=zone_dict["camera_id"],
                parent_zone_id=zone_dict["parent_zone_id"],
                name=zone_dict["name"],
                zone_type=zone_dict["zone_type"],
                polygon_coordinates=zone_dict["polygon_coordinates"],
                auto_generated=zone_dict["auto_generated"],
                excludes_tables=zone_dict["excludes_tables"]
            )

            session.add(new_zone)
            added_count += 1
            print(f"➕ Queued for insert: {zone_dict['name']} (ID: {zone_id})")

        # ج) حفظ التغييرات بالداتابيز
        if added_count > 0:
            await session.commit()
            print(f"\n🎉 SUCCESS: Successfully added {added_count} zones to DB!")
        else:
            print(f"\n⚠️ Process finished with 0 added zones ({skipped_count} skipped).")


if __name__ == "__main__":
    asyncio.run(seed_zones())