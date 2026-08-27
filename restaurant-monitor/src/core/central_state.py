# NOTE (Task 8): superseded by src/core/central_identity_store.py's
# CentralIdentityStore, which is the class actually wired into
# LivePipeline for cross-process identity sync. This file is a prior,
# unguarded teammate attempt -- it is not imported anywhere in the
# codebase and is kept only for reference, not currently used.

import redis
import json
import numpy as np

class CentralStateManager:
    def __init__(self, host='localhost', port=6379, db=0):
        # الاتصال بقاعدة البيانات المركزية
        self.r = redis.Redis(host=host, port=port, db=db, decode_responses=True)
        
    def update_person_identity(self, local_track_id, reid_vector):
        """
        تأخذ ملامح الشخص وتقارنها مع كل الأشخاص الذين لقطتهم الكاميرات الأخرى
        """
        # تحويل الـ Vector إلى نص لتخزينه
        vector_list = reid_vector.tolist() if isinstance(reid_vector, np.ndarray) else reid_vector
        lock = self.r.lock("global_identities_lock", timeout=10, blocking_timeout=10)

        if not lock.acquire(blocking=True):
            raise RuntimeError("Could not acquire Redis lock for global identity registration")

        try:
            # جلب كل الهويات المخزنة من الكاميرات الأخرى تحت قفل واحد
            all_identities = self.r.hgetall("global_identities")

            threshold = 0.7  # نسبة التشابه المطلوبة لاعتبار أنه نفس الشخص

            for global_id, stored_vector_str in all_identities.items():
                stored_vector = json.loads(stored_vector_str)
                denominator = np.linalg.norm(vector_list) * np.linalg.norm(stored_vector)
                if denominator == 0:
                    continue

                # حساب التشابه (Cosine Similarity مثلاً)
                similarity = np.dot(vector_list, stored_vector) / denominator

                if similarity > threshold:
                    # تم العثور على الشخص في كاميرا أخرى! نرجع الـ ID العالمي الخاص به
                    return global_id

            # إذا كان شخصاً جديداً تماماً، ننشئ له ID عالمي جديد داخل نفس القفل
            new_global_id = f"global_{self.r.incr('global_person_counter')}"
            self.r.hset("global_identities", new_global_id, json.dumps(vector_list))
            return new_global_id
        finally:
            lock.release()

    def update_table_status(self, table_id, status, camera_id):
        """
        تحديث حالة الطاولة في مكان مركزي لتفادي تضارب الكاميرات
        """
        # نستخدم الـ Hash لتخزين حالة كل طاولة ومن أي كاميرا جاء التحديث
        data = {"status": status, "updated_by": camera_id}
        self.r.hset("restaurant_tables", table_id, json.dumps(data))

    def get_table_status(self, table_id):
        data = self.r.hget("restaurant_tables", table_id)
        return json.loads(data) if data else None