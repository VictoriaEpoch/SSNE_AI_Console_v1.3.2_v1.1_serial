from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class SedentaryState:
    active: bool
    elapsed_seconds: float
    triggered: bool = False


class SedentaryMonitor:
    """Track continuous SITTING posture reported by the device debug status."""

    def __init__(self, threshold_seconds: float = 30 * 60, grace_seconds: float = 5.0):
        self.enabled = True
        self.threshold_seconds = max(1.0, float(threshold_seconds))
        self.grace_seconds = max(0.0, float(grace_seconds))
        self.started_at: Optional[float] = None
        self.last_sitting_at: Optional[float] = None
        self.alarmed = False

    def configure(self, enabled: bool, threshold_seconds: float) -> None:
        self.enabled = bool(enabled)
        self.threshold_seconds = max(1.0, float(threshold_seconds))
        if not self.enabled:
            self.reset()

    def reset(self) -> None:
        self.started_at = None
        self.last_sitting_at = None
        self.alarmed = False

    def observe(
        self,
        *,
        person_active: bool,
        posture_class: Optional[int],
        posture_valid: bool,
        now: Optional[float] = None,
    ) -> SedentaryState:
        current = time.monotonic() if now is None else float(now)
        if not self.enabled or not person_active:
            self.reset()
            return SedentaryState(False, 0.0)

        if posture_valid:
            if posture_class == 1:
                if self.started_at is None:
                    self.started_at = current
                self.last_sitting_at = current
            else:
                self.reset()
                return SedentaryState(False, 0.0)

        return self.tick(current)

    def tick(self, now: Optional[float] = None) -> SedentaryState:
        current = time.monotonic() if now is None else float(now)
        if not self.enabled or self.started_at is None or self.last_sitting_at is None:
            return SedentaryState(False, 0.0)
        if current - self.last_sitting_at > self.grace_seconds:
            self.reset()
            return SedentaryState(False, 0.0)

        elapsed = max(0.0, current - self.started_at)
        triggered = elapsed >= self.threshold_seconds and not self.alarmed
        if triggered:
            self.alarmed = True
        return SedentaryState(True, elapsed, triggered)


@dataclass(frozen=True)
class NightRiseState:
    in_schedule: bool
    armed: bool
    phase: str
    triggered: bool = False
    elapsed_seconds: float = 0.0


class NightRiseMonitor:
    """Detect a confirmed LYING -> SITTING/STANDING transition at night."""

    def __init__(
        self,
        *,
        start_time: str = "22:00",
        end_time: str = "06:00",
        lying_confirm_seconds: float = 30.0,
        rise_confirm_seconds: float = 2.0,
    ):
        self.enabled = True
        self.start_minute = 22 * 60
        self.end_minute = 6 * 60
        self.lying_confirm_seconds = 30.0
        self.rise_confirm_seconds = 2.0
        self.lying_since: Optional[float] = None
        self.upright_since: Optional[float] = None
        self.armed = False
        self.configure(
            enabled=True,
            start_time=start_time,
            end_time=end_time,
            lying_confirm_seconds=lying_confirm_seconds,
            rise_confirm_seconds=rise_confirm_seconds,
        )

    @staticmethod
    def parse_clock(value: str) -> int:
        parts = str(value).strip().split(":")
        if len(parts) != 2:
            raise ValueError("时间必须使用 HH:MM 格式。")
        try:
            hour, minute = int(parts[0]), int(parts[1])
        except ValueError as exc:
            raise ValueError("时间必须使用 HH:MM 格式。") from exc
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("小时必须为 0~23，分钟必须为 0~59。")
        return hour * 60 + minute

    @staticmethod
    def format_clock(total_minutes: int) -> str:
        hour, minute = divmod(int(total_minutes) % (24 * 60), 60)
        return f"{hour:02d}:{minute:02d}"

    def configure(
        self,
        *,
        enabled: bool,
        start_time: str,
        end_time: str,
        lying_confirm_seconds: float,
        rise_confirm_seconds: float,
    ) -> None:
        start_minute = self.parse_clock(start_time)
        end_minute = self.parse_clock(end_time)
        lying_seconds = float(lying_confirm_seconds)
        rise_seconds = float(rise_confirm_seconds)
        if not 1.0 <= lying_seconds <= 3600.0:
            raise ValueError("躺卧确认时间必须在 1~3600 秒之间。")
        if not 0.5 <= rise_seconds <= 60.0:
            raise ValueError("起身确认时间必须在 0.5~60 秒之间。")
        self.enabled = bool(enabled)
        self.start_minute = start_minute
        self.end_minute = end_minute
        self.lying_confirm_seconds = lying_seconds
        self.rise_confirm_seconds = rise_seconds
        self.reset()

    def reset(self) -> None:
        self.lying_since = None
        self.upright_since = None
        self.armed = False

    def is_in_schedule(self, wall_time: Optional[datetime] = None) -> bool:
        current = datetime.now() if wall_time is None else wall_time
        minute = current.hour * 60 + current.minute
        if self.start_minute == self.end_minute:
            return True
        if self.start_minute < self.end_minute:
            return self.start_minute <= minute < self.end_minute
        return minute >= self.start_minute or minute < self.end_minute

    def observe(
        self,
        *,
        person_active: bool,
        posture_class: Optional[int],
        posture_valid: bool,
        now: Optional[float] = None,
        wall_time: Optional[datetime] = None,
    ) -> NightRiseState:
        current = time.monotonic() if now is None else float(now)
        if not self.enabled:
            self.reset()
            return NightRiseState(False, False, "disabled")
        if not self.is_in_schedule(wall_time):
            self.reset()
            return NightRiseState(False, False, "outside_schedule")
        if not person_active:
            self.upright_since = None
            return NightRiseState(True, self.armed, "no_person")
        if not posture_valid or posture_class not in {0, 1, 2}:
            self.upright_since = None
            return NightRiseState(True, self.armed, "waiting_posture")

        if posture_class == 0:  # LYING
            self.upright_since = None
            if self.lying_since is None:
                self.lying_since = current
            elapsed = max(0.0, current - self.lying_since)
            if elapsed >= self.lying_confirm_seconds:
                self.armed = True
                return NightRiseState(True, True, "armed", elapsed_seconds=elapsed)
            return NightRiseState(True, self.armed, "confirming_lying", elapsed_seconds=elapsed)

        # SITTING or STANDING
        self.lying_since = None
        if not self.armed:
            self.upright_since = None
            return NightRiseState(True, False, "waiting_lying")
        if self.upright_since is None:
            self.upright_since = current
        elapsed = max(0.0, current - self.upright_since)
        if elapsed >= self.rise_confirm_seconds:
            self.armed = False
            self.upright_since = None
            return NightRiseState(True, False, "alerted", triggered=True, elapsed_seconds=elapsed)
        return NightRiseState(True, True, "confirming_rise", elapsed_seconds=elapsed)

    def tick(self, wall_time: Optional[datetime] = None) -> NightRiseState:
        if not self.enabled:
            return NightRiseState(False, False, "disabled")
        if not self.is_in_schedule(wall_time):
            self.reset()
            return NightRiseState(False, False, "outside_schedule")
        return NightRiseState(True, self.armed, "armed" if self.armed else "waiting_lying")
