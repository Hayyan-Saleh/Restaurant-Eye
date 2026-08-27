import json
import sys
from pathlib import Path

import cv2
import numpy as np


PROJECT_DIR = Path(__file__).resolve().parents[3]
sys.path.append(str(PROJECT_DIR))

ZONES_CONFIG_PATH = PROJECT_DIR / "config" / "zones_config.json"
REFERENCE_FRAMES_DIR = PROJECT_DIR / "data" / "debug" / "zones" / "reference_frames"
OUTPUT_DIR = PROJECT_DIR / "data" / "debug" / "zones" / "zones_preview"


def draw_zones(camera_id: str, camera_config: dict):
    image_path = REFERENCE_FRAMES_DIR / f"{camera_id}_reference.jpg"

    if not image_path.exists():
        print(f"⚠️ Missing reference image: {image_path}")
        return

    image = cv2.imread(str(image_path))

    if image is None:
        print(f"⚠️ Cannot read image: {image_path}")
        return

    zones = camera_config.get("zones", [])

    for zone in zones:
        points = np.array(zone["points"], dtype=np.int32)

        cv2.polylines(image, [points], isClosed=True, color=(0, 255, 0), thickness=3)

        x, y = points[0]
        label = f"{zone['zone_id']} ({zone['zone_type']})"

        cv2.putText(
            image,
            label,
            (int(x), int(y) - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 0),
            2,
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"{camera_id}_zones_preview.jpg"
    cv2.imwrite(str(output_path), image)

    print(f"✅ Saved preview: {output_path}")


def main():
    with open(ZONES_CONFIG_PATH, "r", encoding="utf-8") as f:
        zones_config = json.load(f)

    for camera_id, camera_config in zones_config.items():
        draw_zones(camera_id, camera_config)


if __name__ == "__main__":
    main()