from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Tuple


@dataclass
class Zone:
    zone_id: int
    name: str
    x1: int
    y1: int
    x2: int
    y2: int
    speed_limit: float
    pixel_to_meter_scale: Optional[float] = None
    is_active: bool = True

    def contains(self, point: Tuple[float, float]) -> bool:
        x, y = point
        return self.is_active and self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2


def zone_from_record(record: Dict) -> Zone:
    return Zone(
        zone_id=int(record["zone_id"]),
        name=record["zone_name"],
        x1=int(record["x1"]), y1=int(record["y1"]), x2=int(record["x2"]), y2=int(record["y2"]),
        speed_limit=float(record["speed_limit"]),
        pixel_to_meter_scale=float(record["pixel_to_meter_scale"]) if record.get("pixel_to_meter_scale") is not None else None,
        is_active=bool(record.get("is_active", 1)),
    )


def find_matching_zone(zones: Iterable[Zone], point: Tuple[float, float]) -> Optional[Zone]:
    for zone in zones:
        if zone.contains(point):
            return zone
    return None
