import json
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[3]

ZONES_CONFIG_PATH = PROJECT_DIR / "config" / "zones_config.json"
CAMERA_CONFIG_PATH = PROJECT_DIR / "config" / "camera_config.json"


def main():
    with open(ZONES_CONFIG_PATH, "r", encoding="utf-8") as f:
        zones_config = json.load(f)

    with open(CAMERA_CONFIG_PATH, "r", encoding="utf-8") as f:
        camera_config = json.load(f)

    configured_cameras = set(camera_config.get("cameras", {}).keys())

    errors = []
    warnings = []

    for camera_id, camera_zones in zones_config.items():
        if camera_id not in configured_cameras:
            warnings.append(f"{camera_id}: exists in zones_config but is missing from camera_config")

        image_size = camera_zones.get("image_size", {})
        width = image_size.get("width")
        height = image_size.get("height")

        if not width or not height:
            errors.append(f"{camera_id}: missing image_size.width or image_size.height")
            continue

        zones = camera_zones.get("zones", [])

        if not zones:
            warnings.append(f"{camera_id}: no zones defined")
            continue

        seen_zone_ids = set()

        for zone in zones:
            zone_id = zone.get("zone_id")
            zone_type = zone.get("zone_type")
            points = zone.get("points", [])

            if not zone_id:
                errors.append(f"{camera_id}: zone is missing zone_id")
                continue

            if zone_id in seen_zone_ids:
                errors.append(f"{camera_id}: duplicate zone_id: {zone_id}")

            seen_zone_ids.add(zone_id)

            if not zone_type:
                errors.append(f"{camera_id}/{zone_id}: missing zone_type")

            if len(points) < 3:
                errors.append(f"{camera_id}/{zone_id}: zone must have at least 3 points")

            for point in points:
                if not isinstance(point, list) or len(point) != 2:
                    errors.append(f"{camera_id}/{zone_id}: invalid point: {point}")
                    continue

                x, y = point

                if x < 0 or y < 0 or x > width or y > height:
                    warnings.append(
                        f"{camera_id}/{zone_id}: point is outside image bounds {point} image_size=({width}, {height})"
                    )

    print("========== ZONES CONFIG VALIDATION ==========")

    if errors:
        print("\nERRORS:")
        for error in errors:
            print("-", error)
    else:
        print("\nNo errors found.")

    if warnings:
        print("\nWARNINGS:")
        for warning in warnings:
            print("-", warning)
    else:
        print("\nNo warnings found.")

    print("\nCameras in zones_config:", len(zones_config))


if __name__ == "__main__":
    main()