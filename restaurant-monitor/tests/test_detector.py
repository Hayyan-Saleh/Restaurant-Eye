import unittest
import numpy as np
from src.ai_models.detector import PersonDetector

class TestPersonDetector(unittest.TestCase):
    def setUp(self):
        # تجهيز الموديل مرة واحدة قبل الفحص بالإعدادات الافتراضية
        self.detector = PersonDetector(conf_threshold=0.4, imgsz=480)

    def test_detector_output_format(self):
        # 1. صنع صورة وهمية باللون الأسود أبعادها 480x480 (ثلاث قنوات ألوان BGR)
        fake_frame = np.zeros((480, 480, 3), dtype=np.uint8)
        
        # 2. تشغيل دالة الكشف
        detections = self.detector.detect(fake_frame)
        
        # 3. التأكيد (Assertions): نتوقع أن النتيجة يجب أن تكون قائمة (List)
        self.assertIsInstance(detections, list, "المخرجات يجب أن تكون من نوع List")

if __name__ == "__main__":
    unittest.main()