from __future__ import annotations

from collections.abc import Iterable


MAX_ZONE_POINTS = 12


def _validate_unit_point(x: float, y: float) -> tuple[float, float]:
    point = (float(x), float(y))
    if not (0.0 <= point[0] <= 1.0 and 0.0 <= point[1] <= 1.0):
        raise ValueError("危险区域坐标必须位于 0~1 范围内。")
    return point


def build_zone_set(points: Iterable[tuple[float, float]]) -> str:
    """Build the board's normalized multi-point danger-zone command."""
    normalized = [_validate_unit_point(x, y) for x, y in points]
    if len(normalized) < 3:
        raise ValueError("危险区域至少需要 3 个点。")
    if len(normalized) > MAX_ZONE_POINTS:
        raise ValueError(f"危险区域最多支持 {MAX_ZONE_POINTS} 个点。")
    values = " ".join(f"{value:.6f}" for point in normalized for value in point)
    return f"zone set {values}"


def build_zone_rect(x1: float, y1: float, x2: float, y2: float) -> str:
    """Build the board's normalized rectangle danger-zone command."""
    first = _validate_unit_point(x1, y1)
    second = _validate_unit_point(x2, y2)
    values = " ".join(f"{value:.6f}" for value in (*first, *second))
    return f"zone rect {values}"


def build_enroll(person_id: str, display_name: str, frames: int = 15) -> str:
    person_id = person_id.strip()
    display_name = display_name.strip()
    if not person_id or not display_name:
        raise ValueError("person_id 和 display_name 均不能为空。")
    if any(ch.isspace() for ch in person_id + display_name):
        raise ValueError("person_id 和 display_name 不能包含空白字符。")
    frames = int(frames)
    if not 1 <= frames <= 120:
        raise ValueError("采集帧数必须在 1~120 之间。")
    return f"reg {person_id} {display_name} {frames}"
