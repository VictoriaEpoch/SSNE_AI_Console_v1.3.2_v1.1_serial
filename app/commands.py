from __future__ import annotations

import math
import re
from collections.abc import Iterable


MAX_ZONE_POINTS = 12
MAX_COMMAND_CHARS = 24
MAX_DECIMAL_PLACES = 2
DECIMAL_TOKEN_RE = re.compile(
    r"^[+-]?(?:\d+\.(?P<fraction>\d*)|\.(?P<leading_fraction>\d+))(?:[eE][+-]?\d+)?$"
)
COMPACT_DECIMAL_RE = re.compile(
    r"^(?:pa|pp|ps|ph|fr|fd|fi|pi|di)"
    r"(?P<number>[+-]?(?:\d+\.\d+|\.\d+)(?:[eE][+-]?\d+)?)$",
    re.IGNORECASE,
)


def _decimal_places(token: str) -> int:
    match = DECIMAL_TOKEN_RE.fullmatch(token)
    if match is None:
        compact = COMPACT_DECIMAL_RE.fullmatch(token)
        if compact is None:
            return 0
        match = DECIMAL_TOKEN_RE.fullmatch(compact.group("number"))
    if match is None:
        return 0
    fraction = match.group("fraction")
    if fraction is None:
        fraction = match.group("leading_fraction") or ""
    return len(fraction)


def _format_decimal(value: float) -> str:
    formatted = f"{float(value):.{MAX_DECIMAL_PLACES}f}".rstrip("0").rstrip(".")
    return "0" if formatted in {"-0", "+0", ""} else formatted


def validate_command(command: str) -> str:
    """Validate one UART command without counting its trailing CRLF."""
    normalized = str(command).strip("\r\n")
    if not normalized.strip():
        raise ValueError("串口命令不能为空。")
    if "\r" in normalized or "\n" in normalized:
        raise ValueError("一条串口命令中不能包含换行符。")
    for token in normalized.split():
        places = _decimal_places(token)
        if places > MAX_DECIMAL_PLACES:
            raise ValueError(
                f"小数参数 {token!r} 有 {places} 位小数，最多允许 {MAX_DECIMAL_PLACES} 位。"
            )
    char_length = len(normalized)
    byte_length = len(normalized.encode("utf-8"))
    if char_length > MAX_COMMAND_CHARS or byte_length > MAX_COMMAND_CHARS:
        detail = (
            f"{char_length} 个字符"
            if char_length == byte_length
            else f"{char_length} 个字符、UTF-8 编码后 {byte_length} 字节"
        )
        raise ValueError(
            f"串口命令长度为 {detail}，不得超过 {MAX_COMMAND_CHARS}；"
            "长度不包含结尾 CRLF。"
        )
    return normalized


def _validate_unit_point(x: float, y: float) -> tuple[float, float]:
    point = (float(x), float(y))
    if not (0.0 <= point[0] <= 1.0 and 0.0 <= point[1] <= 1.0):
        raise ValueError("危险区域坐标必须位于 0~1 范围内。")
    return point


def build_zone_set(points: Iterable[tuple[float, float]]) -> str:
    """Build a zone-set command only when its compact form fits the UART limit."""
    normalized = [_validate_unit_point(x, y) for x, y in points]
    if len(normalized) < 3:
        raise ValueError("危险区域至少需要 3 个点。")
    if len(normalized) > MAX_ZONE_POINTS:
        raise ValueError(f"危险区域最多支持 {MAX_ZONE_POINTS} 个点。")
    values = " ".join(_format_decimal(value) for point in normalized for value in point)
    return validate_command(f"zone set {values}")


def build_zone_rect(x1: float, y1: float, x2: float, y2: float) -> str:
    """Build a zone-rect command only when its compact form fits the UART limit."""
    first = _validate_unit_point(x1, y1)
    second = _validate_unit_point(x2, y2)
    values = " ".join(_format_decimal(value) for value in (*first, *second))
    return validate_command(f"zone rect {values}")


def build_zone_upload(points: Iterable[tuple[float, float]]) -> list[str]:
    """Build a short, acknowledgement-friendly danger-zone upload sequence."""
    normalized = [_validate_unit_point(x, y) for x, y in points]
    if len(normalized) < 3:
        raise ValueError("危险区域至少需要 3 个点。")
    if len(normalized) > MAX_ZONE_POINTS:
        raise ValueError(f"危险区域最多支持 {MAX_ZONE_POINTS} 个点。")
    commands = [validate_command("zone clear")]
    commands.extend(
        validate_command(f"zone add {_format_decimal(x)} {_format_decimal(y)}")
        for x, y in normalized
    )
    commands.extend(("zone on", "zone list"))
    return commands


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
    command = f"reg {person_id} {display_name}"
    if frames != 15:
        command += f" {frames}"
    return validate_command(command)


def build_print_interval(value: int) -> str:
    value = int(value)
    if value <= 0:
        raise ValueError("打印周期必须大于 0。")
    return validate_command(f"pi{value}")


def build_benchmark(mode: str, seconds: int, sensor_fps: float) -> str:
    mode = str(mode).strip().lower()
    if mode not in {"base", "face", "pose", "all", "each"}:
        raise ValueError("测试模式必须是 base、face、pose、all 或 each。")
    seconds = int(seconds)
    sensor_fps = float(sensor_fps)
    if seconds <= 0 or not math.isfinite(sensor_fps) or sensor_fps <= 0:
        raise ValueError("测试秒数和传感器 FPS 必须大于 0。")
    return validate_command(f"test {mode} {seconds} {_format_decimal(sensor_fps)}")
