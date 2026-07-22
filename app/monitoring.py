from __future__ import annotations

import time
from dataclasses import dataclass
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
