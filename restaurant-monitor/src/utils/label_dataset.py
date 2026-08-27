import argparse
import json
import os
import cv2
from collections import defaultdict

LABEL_MAP = {
    ord('1'): 'standing',
    ord('2'): 'sitting',
    ord('3'): 'walking',
    ord('4'): 'serving'
}

def label_dataset(json_path, crops_dir, camera_id):
    with open(json_path, "r") as f:
        data = json.load(f)

    print(f"Loaded {len(data)} entries from {json_path}")
    labeled_count = sum(1 for d in data if d.get("label"))
    print(f"Already labeled: {labeled_count}/{len(data)}")

    # Group indices by tracked_person_id (preserving insertion order)
    person_groups = defaultdict(list)
    for i, entry in enumerate(data):
        person_groups[entry['tracked_person_id']].append(i)

    cv2.namedWindow("Labeling UI", cv2.WINDOW_NORMAL)

    for person_id, indices in person_groups.items():
        unlabeled = [i for i in indices if not data[i].get("label")]
        if not unlabeled:
            continue

        print(f"\n--- Person {person_id} ({len(unlabeled)} unlabeled / {len(indices)} total) ---")

        for i in unlabeled:
            entry = data[i]

            crop_filename = f"{camera_id}_{entry['vid_id']}_frame_{entry['frame_id']:06d}_track_{entry['tracked_person_id']}.jpg"
            crop_path = os.path.join(crops_dir, crop_filename)

            if not os.path.exists(crop_path):
                print(f"Warning: {crop_path} not found. Skipped.")
                continue

            img = cv2.imread(crop_path)
            if img is None:
                print(f"Warning: Could not read {crop_path}. Skipped.")
                continue

            while True:
                display_img = img.copy()
                h, w = display_img.shape[:2]
                if min(h, w) < 300:
                    scale = 300.0 / min(h, w)
                    display_img = cv2.resize(display_img, (0, 0), fx=scale, fy=scale)

                cv2.putText(display_img, "1:Stand 2:Sit 3:Walk 4:Serve | s:Skip q:Quit",
                            (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                cv2.imshow("Labeling UI", display_img)

                print(f"\n[Person {person_id}] {crop_filename}")
                print("1=standing, 2=sitting, 3=walking, 4=serving | s=skip, q=quit")

                key = cv2.waitKey(0) & 0xFF

                if key in LABEL_MAP:
                    entry['label'] = LABEL_MAP[key]
                    print(f"-> Assigned: {entry['label']}")
                    break
                elif key == ord('s'):
                    print("-> Skipped")
                    break
                elif key == ord('q'):
                    print("\nQuitting... saving progress.")
                    with open(json_path, "w") as f:
                        json.dump(data, f, indent=2)
                    cv2.destroyAllWindows()
                    return

            with open(json_path, "w") as f:
                json.dump(data, f, indent=2)

    cv2.destroyAllWindows()
    print("\nDataset labeling complete!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="UI for manual action labeling.")
    parser.add_argument("--json", default="data/debug/classification/samples/raw_poses.json", help="Path to JSON file")
    parser.add_argument("--crops", default="data/debug/crops", help="Path to crops dir")
    parser.add_argument("--camera", default="live_camera", help="Camera ID (default: live_camera)")
    args = parser.parse_args()

    label_dataset(args.json, args.crops, args.camera)