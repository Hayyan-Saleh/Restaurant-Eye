import redis
import numpy as np
from src.core.central_state import CentralStateManager

print("=== جاري بدء اختبار مزامنة الهويات الموحدة ===")

# 1. الاتصال بـ Redis وتصفيره للتجربة بنظافة
try:
    # نستخدم بروتوكول 2 المتوافق مع لاراغون عندك
    r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True, protocol=2)
    r.flushdb()
    print("✅ تم الاتصال بسيرفر Redis في لاراغون وتصفيره بنجاح!")
except Exception as e:
    print(f"❌ فشل الاتصال بـ Redis: {e}")
    print("تأكد أن لاراغون يعمل وأن الـ Redis مشغّل فيه.")
    exit()

# 2. تشغيل مدير الحالة المشترك
manager = CentralStateManager(host='localhost', port=6379, db=0)
manager.r = r  # نجبره على استخدام نفس الاتصال السليم

# 3. محاكاة بصمة شخص (Vector)
# سنقوم بإنشاء بصمة ثابتة تمثل ملامح "أحمد" مثلاً
person_features = np.array([0.1, 0.5, 0.8, -0.2] * 32) # بصمة وهمية مكونة من 128 رقم

# --- السيناريو ---
print("\n🎬 [السيناريو]: 'أحمد' دخل المطعم ولقطته الكاميرا الأولى أولاً، ثم انتقل لزاوية الكاميرا الثانية...")

# الكاميرا 1 ترسل البصمة للـ Redis
global_id_cam1 = manager.update_person_identity(local_track_id=99, reid_vector=person_features)
print(f"📷 [الكاميرا 1] رصدت الشخص (الـ Track المحلي 99) -> تم ربطه بالهوية العالمية: {global_id_cam1}")

# الكاميرا 2 ترصد نفس الشخص ببصمته
global_id_cam2 = manager.update_person_identity(local_track_id=55, reid_vector=person_features)
print(f"📷 [الكاميرا 2] رصدت نفس الشخص (الـ Track المحلي 55) -> تم ربطه بالهوية العالمية: {global_id_cam2}")

# 4. النتيجة النهائية للتحقق
print("\n=== النتيجة النهائية ===")
if global_id_cam1 == global_id_cam2:
    print(f"🎉 نجاح باهر ومؤكد بنسبة 100% !!!")
    print(f"الهويتان متطابقتان تماماً ({global_id_cam1} == {global_id_cam2})")
    print("الآن أي شخص ينتقل بين الكاميرات سيُعرف بنفس الهوية المشتركة عبر الـ Redis!")
else:
    print("❌ هناك اختلاف في الهويات. يرجى مراجعة كود المقارنة.")