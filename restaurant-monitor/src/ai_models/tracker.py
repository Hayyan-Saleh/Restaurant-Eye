# src/tracking/tracker.py

import numpy as np
from deep_sort_realtime.deepsort_tracker import DeepSort

class RestaurantTracker:
    def __init__(
        self, 
        max_age: int | None = None, 
        n_init: int | None = None, 
        nms_max_overlap: float | None = None
    ):
        # 💡 هنا نحدد قيم الأمان المركزية والوحيدة في الكود (Single Source of Truth)
        self.max_age = max_age if max_age is not None else 5
        self.n_init = n_init if n_init is not None else 1
        self.nms_max_overlap = nms_max_overlap if nms_max_overlap is not None else 0.8

        # إعدادات متوافقة مع الـ 2 FPS ومحمية داخلياً
        self.tracker = DeepSort(
            max_age=self.max_age,
            n_init=self.n_init,
            nms_max_overlap=self.nms_max_overlap,
            embedder="mobilenet",
            half=True,
            max_cosine_distance=0.4
        )

    def update(self, detections, frame=None):
        raw_deepsort_detections = []
        
        for det in detections:
            box = det["box"]
            conf = det["conf"]
            
            w = box[2] - box[0]
            h = box[3] - box[1]
            ltwh = [box[0], box[1], w, h]
            
            raw_deepsort_detections.append((ltwh, conf, "person"))

        # تمرير الفريم (سواء كان صورة حقيقية أو None)
        tracks = self.tracker.update_tracks(raw_deepsort_detections, frame=frame)
        
        updated_detections = []
        for track in tracks:
            # اقبل التراكات الحديثة أيضًا حتى لا نضيع الأشخاص الجدد داخل المشهد
            is_confirmed = track.is_confirmed() if hasattr(track, "is_confirmed") else True
            time_since_update = getattr(track, "time_since_update", 0)
            if not is_confirmed and time_since_update > 1:
                continue
                
            track_id = track.track_id
            ltrb = track.to_ltrb()
            
            updated_detections.append({
                "track_id": int(track_id),
                "box": [float(ltrb[0]), float(ltrb[1]), float(ltrb[2]), float(ltrb[3])],
                "conf": float(track.get_det_conf() if track.get_det_conf() else 1.0),
                "class_id": 0,
                "class_name": "person"
            })
            
        return updated_detections