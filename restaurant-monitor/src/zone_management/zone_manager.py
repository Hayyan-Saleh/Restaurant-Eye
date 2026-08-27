import json
from pathlib import Path
from typing import Any

from shapely.geometry import Point, Polygon, box as shapely_box


class ZoneManager:
    ZONE_TYPE_MAP: dict[str, str] = {
        "table_area": "table",
        "table": "table",
        "service_path": "walk",
        "hallway": "walk",
        "corridor": "walk",
        "open_area": "walk",
        "mixed_area": "walk",
        "entrance": "walk",
        "bathroom_entrance": "walk",
        "prayer_room_entrance": "walk",
        "staff_area": "work",
        "buffet": "work",
        "cashier": "work",
    }

    LOWER_BODY_KP_INDICES = (11, 12, 13, 14, 15, 16)
    HAND_KP_INDICES = (9, 10)
    KP_CONF_THRESHOLD = 0.25

    # --- إعدادات محرك الحالات (State Engine Config) ---
    TIME_TO_OCCUPY = 3.0  # عدد الثواني اللازمة لاعتبار الطاولة "مشغولة"
    TIME_TO_VACATE = 5.0  # عدد الثواني بعد مغادرة الشخص لاعتبارها "فاضية"

    def _is_table_zone(self, zone_type: str | None) -> bool:
        return self.ZONE_TYPE_MAP.get(zone_type or "", zone_type or "") == "table"

    def __init__(self, zones_config_path: str = "config/zones_config.json"):
        self.project_dir = Path.cwd()
        self.zones_config_path = self.project_dir / zones_config_path

        if not self.zones_config_path.exists():
            raise FileNotFoundError(
                f"zones_config.json not found: {self.zones_config_path}"
            )

        with open(self.zones_config_path, "r", encoding="utf-8") as f:
            self.zones_config = json.load(f)

    def get_bbox_zone(
        self,
        camera_id: str,
        box: list[int | float],
        keypoints=None,
    ) -> dict[str, Any]:
        camera_zones = self.zones_config.get(camera_id)
        if camera_zones is None:
            return self._unknown_zone(reason="camera_not_found", box=box)

        zones = camera_zones.get("zones", [])
        if not zones:
            return self._unknown_zone(reason="no_zones", box=box)

        # -- Step 1: hand/table check takes priority -- a person whose hands
        # are on/near a table (e.g. sitting) must not be short-circuited into
        # a walk zone just because their feet/legs geometrically overlap an
        # adjacent aisle.
        table_hit, table_hit_method = self._find_table_by_hands(zones, keypoints)
        if table_hit is not None:
            return self._zone_result(
                table_hit, 100.0, box, method=table_hit_method, zone_specificity="specific"
            )

        # -- Step 1b: skeleton check for walk zones --
        walk_zone_hit = self._check_lower_body_in_walk_zone(zones, keypoints)
        if walk_zone_hit is not None:
            return self._zone_result(
                walk_zone_hit, 100.0, box, method="skeleton"
            )

        # -- Step 2: intersection fallback --
        x1, y1, x2, y2 = box
        y1_trimmed = y1 + 0.10 * (y2 - y1)
        bbox_poly = shapely_box(x1, y1_trimmed, x2, y2)
        bbox_area = bbox_poly.area

        if bbox_area == 0:
            return self._unknown_zone(reason="empty_bbox", box=box)

        work_pct = 0.0
        table_area_pct = 0.0
        work_zone = None
        table_area_zone = None

        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        center_point = Point(cx, cy)

        table_specific_zone, table_match_method = self._find_table_by_hands(
            zones, keypoints
        )
        if table_specific_zone is None:
            table_specific_zone = self._find_table_by_center(
                zones, center_point
            )
            table_match_method = "center_point"

        for zone in zones:
            points = zone.get("points", [])
            if len(points) < 3:
                continue

            polygon = Polygon(points)
            if not polygon.is_valid:
                continue

            inter_area = bbox_poly.intersection(polygon).area
            pct = (inter_area / bbox_area) * 100

            raw_zone_type = zone.get("zone_type")
            zone_type = self.ZONE_TYPE_MAP.get(raw_zone_type, "unknown")

            if zone_type in ("work", "walk") and pct > work_pct:
                work_pct = pct
                work_zone = zone
            elif (
                zone_type == "table"
                and raw_zone_type == "table_area"
                and pct > table_area_pct
            ):
                table_area_pct = pct
                table_area_zone = zone

        if work_pct >= 30 and work_zone is not None:
            return self._zone_result(
                work_zone, work_pct, box, method="intersection"
            )

        if table_specific_zone is not None:
            return self._zone_result(
                table_specific_zone, 100.0, box, method=table_match_method, zone_specificity="specific"
            )

        if table_area_zone is not None:
            return self._zone_result(
                table_area_zone, table_area_pct, box, method="intersection", zone_specificity="area"
            )

        return self._unknown_zone(reason="no_sufficient_intersection", box=box)

    # --- التوابع المساعدة المتبقية (دون تغيير بالمنطق الداخلي لضمان دقتها) ---
    def _find_table_by_hands(
        self, zones, keypoints
    ) -> tuple[dict[str, Any] | None, str]:
        if keypoints is None:
            return None, "hands"
        table_zones = [
            zone
            for zone in zones
            if zone.get("zone_type") == "table"
            and len(zone.get("points", [])) >= 3
        ]
        if not table_zones:
            return None, "hands"

        for idx in self.HAND_KP_INDICES:
            if idx >= len(keypoints):
                continue
            x, y, conf = keypoints[idx]
            if conf < self.KP_CONF_THRESHOLD:
                continue
            point = Point(float(x), float(y))
            best_zone, best_area = None, None
            for zone in table_zones:
                polygon = Polygon(zone["points"])
                if polygon.is_valid and polygon.contains(point):
                    if best_area is None or polygon.area < best_area:
                        best_area = polygon.area
                        best_zone = zone
            if best_zone is not None:
                return best_zone, "hands"
        return None, "hands"

    def _find_table_by_center(
        self, zones, center_point: Point
    ) -> dict[str, Any] | None:
        best_zone, best_area = None, None
        for zone in zones:
            if zone.get("zone_type") != "table":
                continue
            points = zone.get("points", [])
            if len(points) < 3:
                continue
            polygon = Polygon(points)
            if not polygon.is_valid or not polygon.contains(center_point):
                continue
            if best_area is None or polygon.area < best_area:
                best_area = polygon.area
                best_zone = zone
        return best_zone

    def _check_lower_body_in_walk_zone(
        self, zones, keypoints
    ) -> dict[str, Any] | None:
        if keypoints is None:
            return None
        walk_zones = [
            zone
            for zone in zones
            if self.ZONE_TYPE_MAP.get(zone.get("zone_type"), "unknown")
            == "walk"
            and len(zone.get("points", [])) >= 3
        ]
        if not walk_zones:
            return None
        for idx in self.LOWER_BODY_KP_INDICES:
            if idx >= len(keypoints):
                continue
            x, y, conf = keypoints[idx]
            if conf < self.KP_CONF_THRESHOLD:
                continue
            point = Point(float(x), float(y))
            for zone in walk_zones:
                polygon = Polygon(zone["points"])
                if polygon.is_valid and polygon.contains(point):
                    return zone
        return None

    def _zone_result(
        self,
        zone: dict[str, Any],
        pct: float,
        box: list[int | float] | None = None,
        method: str = "intersection",
        zone_specificity: str | None = None,
    ) -> dict[str, Any]:
        return {
            "zone_id": zone.get("zone_id"),
            "zone_type": zone.get("zone_type"),
            "zone_name": zone.get("zone_name"),
            "intersection_pct": round(pct, 2),
            "box": box,
            "matched": True,
            "method": method,
            "zone_specificity": zone_specificity,
        }

    def _unknown_zone(
        self, reason: str, box: list[int | float] | None = None
    ) -> dict[str, Any]:
        return {
            "zone_id": "unknown",
            "zone_type": "unknown",
            "zone_name": "Unknown",
            "intersection_pct": 0.0,
            "box": box,
            "matched": False,
            "reason": reason,
        }