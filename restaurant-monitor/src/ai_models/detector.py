from typing import Any
import numpy as np
from ultralytics import YOLO


class PersonDetector:
    def __init__(
        self,
        model_name: str | None = None,
        conf_threshold: float | None = None,
        imgsz: int | None = None,
        min_box_width: int | None = None,
        min_box_height: int | None = None,
        device: str | None = None,
    ):
        # (Single Source of Truth)
        self.model_name = model_name or "models/yolo/yolov8n.pt"
        self.conf_threshold = conf_threshold if conf_threshold is not None else 0.4
        self.imgsz = imgsz if imgsz is not None else 480
        self.min_box_width = min_box_width if min_box_width is not None else 20
        self.min_box_height = min_box_height if min_box_height is not None else 20
        self.device = device

        self.model = YOLO(self.model_name)

    def detect(self, frame: np.ndarray) -> list[dict[str, Any]]:

        predict_kwargs = {
            "source": frame,
            "conf": self.conf_threshold,
            "imgsz": self.imgsz,
            "classes": [0],  
            "verbose": False,
        }

        if self.device is not None:
            predict_kwargs["device"] = self.device

        results = self.model.predict(**predict_kwargs)

        detections = []

        for result in results:
            for box in result.boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                conf = float(box.conf[0])

                x1, y1, x2, y2 = map(int, [x1, y1, x2, y2])

                width = x2 - x1
                height = y2 - y1

                if width < self.min_box_width or height < self.min_box_height:
                    continue

                detections.append(
                    {
                        "track_id": None,
                        "box": [x1, y1, x2, y2],
                        "conf": round(conf, 4),
                        "class_id": 0,
                        "class_name": "person",
                    }
                )

        return detections
    
    def detect_with_pose(self, frame: np.ndarray) -> tuple[list[dict], list[np.ndarray] | None]:
        predict_kwargs = {
            "source": frame,
            "conf": self.conf_threshold,
            "imgsz": self.imgsz,
            "classes": [0],
            "verbose": False,
        }
        if self.device is not None:
            predict_kwargs["device"] = self.device

        results = self.model.predict(**predict_kwargs)

        detections = []
        keypoints_list = []  # parallel to detections

        for result in results:
            kpts_data = result.keypoints.data.cpu().numpy() if result.keypoints is not None else None

            for i, box in enumerate(result.boxes):
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                conf = float(box.conf[0])

                if (x2 - x1) < self.min_box_width or (y2 - y1) < self.min_box_height:
                    continue

                detections.append({
                    "track_id": None,
                    "box": [x1, y1, x2, y2],
                    "conf": round(conf, 4),
                    "class_id": 0,
                    "class_name": "person",
                })
                keypoints_list.append(kpts_data[i] if kpts_data is not None else None)

        return detections, keypoints_list