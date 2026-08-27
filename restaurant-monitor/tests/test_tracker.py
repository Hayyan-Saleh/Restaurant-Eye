import unittest
import numpy as np
from src.ai_models.tracker import RestaurantTracker

class TestRestaurantTracker(unittest.TestCase):
    def setUp(self):
        # إنشاء كائن التتبع
        self.tracker = RestaurantTracker()

    def test_tracker_update(self):
        # 1. محاكاة فريم وهمي وصندوق كشف وهمي لشخص [x1, y1, x2, y2, confidence, class_id]
        fake_frame = np.zeros((480, 480, 3), dtype=np.uint8)
        fake_detections = [[100, 100, 200, 300, 0.9, 0]] # شخص بنسبة ثقة 90%
        
        # 2. تمرير الكشف للـ Tracker
        tracked_objects = self.tracker.update(fake_detections, frame=fake_frame)
        
        # 3. التأكيد: يجب أن يعيد التتبع قائمة كائنات
        self.assertIsInstance(tracked_objects, list, "المخرجات يجب أن تكون قائمة بالتتبعات")

if __name__ == "__main__":
    unittest.main()