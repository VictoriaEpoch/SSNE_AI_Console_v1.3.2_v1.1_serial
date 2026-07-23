from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional


ACTION_LABELS = {
    "none": "不执行",
    "toggle_living_light": "切换客厅灯",
    "toggle_bedroom_light": "切换卧室灯",
    "toggle_curtain": "打开/关闭窗帘",
    "toggle_ac": "打开/关闭空调",
    "toggle_tv": "打开/关闭电视",
    "living_brighter": "客厅灯调亮",
    "living_dimmer": "客厅灯调暗",
    "bedroom_brighter": "卧室灯调亮",
    "bedroom_dimmer": "卧室灯调暗",
    "ac_warmer": "空调调高 1℃",
    "ac_cooler": "空调调低 1℃",
    "tv_volume_up": "电视音量 +5",
    "tv_volume_down": "电视音量 -5",
    "all_lights_on": "打开全部灯光",
    "all_lights_off": "关闭全部灯光",
    "home_scene": "回家场景",
    "away_scene": "离家场景",
    "movie_scene": "观影场景",
    "reading_scene": "阅读场景",
    "sleep_scene": "睡眠场景",
}

DEFAULT_GESTURE_MAPPING = {
    0: "toggle_living_light",
    1: "toggle_bedroom_light",
    2: "toggle_curtain",
    3: "toggle_ac",
    4: "toggle_tv",
}

DEFAULT_HOLD_MAPPING = {
    0: "all_lights_on",
    1: "reading_scene",
    2: "movie_scene",
    3: "sleep_scene",
    4: "away_scene",
}

DEVICE_LABELS = {
    "living_light": "客厅灯",
    "bedroom_light": "卧室灯",
    "curtain": "窗帘",
    "air_conditioner": "空调",
    "television": "电视",
}


@dataclass(frozen=True)
class HomeControlEvent:
    gesture_class: Optional[int]
    action: str
    action_label: str
    source: str
    result: str
    gesture_kind: str = "single"


class SmartHomeController:
    """In-memory smart-home simulator driven by debounced gesture classes."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        minimum_score: float = 0.75,
        confirm_samples: int = 2,
        release_seconds: float = 1.0,
        mapping: Optional[dict[int, str]] = None,
        hold_seconds: float = 0.0,
        hold_mapping: Optional[dict[int, str]] = None,
    ):
        self.devices = {
            "living_light": False,
            "bedroom_light": False,
            "curtain": False,
            "air_conditioner": False,
            "television": False,
        }
        self.levels = {
            "living_brightness": 60,
            "bedroom_brightness": 60,
            "ac_temperature": 26,
            "tv_volume": 30,
        }
        self.enabled = True
        self.minimum_score = 0.75
        self.confirm_samples = 2
        self.release_seconds = 1.0
        self.hold_seconds = 0.0
        self.mapping = dict(DEFAULT_GESTURE_MAPPING)
        self.hold_mapping = {gesture: "none" for gesture in range(5)}
        self.candidate_class: Optional[int] = None
        self.candidate_samples = 0
        self.candidate_since: Optional[float] = None
        self.latched_class: Optional[int] = None
        self.invalid_since: Optional[float] = None
        self.active_class: Optional[int] = None
        self.active_since: Optional[float] = None
        self.hold_triggered = False
        self.last_observed_at: Optional[float] = None
        self.configure(
            enabled=enabled,
            minimum_score=minimum_score,
            confirm_samples=confirm_samples,
            release_seconds=release_seconds,
            mapping=mapping or DEFAULT_GESTURE_MAPPING,
            hold_seconds=hold_seconds,
            hold_mapping=hold_mapping,
        )

    def configure(
        self,
        *,
        enabled: bool,
        minimum_score: float,
        confirm_samples: int,
        release_seconds: float,
        mapping: dict[int, str],
        hold_seconds: Optional[float] = None,
        hold_mapping: Optional[dict[int, str]] = None,
    ) -> None:
        score = float(minimum_score)
        samples = int(confirm_samples)
        release = float(release_seconds)
        hold = self.hold_seconds if hold_seconds is None else float(hold_seconds)
        if not 0.0 <= score <= 1.0:
            raise ValueError("手势分数阈值必须在 0~1 之间。")
        if not 1 <= samples <= 10:
            raise ValueError("连续确认次数必须在 1~10 之间。")
        if not 0.0 <= release <= 10.0:
            raise ValueError("释放时间必须在 0~10 秒之间。")
        if not 0.0 <= hold <= 10.0:
            raise ValueError("长按时间必须在 0~10 秒之间。")
        normalized: dict[int, str] = {}
        for gesture_class in range(5):
            action = str(mapping.get(gesture_class, "none"))
            if action not in ACTION_LABELS:
                raise ValueError(f"不支持的家居动作：{action}")
            normalized[gesture_class] = action
        raw_hold_mapping = self.hold_mapping if hold_mapping is None else hold_mapping
        normalized_hold: dict[int, str] = {}
        for gesture_class in range(5):
            action = str(raw_hold_mapping.get(gesture_class, "none"))
            if action not in ACTION_LABELS:
                raise ValueError(f"不支持的长按动作：{action}")
            normalized_hold[gesture_class] = action
        self.enabled = bool(enabled)
        self.minimum_score = score
        self.confirm_samples = samples
        self.release_seconds = release
        self.hold_seconds = hold
        self.mapping = normalized
        self.hold_mapping = normalized_hold
        self.reset_gesture_state()

    def reset_gesture_state(self) -> None:
        self.candidate_class = None
        self.candidate_samples = 0
        self.candidate_since = None
        self.latched_class = None
        self.invalid_since = None
        self.active_class = None
        self.active_since = None
        self.hold_triggered = False
        self.last_observed_at = None

    def observe(
        self,
        gesture_class: int,
        score: float,
        *,
        now: Optional[float] = None,
    ) -> Optional[HomeControlEvent]:
        current = time.monotonic() if now is None else float(now)
        self.last_observed_at = current
        valid = self.enabled and 0 <= int(gesture_class) <= 4 and float(score) >= self.minimum_score
        if not valid:
            self.candidate_class = None
            self.candidate_samples = 0
            self.candidate_since = None
            if self.active_class is not None:
                if self.invalid_since is None:
                    self.invalid_since = current
                    return None
                if current - self.invalid_since < self.release_seconds:
                    return None
                gesture = self.active_class
                was_held = self.hold_triggered
                self.active_class = None
                self.active_since = None
                self.hold_triggered = False
                self.latched_class = None
                if not was_held:
                    return self.execute(
                        self.mapping.get(gesture, "none"),
                        source="gesture",
                        gesture_class=gesture,
                        gesture_kind="tap",
                    )
            if self.invalid_since is None:
                self.invalid_since = current
            if current - self.invalid_since >= self.release_seconds:
                self.latched_class = None
            return None

        gesture = int(gesture_class)
        if self.invalid_since is not None and current - self.invalid_since >= self.release_seconds:
            if self.active_class is not None:
                previous = self.active_class
                was_held = self.hold_triggered
                self.active_class = None
                self.active_since = None
                self.hold_triggered = False
                self.latched_class = None
                self.invalid_since = None
                if not was_held:
                    return self.execute(
                        self.mapping.get(previous, "none"),
                        source="gesture",
                        gesture_class=previous,
                        gesture_kind="tap",
                    )
            else:
                self.latched_class = None
        self.invalid_since = None
        if self.active_class is not None:
            if gesture != self.active_class:
                previous = self.active_class
                was_held = self.hold_triggered
                self.active_class = None
                self.active_since = None
                self.hold_triggered = False
                self.latched_class = previous
                if not was_held:
                    return self.execute(
                        self.mapping.get(previous, "none"),
                        source="gesture",
                        gesture_class=previous,
                        gesture_kind="tap",
                    )
            if (
                self.active_class is not None
                and not self.hold_triggered
                and self.active_since is not None
                and current - self.active_since >= self.hold_seconds
            ):
                self.hold_triggered = True
                self.latched_class = gesture
                return self.execute(
                    self.hold_mapping.get(gesture, "none"),
                    source="gesture",
                    gesture_class=gesture,
                    gesture_kind="hold",
                )
            if self.active_class is not None:
                return None
        if gesture == self.latched_class:
            return None
        if gesture != self.candidate_class:
            self.candidate_class = gesture
            self.candidate_samples = 1
            self.candidate_since = current
        else:
            self.candidate_samples += 1
        if self.candidate_samples < self.confirm_samples:
            return None

        candidate_since = self.candidate_since if self.candidate_since is not None else current
        self.candidate_class = None
        self.candidate_samples = 0
        self.candidate_since = None
        if self.hold_seconds > 0 and self.hold_mapping.get(gesture, "none") != "none":
            self.active_class = gesture
            self.active_since = candidate_since
            self.hold_triggered = False
            return None
        self.latched_class = gesture
        return self.execute(
            self.mapping.get(gesture, "none"),
            source="gesture",
            gesture_class=gesture,
            gesture_kind="tap",
        )

    def hold_progress(self, *, now: Optional[float] = None) -> float:
        if self.active_class is None or self.active_since is None or self.hold_seconds <= 0:
            return 0.0
        current = self.last_observed_at if now is None else float(now)
        if current is None:
            return 0.0
        if self.invalid_since is not None:
            current = min(current, self.invalid_since)
        return max(0.0, min(1.0, (current - self.active_since) / self.hold_seconds))

    def execute(
        self,
        action: str,
        *,
        source: str = "manual",
        gesture_class: Optional[int] = None,
        gesture_kind: str = "single",
    ) -> HomeControlEvent:
        if action not in ACTION_LABELS:
            raise ValueError(f"不支持的家居动作：{action}")
        if action == "toggle_living_light":
            self.devices["living_light"] = not self.devices["living_light"]
        elif action == "toggle_bedroom_light":
            self.devices["bedroom_light"] = not self.devices["bedroom_light"]
        elif action == "toggle_curtain":
            self.devices["curtain"] = not self.devices["curtain"]
        elif action == "toggle_ac":
            self.devices["air_conditioner"] = not self.devices["air_conditioner"]
        elif action == "toggle_tv":
            self.devices["television"] = not self.devices["television"]
        elif action == "living_brighter":
            self.devices["living_light"] = True
            self.levels["living_brightness"] = min(100, self.levels["living_brightness"] + 20)
        elif action == "living_dimmer":
            self.devices["living_light"] = True
            self.levels["living_brightness"] = max(20, self.levels["living_brightness"] - 20)
        elif action == "bedroom_brighter":
            self.devices["bedroom_light"] = True
            self.levels["bedroom_brightness"] = min(100, self.levels["bedroom_brightness"] + 20)
        elif action == "bedroom_dimmer":
            self.devices["bedroom_light"] = True
            self.levels["bedroom_brightness"] = max(20, self.levels["bedroom_brightness"] - 20)
        elif action == "ac_warmer":
            self.devices["air_conditioner"] = True
            self.levels["ac_temperature"] = min(30, self.levels["ac_temperature"] + 1)
        elif action == "ac_cooler":
            self.devices["air_conditioner"] = True
            self.levels["ac_temperature"] = max(16, self.levels["ac_temperature"] - 1)
        elif action == "tv_volume_up":
            self.devices["television"] = True
            self.levels["tv_volume"] = min(100, self.levels["tv_volume"] + 5)
        elif action == "tv_volume_down":
            self.devices["television"] = True
            self.levels["tv_volume"] = max(0, self.levels["tv_volume"] - 5)
        elif action == "all_lights_on":
            self.devices.update(living_light=True, bedroom_light=True)
        elif action == "all_lights_off":
            self.devices.update(living_light=False, bedroom_light=False)
        elif action == "home_scene":
            self.devices.update(
                living_light=True,
                bedroom_light=True,
                curtain=True,
                air_conditioner=True,
            )
            self.levels.update(living_brightness=80, bedroom_brightness=60, ac_temperature=24)
        elif action == "away_scene":
            for key in self.devices:
                self.devices[key] = False
            self.devices["curtain"] = False
        elif action == "movie_scene":
            self.devices.update(
                living_light=False,
                bedroom_light=False,
                curtain=False,
                air_conditioner=True,
                television=True,
            )
            self.levels.update(ac_temperature=24, tv_volume=35)
        elif action == "reading_scene":
            self.devices.update(
                living_light=True,
                bedroom_light=False,
                curtain=True,
                air_conditioner=False,
                television=False,
            )
            self.levels["living_brightness"] = 100
        elif action == "sleep_scene":
            self.devices.update(
                living_light=False,
                bedroom_light=True,
                curtain=False,
                air_conditioner=True,
                television=False,
            )
            self.levels.update(bedroom_brightness=20, ac_temperature=26)
        result = self._result_text(action)
        return HomeControlEvent(
            gesture_class=gesture_class,
            action=action,
            action_label=ACTION_LABELS[action],
            source=source,
            result=result,
            gesture_kind=gesture_kind,
        )

    def device_state_text(self, device: str) -> str:
        state = bool(self.devices[device])
        if device == "curtain":
            return "已打开" if state else "已关闭"
        if not state:
            return "已关闭"
        if device == "living_light":
            return f"已开启 · {self.levels['living_brightness']}%"
        if device == "bedroom_light":
            return f"已开启 · {self.levels['bedroom_brightness']}%"
        if device == "air_conditioner":
            return f"已开启 · {self.levels['ac_temperature']}℃"
        if device == "television":
            return f"已开启 · 音量 {self.levels['tv_volume']}"
        return "已开启"

    def _result_text(self, action: str) -> str:
        if action == "none":
            return "该手势未绑定动作"
        if action == "home_scene":
            return "客厅灯、卧室灯、窗帘和空调已开启"
        if action == "away_scene":
            return "全部设备已关闭"
        if action == "movie_scene":
            return "灯光与窗帘已关闭，空调 24℃，电视已开启"
        if action == "reading_scene":
            return "客厅灯全亮、窗帘打开，其余设备已关闭"
        if action == "sleep_scene":
            return "卧室灯 20%、空调 26℃，窗帘与电视已关闭"
        if action == "all_lights_on":
            return "客厅灯和卧室灯已开启"
        if action == "all_lights_off":
            return "客厅灯和卧室灯已关闭"
        device_by_action = {
            "toggle_living_light": "living_light",
            "toggle_bedroom_light": "bedroom_light",
            "toggle_curtain": "curtain",
            "toggle_ac": "air_conditioner",
            "toggle_tv": "television",
            "living_brighter": "living_light",
            "living_dimmer": "living_light",
            "bedroom_brighter": "bedroom_light",
            "bedroom_dimmer": "bedroom_light",
            "ac_warmer": "air_conditioner",
            "ac_cooler": "air_conditioner",
            "tv_volume_up": "television",
            "tv_volume_down": "television",
        }
        device = device_by_action[action]
        return f"{DEVICE_LABELS[device]}{self.device_state_text(device)}"
