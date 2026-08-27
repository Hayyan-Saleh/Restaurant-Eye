import json
import sys
from pathlib import Path

import cv2


PROJECT_DIR = Path(__file__).resolve().parents[3]
sys.path.append(str(PROJECT_DIR))

VIDEOS_INDEX_PATH = PROJECT_DIR / "data" / "metadata" / "videos" / "videos_index.json"
OUTPUT_DIR = PROJECT_DIR / "data" / "debug" / "zones" / "reference_frames"


def extract_frame(video_path: Path, output_path: Path, target_second: int = 30):
    cap = cv2.VideoCapture(str(video_path))

    if not cap.isOpened():
        print(f"❌ Cannot open video: {video_path}")
        return False

    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        fps = 25

    target_frame = int(fps * target_second)
    cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)

    ret, frame = cap.read()

    if not ret:
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        ret, frame = cap.read()

    cap.release()

    if not ret:
        print(f"❌ Cannot extract frame from: {video_path}")
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), frame)

    print(f"✅ Saved reference frame: {output_path}")
    return True


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with open(VIDEOS_INDEX_PATH, "r", encoding="utf-8") as f:
        videos = json.load(f)

    used_cameras = set()

    for video in videos:
        camera_id = video["camera_id"]

        if camera_id in used_cameras:
            continue

        used_cameras.add(camera_id)

        video_path = PROJECT_DIR / video["path"]
        output_path = OUTPUT_DIR / f"{camera_id}_reference.jpg"

        extract_frame(video_path=video_path, output_path=output_path, target_second=30)


if __name__ == "__main__":
    main()