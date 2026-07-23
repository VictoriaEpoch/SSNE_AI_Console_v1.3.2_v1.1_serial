from __future__ import annotations

import queue
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, Optional

from .commands import build_enroll, build_zone_rect, build_zone_set
from .config import (
    APP_NAME,
    APP_VERSION,
    COLORS,
    FALL_STATUS,
    GESTURES,
    OSD_MODES,
    POSTURE_STATUS,
    REPO_COMMIT,
)
from .connection import PortInfo, SerialConnection, list_serial_ports
from .monitoring import NightRiseMonitor, NightRiseState, SedentaryMonitor, SedentaryState
from .protocol import parse_line
from .settings import load_settings, save_settings
from .smart_home import (
    ACTION_LABELS,
    DEFAULT_GESTURE_MAPPING,
    DEFAULT_HOLD_MAPPING,
    DEVICE_LABELS,
    HomeControlEvent,
    SmartHomeController,
)
from .zone_editor import (
    ScreenRegionSelector,
    ZoneEditorDialog,
    draw_zone_preview,
    open_image,
    pillow_available,
)


def _as_bool(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "on", "true", "yes"}
    try:
        return bool(int(value or 0))
    except (TypeError, ValueError):
        return bool(value)


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return default


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


class MainWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.geometry("1400x860")
        self.root.minsize(1080, 700)
        self.root.configure(bg=COLORS["bg"])
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.connection: Optional[SerialConnection] = None
        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.current_status: Dict[str, object] = {}
        self.port_infos: Dict[str, PortInfo] = {}
        self.last_status_at = 0.0
        self.last_frame: Optional[int] = None
        self.last_frame_time = 0.0
        self.estimated_fps = 0.0
        self.log_cache: list[str] = []
        self._closing = False
        self._danger_active = False
        self._pet_danger_active = False
        self._fall_active = False
        self._last_alert_key = ""
        self._last_alert_at = 0.0

        self.user_settings = load_settings()
        threshold_minutes = _as_float(self.user_settings.get("sedentary_minutes"), 30.0)
        if threshold_minutes <= 0:
            threshold_minutes = 30.0
        sedentary_enabled = bool(self.user_settings.get("sedentary_enabled", True))
        self.sedentary_monitor = SedentaryMonitor(threshold_minutes * 60.0)
        self.sedentary_monitor.configure(sedentary_enabled, threshold_minutes * 60.0)
        self.sedentary_state = SedentaryState(False, 0.0)

        night_enabled = bool(self.user_settings.get("night_rise_enabled", True))
        night_start = str(self.user_settings.get("night_rise_start", "22:00"))
        night_end = str(self.user_settings.get("night_rise_end", "06:00"))
        night_lying_seconds = _as_float(self.user_settings.get("night_rise_lying_seconds"), 30.0)
        night_rise_seconds = _as_float(self.user_settings.get("night_rise_confirm_seconds"), 2.0)
        try:
            self.night_rise_monitor = NightRiseMonitor(
                start_time=night_start,
                end_time=night_end,
                lying_confirm_seconds=night_lying_seconds,
                rise_confirm_seconds=night_rise_seconds,
            )
            self.night_rise_monitor.configure(
                enabled=night_enabled,
                start_time=night_start,
                end_time=night_end,
                lying_confirm_seconds=night_lying_seconds,
                rise_confirm_seconds=night_rise_seconds,
            )
        except (TypeError, ValueError):
            self.night_rise_monitor = NightRiseMonitor()
            self.night_rise_monitor.configure(
                enabled=night_enabled,
                start_time="22:00",
                end_time="06:00",
                lying_confirm_seconds=30.0,
                rise_confirm_seconds=2.0,
            )
        self.night_rise_state = self.night_rise_monitor.tick()

        home_mapping = dict(DEFAULT_GESTURE_MAPPING)
        raw_home_mapping = self.user_settings.get("home_gesture_mapping")
        if isinstance(raw_home_mapping, dict):
            for key, action in raw_home_mapping.items():
                try:
                    gesture_class = int(key)
                except (TypeError, ValueError):
                    continue
                if 0 <= gesture_class <= 4 and str(action) in ACTION_LABELS:
                    home_mapping[gesture_class] = str(action)
        home_hold_mapping = dict(DEFAULT_HOLD_MAPPING)
        raw_home_hold_mapping = self.user_settings.get("home_hold_mapping")
        if isinstance(raw_home_hold_mapping, dict):
            for key, action in raw_home_hold_mapping.items():
                try:
                    gesture_class = int(key)
                except (TypeError, ValueError):
                    continue
                if 0 <= gesture_class <= 4 and str(action) in ACTION_LABELS:
                    home_hold_mapping[gesture_class] = str(action)
        try:
            self.home_controller = SmartHomeController(
                enabled=bool(self.user_settings.get("home_enabled", True)),
                minimum_score=_as_float(self.user_settings.get("home_minimum_score"), 0.75),
                confirm_samples=_as_int(self.user_settings.get("home_confirm_samples"), 2),
                release_seconds=_as_float(self.user_settings.get("home_release_seconds"), 1.0),
                mapping=home_mapping,
                hold_seconds=_as_float(self.user_settings.get("home_hold_seconds"), 2.5),
                hold_mapping=home_hold_mapping,
            )
        except (TypeError, ValueError):
            self.home_controller = SmartHomeController(
                hold_seconds=2.5,
                hold_mapping=DEFAULT_HOLD_MAPPING,
            )
        self.home_last_status_frame: Optional[int] = None
        self.zone_points = self._load_zone_points(self.user_settings.get("zone_points"))
        self.zone_screenshot = None

        self._styles()
        self._build(threshold_minutes, sedentary_enabled)
        self.refresh_ports(show_message=False)
        self.root.after(40, self._drain_events)
        self.root.after(1000, self._status_poll)
        self.root.after(500, self._update_health)

    @staticmethod
    def _load_zone_points(raw: object) -> list[tuple[float, float]]:
        if not isinstance(raw, list):
            return []
        points = []
        for item in raw[:12]:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            x, y = _as_float(item[0], -1), _as_float(item[1], -1)
            if 0 <= x <= 1 and 0 <= y <= 1:
                points.append((x, y))
        return points

    def _styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", font=("Microsoft YaHei UI", 10), background=COLORS["bg"], foreground=COLORS["text"])
        style.configure("Bg.TFrame", background=COLORS["bg"])
        style.configure("Panel.TFrame", background=COLORS["panel"])
        style.configure("Panel2.TFrame", background=COLORS["panel2"])
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"])
        style.configure("Panel.TLabel", background=COLORS["panel"], foreground=COLORS["text"])
        style.configure("Muted.TLabel", background=COLORS["bg"], foreground=COLORS["muted"])
        style.configure("PanelMuted.TLabel", background=COLORS["panel"], foreground=COLORS["muted"])
        style.configure("Panel2Muted.TLabel", background=COLORS["panel2"], foreground=COLORS["muted"])
        style.configure("Title.TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=("Microsoft YaHei UI", 20, "bold"))
        style.configure("CardTitle.TLabel", background=COLORS["panel"], foreground=COLORS["text"], font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("Value.TLabel", background=COLORS["panel"], foreground=COLORS["text"], font=("Microsoft YaHei UI", 20, "bold"))
        style.configure("TButton", background=COLORS["panel2"], foreground=COLORS["text"], padding=(12, 8), borderwidth=0)
        style.map("TButton", background=[("active", COLORS["border"])])
        style.configure("Primary.TButton", background=COLORS["primary"], foreground="#FFFFFF", font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Primary.TButton", background=[("active", "#6BA0FF")])
        style.configure("Danger.TButton", background=COLORS["danger"], foreground="#FFFFFF")
        style.configure("TEntry", fieldbackground=COLORS["panel2"], foreground=COLORS["text"], insertcolor=COLORS["text"], padding=7)
        style.configure("TCombobox", fieldbackground=COLORS["panel2"], background=COLORS["panel2"], foreground=COLORS["text"], padding=6)
        style.map("TCombobox", fieldbackground=[("readonly", COLORS["panel2"])], foreground=[("readonly", COLORS["text"])])
        style.configure("TCheckbutton", background=COLORS["panel"], foreground=COLORS["text"])
        style.map("TCheckbutton", background=[("active", COLORS["panel"])])
        style.configure("TNotebook", background=COLORS["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=COLORS["panel"], foreground=COLORS["muted"], padding=(16, 10))
        style.map("TNotebook.Tab", background=[("selected", COLORS["primary"])], foreground=[("selected", "#FFFFFF")])
        style.configure("Treeview", background=COLORS["panel"], fieldbackground=COLORS["panel"], foreground=COLORS["text"], rowheight=28)
        style.configure("Treeview.Heading", background=COLORS["panel2"], foreground=COLORS["muted"], padding=7)

    def _build(self, threshold_minutes: float, sedentary_enabled: bool) -> None:
        top = ttk.Frame(self.root, style="Panel.TFrame", padding=(18, 12))
        top.pack(fill="x")
        ttk.Label(top, text="SSNE AI CONSOLE", style="Panel.TLabel", font=("Microsoft YaHei UI", 13, "bold")).pack(side="left")
        self.link_text = tk.StringVar(value="未连接")
        self.link_label = tk.Label(top, textvariable=self.link_text, bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 10, "bold"))
        self.link_label.pack(side="right")

        shell = ttk.Frame(self.root, style="Bg.TFrame", padding=(22, 18))
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text="SSNE 多任务监测上位机", style="Title.TLabel").pack(anchor="w")
        ttk.Label(shell, text=f"参考板端 UART 协议 · 久坐 / 起夜 / 跌倒 / 危险区 / 人脸 / 手势家居 · {REPO_COMMIT[:8]}", style="Muted.TLabel").pack(anchor="w", pady=(3, 14))

        self.tabs = ttk.Notebook(shell)
        self.tabs.pack(fill="both", expand=True)
        self.dashboard = ttk.Frame(self.tabs, style="Bg.TFrame", padding=16)
        self.serial_page = ttk.Frame(self.tabs, style="Bg.TFrame", padding=16)
        self.control_page = ttk.Frame(self.tabs, style="Bg.TFrame", padding=16)
        self.safety_page = ttk.Frame(self.tabs, style="Bg.TFrame", padding=16)
        self.home_page = ttk.Frame(self.tabs, style="Bg.TFrame", padding=16)
        self.log_page = ttk.Frame(self.tabs, style="Bg.TFrame", padding=16)
        self.tabs.add(self.dashboard, text="运行总览")
        self.tabs.add(self.serial_page, text="串口连接")
        self.tabs.add(self.control_page, text="设备控制")
        self.tabs.add(self.safety_page, text="姿态控制")
        self.tabs.add(self.home_page, text="智能家居")
        self.tabs.add(self.log_page, text="日志终端")
        self._build_dashboard()
        self._build_serial_page()
        self._build_control_page()
        self._build_safety_page(threshold_minutes, sedentary_enabled)
        self._build_home_page()
        self._build_log_page()

    def _card(self, master, title: str):
        frame = ttk.Frame(master, style="Panel.TFrame", padding=16)
        ttk.Label(frame, text=title, style="CardTitle.TLabel").pack(anchor="w", pady=(0, 10))
        return frame

    def _metric(self, master, title: str):
        frame = ttk.Frame(master, style="Panel.TFrame", padding=(14, 12))
        ttk.Label(frame, text=title, style="PanelMuted.TLabel").pack(anchor="w")
        value = ttk.Label(frame, text="--", style="Value.TLabel")
        value.pack(anchor="w", pady=(4, 0))
        return frame, value

    def _build_dashboard(self) -> None:
        for column in range(5):
            self.dashboard.columnconfigure(column, weight=1)
        for index, title in enumerate(("人员状态", "姿态 / 起夜", "跌倒状态", "危险区域", "设备帧率")):
            card, value = self._metric(self.dashboard, title)
            card.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 5, 0 if index == 4 else 5))
            setattr(self, ("m_person", "m_posture", "m_fall", "m_zone", "m_fps")[index], value)

        self.alert_var = tk.StringVar(value="暂无告警")
        self.alert_banner = tk.Label(
            self.dashboard,
            textvariable=self.alert_var,
            bg=COLORS["panel2"],
            fg=COLORS["muted"],
            anchor="w",
            padx=14,
            pady=9,
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.alert_banner.grid(row=1, column=0, columnspan=5, sticky="ew", pady=(12, 0))

        left = self._card(self.dashboard, "检测状态")
        left.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(12, 0), padx=(0, 7))
        right = self._card(self.dashboard, "实时字段（含板端紧凑字段）")
        right.grid(row=2, column=2, columnspan=3, sticky="nsew", pady=(12, 0), padx=(7, 0))
        self.dashboard.rowconfigure(2, weight=1)

        self.status_labels: dict[str, ttk.Label] = {}
        rows = (
            ("face_name", "识别身份"),
            ("posture", "当前姿态"),
            ("sedentary", "连续坐姿"),
            ("night_rise", "起夜监测"),
            ("danger", "危险区人员 / 宠物"),
            ("gesture", "稳定手势"),
            ("gesture_score", "手势分数"),
            ("osd_mode", "推理模式"),
            ("frame", "帧号"),
        )
        for key, label in rows:
            row = ttk.Frame(left, style="Panel.TFrame")
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=label, style="PanelMuted.TLabel").pack(side="left")
            value = ttk.Label(row, text="--", style="Panel.TLabel", font=("Microsoft YaHei UI", 10, "bold"))
            value.pack(side="right")
            self.status_labels[key] = value

        self.field_tree = ttk.Treeview(right, columns=("field", "value"), show="headings")
        self.field_tree.heading("field", text="字段")
        self.field_tree.heading("value", text="当前值")
        self.field_tree.column("field", width=190)
        self.field_tree.column("value", width=280)
        self.field_tree.pack(fill="both", expand=True)

    def _build_serial_page(self) -> None:
        self.serial_page.columnconfigure(0, weight=1)
        self.serial_page.rowconfigure(0, weight=1)
        config = self._card(self.serial_page, "串口连接")
        config.grid(row=0, column=0, sticky="nsew")

        ttk.Label(config, text="串口", style="PanelMuted.TLabel").pack(anchor="w")
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(config, textvariable=self.port_var, state="readonly")
        self.port_combo.pack(fill="x", pady=(4, 12))
        self.port_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_port_detail())

        ttk.Label(config, text="波特率", style="PanelMuted.TLabel").pack(anchor="w")
        self.baud_var = tk.StringVar(value="115200")
        self.baud_combo = ttk.Combobox(
            config,
            textvariable=self.baud_var,
            values=("115200", "230400", "460800", "921600", "1500000"),
            state="readonly",
        )
        self.baud_combo.pack(fill="x", pady=(4, 12))

        buttons = ttk.Frame(config, style="Panel.TFrame")
        buttons.pack(fill="x")
        self.connect_btn = ttk.Button(buttons, text="连接", style="Primary.TButton", command=self.toggle_connect)
        self.connect_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.refresh_btn = ttk.Button(buttons, text="刷新串口", command=lambda: self.refresh_ports(True))
        self.refresh_btn.pack(side="left", fill="x", expand=True, padx=(4, 0))

        self.port_detail = ttk.Label(config, text="尚未检测到串口", style="PanelMuted.TLabel", wraplength=900, justify="left")
        self.port_detail.pack(anchor="w", pady=(14, 0))
        note = ttk.Frame(config, style="Panel2.TFrame", padding=14)
        note.pack(fill="x", pady=(18, 0))
        ttk.Label(
            note,
            text="115200 8-N-1，命令按行发送。程序优先使用 pyserial；未安装时在 Windows 上直接调用系统 COM 接口。连接后每秒读取 status；久坐或起夜监测需要时还会读取 debug 姿态字段。",
            style="Panel2Muted.TLabel",
            wraplength=980,
            justify="left",
        ).pack(anchor="w")

    def _build_control_page(self) -> None:
        modes = self._card(self.control_page, "推理模式")
        modes.pack(fill="x")
        row = ttk.Frame(modes, style="Panel.TFrame")
        row.pack(fill="x")
        for mode in OSD_MODES:
            ttk.Button(row, text=mode, command=lambda selected=mode: self.send_command(selected.lower())).pack(side="left", expand=True, fill="x", padx=3)

        quick = self._card(self.control_page, "显示与调试")
        quick.pack(fill="x", pady=(12, 0))
        row = ttk.Frame(quick, style="Panel.TFrame")
        row.pack(fill="x")
        actions = (
            ("背景开", "bg on"),
            ("背景关", "bg off"),
            ("关键点开", "pk on"),
            ("关键点关", "pk off"),
            ("立即扫脸", "scan"),
            ("读取状态", "status"),
            ("读取调试", "debug"),
        )
        for label, command in actions:
            ttk.Button(row, text=label, command=lambda cmd=command: self.send_command(cmd)).pack(side="left", expand=True, fill="x", padx=3)

        enroll = self._card(self.control_page, "人脸录入")
        enroll.pack(fill="x", pady=(12, 0))
        fields = ttk.Frame(enroll, style="Panel.TFrame")
        fields.pack(fill="x")
        self.enroll_id = tk.StringVar()
        self.enroll_name = tk.StringVar()
        self.enroll_frames = tk.StringVar(value="15")
        for index, (label, variable, width) in enumerate(
            (("person_id", self.enroll_id, 28), ("display_name", self.enroll_name, 28), ("采集帧数 1~120", self.enroll_frames, 12))
        ):
            box = ttk.Frame(fields, style="Panel.TFrame")
            box.pack(side="left", fill="x", expand=index < 2, padx=(0 if index == 0 else 6, 0))
            ttk.Label(box, text=label, style="PanelMuted.TLabel").pack(anchor="w")
            ttk.Entry(box, textvariable=variable, width=width).pack(fill="x", pady=(4, 0))
        ttk.Button(fields, text="提交录入", style="Primary.TButton", command=self.enroll_face).pack(side="left", padx=(10, 4), pady=(20, 0))
        ttk.Button(fields, text="取消录入", command=lambda: self.send_command("reg cancel")).pack(side="left", padx=(4, 0), pady=(20, 0))

        params = self._card(self.control_page, "状态周期与性能测试")
        params.pack(fill="x", pady=(12, 0))
        row = ttk.Frame(params, style="Panel.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="状态打印周期（帧）", style="PanelMuted.TLabel").pack(side="left")
        self.interval_var = tk.StringVar(value="120")
        ttk.Entry(row, textvariable=self.interval_var, width=10).pack(side="left", padx=(8, 4))
        ttk.Button(row, text="下发", command=self.set_interval).pack(side="left")
        ttk.Label(row, text="测试模式", style="PanelMuted.TLabel").pack(side="left", padx=(28, 4))
        self.bench_mode_var = tk.StringVar(value="each")
        ttk.Combobox(row, textvariable=self.bench_mode_var, values=("base", "face", "pose", "all", "each"), state="readonly", width=8).pack(side="left")
        self.bench_seconds_var = tk.StringVar(value="60")
        self.bench_fps_var = tk.StringVar(value="30")
        ttk.Label(row, text="秒", style="PanelMuted.TLabel").pack(side="left", padx=(12, 3))
        ttk.Entry(row, textvariable=self.bench_seconds_var, width=7).pack(side="left")
        ttk.Label(row, text="传感器 FPS", style="PanelMuted.TLabel").pack(side="left", padx=(12, 3))
        ttk.Entry(row, textvariable=self.bench_fps_var, width=7).pack(side="left")
        ttk.Button(row, text="开始测试", command=self.start_benchmark).pack(side="left", padx=(10, 4))
        ttk.Button(row, text="停止", command=lambda: self.send_command("test stop")).pack(side="left")

    def _build_safety_page(self, threshold_minutes: float, sedentary_enabled: bool) -> None:
        self.safety_page.columnconfigure(0, weight=0, minsize=420)
        self.safety_page.columnconfigure(1, weight=1)
        self.safety_page.rowconfigure(0, weight=1)

        left = ttk.Frame(self.safety_page, style="Bg.TFrame")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        posture = self._card(left, "姿态告警")
        posture.pack(fill="x")
        self.posture_tabs = ttk.Notebook(posture, height=235)
        self.posture_tabs.pack(fill="x")
        sedentary = ttk.Frame(self.posture_tabs, style="Panel.TFrame", padding=12)
        night_rise = ttk.Frame(self.posture_tabs, style="Panel.TFrame", padding=12)
        self.posture_tabs.add(sedentary, text="久坐")
        self.posture_tabs.add(night_rise, text="起夜")

        self.sedentary_enabled_var = tk.BooleanVar(value=sedentary_enabled)
        ttk.Checkbutton(
            sedentary,
            text="启用久坐告警",
            variable=self.sedentary_enabled_var,
            command=self.apply_sedentary_settings,
        ).pack(anchor="w")
        row = ttk.Frame(sedentary, style="Panel.TFrame")
        row.pack(fill="x", pady=(10, 0))
        ttk.Label(row, text="告警阈值（分钟）", style="PanelMuted.TLabel").pack(side="left")
        self.sedentary_minutes_var = tk.StringVar(value=f"{threshold_minutes:g}")
        ttk.Entry(row, textvariable=self.sedentary_minutes_var, width=10).pack(side="left", padx=8)
        ttk.Button(row, text="应用", command=self.apply_sedentary_settings).pack(side="left")
        ttk.Button(row, text="计时清零", command=self.reset_sedentary).pack(side="left", padx=(6, 0))
        self.sedentary_status_var = tk.StringVar(value="等待设备姿态数据")
        ttk.Label(sedentary, textvariable=self.sedentary_status_var, style="PanelMuted.TLabel", wraplength=340).pack(anchor="w", pady=(12, 0))
        ttk.Label(
            sedentary,
            text="久坐由板端 debug 的 SITTING 姿态连续计时；短时丢失姿态允许 5 秒缓冲。",
            style="PanelMuted.TLabel",
            wraplength=340,
            justify="left",
        ).pack(anchor="w", pady=(8, 0))

        self.night_rise_enabled_var = tk.BooleanVar(value=self.night_rise_monitor.enabled)
        ttk.Checkbutton(
            night_rise,
            text="启用起夜告警",
            variable=self.night_rise_enabled_var,
            command=self.apply_night_rise_settings,
        ).pack(anchor="w")
        schedule = ttk.Frame(night_rise, style="Panel.TFrame")
        schedule.pack(fill="x", pady=(8, 0))
        ttk.Label(schedule, text="夜间时段", style="PanelMuted.TLabel").pack(side="left")
        self.night_rise_start_var = tk.StringVar(value=NightRiseMonitor.format_clock(self.night_rise_monitor.start_minute))
        self.night_rise_end_var = tk.StringVar(value=NightRiseMonitor.format_clock(self.night_rise_monitor.end_minute))
        ttk.Entry(schedule, textvariable=self.night_rise_start_var, width=7).pack(side="left", padx=(8, 3))
        ttk.Label(schedule, text="至", style="PanelMuted.TLabel").pack(side="left")
        ttk.Entry(schedule, textvariable=self.night_rise_end_var, width=7).pack(side="left", padx=(3, 0))
        confirm = ttk.Frame(night_rise, style="Panel.TFrame")
        confirm.pack(fill="x", pady=(8, 0))
        ttk.Label(confirm, text="躺卧确认(秒)", style="PanelMuted.TLabel").pack(side="left")
        self.night_rise_lying_var = tk.StringVar(value=f"{self.night_rise_monitor.lying_confirm_seconds:g}")
        ttk.Entry(confirm, textvariable=self.night_rise_lying_var, width=7).pack(side="left", padx=(6, 10))
        ttk.Label(confirm, text="起身确认(秒)", style="PanelMuted.TLabel").pack(side="left")
        self.night_rise_confirm_var = tk.StringVar(value=f"{self.night_rise_monitor.rise_confirm_seconds:g}")
        ttk.Entry(confirm, textvariable=self.night_rise_confirm_var, width=7).pack(side="left", padx=(6, 0))
        actions = ttk.Frame(night_rise, style="Panel.TFrame")
        actions.pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="应用", command=self.apply_night_rise_settings).pack(side="left")
        ttk.Button(actions, text="重新布防", command=self.reset_night_rise).pack(side="left", padx=(6, 0))
        self.night_rise_status_var = tk.StringVar(value="等待夜间躺卧姿态")
        ttk.Label(night_rise, textvariable=self.night_rise_status_var, style="PanelMuted.TLabel", wraplength=355).pack(anchor="w", pady=(8, 0))

        mode_note = self._card(left, "姿态运行模式")
        mode_note.pack(fill="x", pady=(12, 0))
        ttk.Button(
            mode_note,
            text="进入 ALL 安全监测模式",
            style="Primary.TButton",
            command=lambda: self.send_command("all"),
        ).pack(fill="x", pady=(0, 9))
        ttk.Label(
            mode_note,
            text="同时运行久坐、起夜姿态与危险区检测需要板端处于 ALL 模式。",
            style="PanelMuted.TLabel",
            wraplength=355,
        ).pack(anchor="w", pady=(0, 8))

        right = self._card(self.safety_page, "姿态控制 > 危险区域画面与多边形")
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.zone_device_status_var = tk.StringVar(value="设备区域状态：未知")
        ttk.Label(right, textvariable=self.zone_device_status_var, style="PanelMuted.TLabel", wraplength=820).pack(anchor="w")
        buttons = ttk.Frame(right, style="Panel.TFrame")
        buttons.pack(fill="x", pady=(8, 8))
        for index, (label, command) in enumerate(
            (
                ("下发并启用", self._send_zone_points),
                ("启用", lambda: self.send_command("zone on")),
                ("停用", lambda: self.send_command("zone off")),
                ("查询", lambda: self.send_command("zone list")),
                ("测试矩形", self.send_test_zone_rect),
                ("清空区域", self.clear_zone),
            )
        ):
            style = "Primary.TButton" if index == 0 else ("Danger.TButton" if label == "清空区域" else "TButton")
            ttk.Button(buttons, text=label, style=style, command=command).pack(side="left", expand=True, fill="x", padx=3)
        self.safety_fields_var = tk.StringVar(value="dza=--  pza=--  dzh=--  pzh=--")
        ttk.Label(right, textvariable=self.safety_fields_var, style="PanelMuted.TLabel", wraplength=820, justify="left").pack(anchor="w")
        ttk.Label(
            right,
            text="先框选完整设备视频画面，再逐点绘制危险区。dza 表示人员进入，pza 表示猫/狗进入；板端按目标框底边中心判定。",
            style="PanelMuted.TLabel",
            wraplength=820,
            justify="left",
        ).pack(anchor="w", pady=(5, 8))
        self.zone_preview = tk.Canvas(right, bg="#050912", highlightthickness=1, highlightbackground=COLORS["border"], height=250)
        self.zone_preview.pack(fill="both", expand=True)
        self.zone_preview.bind("<Configure>", lambda _event: self._draw_zone_preview())
        self.zone_points_var = tk.StringVar()
        ttk.Label(right, textvariable=self.zone_points_var, style="PanelMuted.TLabel", wraplength=820).pack(anchor="w", pady=(9, 5))
        bar = ttk.Frame(right, style="Panel.TFrame")
        bar.pack(fill="x")
        ttk.Button(bar, text="截取屏幕区域", style="Primary.TButton", command=self.capture_zone_screenshot).pack(side="left", expand=True, fill="x", padx=(0, 4))
        ttk.Button(bar, text="导入截图", command=self.import_zone_screenshot).pack(side="left", expand=True, fill="x", padx=4)
        ttk.Button(bar, text="编辑多边形", command=self.edit_zone_polygon).pack(side="left", expand=True, fill="x", padx=(4, 0))
        self._update_zone_summary()

    def _build_home_page(self) -> None:
        self.home_page.columnconfigure(0, weight=0, minsize=445)
        self.home_page.columnconfigure(1, weight=1)
        self.home_page.rowconfigure(0, weight=1)

        settings_shell = ttk.Frame(self.home_page, style="Bg.TFrame")
        settings_shell.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        settings_shell.columnconfigure(0, weight=1)
        settings_shell.rowconfigure(0, weight=1)
        settings_canvas = tk.Canvas(
            settings_shell,
            width=420,
            bg=COLORS["bg"],
            highlightthickness=0,
            borderwidth=0,
        )
        settings_scrollbar = ttk.Scrollbar(settings_shell, orient="vertical", command=settings_canvas.yview)
        settings_canvas.configure(yscrollcommand=settings_scrollbar.set)
        settings_canvas.grid(row=0, column=0, sticky="nsew")
        settings_scrollbar.grid(row=0, column=1, sticky="ns")
        settings = self._card(settings_canvas, "手势控制设置")
        settings_window = settings_canvas.create_window((0, 0), window=settings, anchor="nw")
        settings.bind(
            "<Configure>",
            lambda _event: settings_canvas.configure(scrollregion=settings_canvas.bbox("all")),
        )
        settings_canvas.bind(
            "<Configure>",
            lambda event: settings_canvas.itemconfigure(settings_window, width=event.width),
        )
        settings_canvas.bind(
            "<MouseWheel>",
            lambda event: settings_canvas.yview_scroll(int(-event.delta / 120), "units"),
        )
        self.home_enabled_var = tk.BooleanVar(value=self.home_controller.enabled)
        ttk.Checkbutton(
            settings,
            text="启用手势模拟家居控制",
            variable=self.home_enabled_var,
            command=self.apply_home_settings,
        ).pack(anchor="w")
        ttk.Label(
            settings,
            text="同一个手势可分别绑定轻触和长按动作：松手执行轻触，持续保持执行长按。",
            style="PanelMuted.TLabel",
            wraplength=390,
            justify="left",
        ).pack(anchor="w", pady=(6, 12))

        tuning = ttk.Frame(settings, style="Panel.TFrame")
        tuning.pack(fill="x")
        self.home_minimum_score_var = tk.StringVar(value=f"{self.home_controller.minimum_score:g}")
        self.home_confirm_samples_var = tk.StringVar(value=str(self.home_controller.confirm_samples))
        self.home_release_seconds_var = tk.StringVar(value=f"{self.home_controller.release_seconds:g}")
        self.home_hold_seconds_var = tk.StringVar(value=f"{self.home_controller.hold_seconds:g}")
        for column, (label, variable, width) in enumerate(
            (
                ("分数阈值", self.home_minimum_score_var, 8),
                ("连续确认次数", self.home_confirm_samples_var, 8),
                ("松手释放(秒)", self.home_release_seconds_var, 8),
                ("长按(秒)", self.home_hold_seconds_var, 8),
            )
        ):
            group = ttk.Frame(tuning, style="Panel.TFrame")
            group.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 5, 0))
            tuning.columnconfigure(column, weight=1)
            ttk.Label(group, text=label, style="PanelMuted.TLabel").pack(anchor="w")
            ttk.Entry(group, textvariable=variable, width=width).pack(fill="x", pady=(4, 0))

        ttk.Separator(settings).pack(fill="x", pady=14)
        ttk.Label(settings, text="手势的轻触 / 长按映射", style="Panel.TLabel", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        self.home_action_by_label = {label: action for action, label in ACTION_LABELS.items()}
        self.home_mapping_vars: dict[int, tk.StringVar] = {}
        self.home_hold_mapping_vars: dict[int, tk.StringVar] = {}
        action_labels = tuple(ACTION_LABELS.values())
        mapping_grid = ttk.Frame(settings, style="Panel.TFrame")
        mapping_grid.pack(fill="x", pady=(7, 0))
        mapping_grid.columnconfigure(1, weight=1)
        mapping_grid.columnconfigure(2, weight=1)
        ttk.Label(mapping_grid, text="板端类别", style="PanelMuted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 7))
        ttk.Label(mapping_grid, text="轻触动作", style="PanelMuted.TLabel").grid(row=0, column=1, sticky="w", padx=(0, 5))
        ttk.Label(mapping_grid, text="长按动作", style="PanelMuted.TLabel").grid(row=0, column=2, sticky="w", padx=(5, 0))
        for gesture_class in range(5):
            row = gesture_class + 1
            ttk.Label(mapping_grid, text=GESTURES[gesture_class].split(" / ")[0], style="Panel.TLabel").grid(
                row=row, column=0, sticky="w", padx=(0, 7), pady=(5, 0)
            )
            action = self.home_controller.mapping.get(gesture_class, "none")
            variable = tk.StringVar(value=ACTION_LABELS[action])
            self.home_mapping_vars[gesture_class] = variable
            ttk.Combobox(
                mapping_grid,
                textvariable=variable,
                values=action_labels,
                state="readonly",
                width=16,
            ).grid(row=row, column=1, sticky="ew", padx=(0, 5), pady=(5, 0))
            hold_action = self.home_controller.hold_mapping.get(gesture_class, "none")
            hold_variable = tk.StringVar(value=ACTION_LABELS[hold_action])
            self.home_hold_mapping_vars[gesture_class] = hold_variable
            ttk.Combobox(
                mapping_grid,
                textvariable=hold_variable,
                values=action_labels,
                state="readonly",
                width=16,
            ).grid(row=row, column=2, sticky="ew", padx=(5, 0), pady=(5, 0))

        actions = ttk.Frame(settings, style="Panel.TFrame")
        actions.pack(fill="x", pady=(14, 0))
        ttk.Button(actions, text="应用设置", style="Primary.TButton", command=self.apply_home_settings).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(actions, text="恢复丰富预设", command=self.reset_home_mapping).pack(side="left", fill="x", expand=True, padx=(4, 0))

        modes = ttk.Frame(settings, style="Panel.TFrame")
        modes.pack(fill="x", pady=(10, 0))
        ttk.Button(modes, text="进入 HAND 模式", command=lambda: self.send_command("hand")).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(modes, text="进入 ALL 模式", command=lambda: self.send_command("all")).pack(side="left", fill="x", expand=True, padx=(4, 0))

        self.home_current_gesture_var = tk.StringVar(value="等待板端手势数据")
        self.home_trigger_status_var = tk.StringVar(value="防误触机制尚未收到样本")
        ttk.Label(settings, textvariable=self.home_current_gesture_var, style="Panel.TLabel", font=("Microsoft YaHei UI", 10, "bold"), wraplength=390).pack(anchor="w", pady=(14, 0))
        ttk.Label(settings, textvariable=self.home_trigger_status_var, style="PanelMuted.TLabel", wraplength=390, justify="left").pack(anchor="w", pady=(5, 0))
        self.home_hold_progress_var = tk.DoubleVar(value=0.0)
        ttk.Progressbar(settings, variable=self.home_hold_progress_var, maximum=100).pack(fill="x", pady=(7, 0))

        right = ttk.Frame(self.home_page, style="Bg.TFrame")
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        devices = self._card(right, "模拟设备状态")
        devices.grid(row=0, column=0, sticky="ew")
        device_grid = ttk.Frame(devices, style="Panel.TFrame")
        device_grid.pack(fill="x")
        for column in range(3):
            device_grid.columnconfigure(column, weight=1)
        self.home_device_state_labels: dict[str, tk.Label] = {}
        for index, (device, label) in enumerate(DEVICE_LABELS.items()):
            row = index // 3
            column = index % 3
            card = ttk.Frame(device_grid, style="Panel2.TFrame", padding=(12, 10))
            card.grid(
                row=row,
                column=column,
                sticky="nsew",
                padx=(0 if column == 0 else 4, 0 if column == 2 else 4),
                pady=(0 if row == 0 else 8, 0),
            )
            ttk.Label(card, text=label, style="Panel2Muted.TLabel").pack(anchor="w")
            state_label = tk.Label(
                card,
                text="--",
                bg=COLORS["panel2"],
                fg=COLORS["muted"],
                font=("Microsoft YaHei UI", 13, "bold"),
            )
            state_label.pack(anchor="w", pady=(5, 8))
            self.home_device_state_labels[device] = state_label
            action_by_device = {
                "living_light": "toggle_living_light",
                "bedroom_light": "toggle_bedroom_light",
                "curtain": "toggle_curtain",
                "air_conditioner": "toggle_ac",
                "television": "toggle_tv",
            }
            ttk.Button(
                card,
                text="手动切换",
                command=lambda selected=action_by_device[device]: self._manual_home_action(selected),
            ).pack(fill="x")

        scene_bar = ttk.Frame(devices, style="Panel.TFrame")
        scene_bar.pack(fill="x", pady=(10, 0))
        for column, (label, action) in enumerate(
            (
                ("回家", "home_scene"),
                ("离家", "away_scene"),
                ("观影", "movie_scene"),
                ("阅读", "reading_scene"),
                ("睡眠", "sleep_scene"),
            )
        ):
            scene_bar.columnconfigure(column, weight=1)
            ttk.Button(
                scene_bar,
                text=f"{label}场景",
                style="Primary.TButton" if column == 0 else "TButton",
                command=lambda selected=action: self._manual_home_action(selected),
            ).grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 4, 0))

        history = self._card(right, "控制记录（仅本次运行）")
        history.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        ttk.Label(
            history,
            text="手势动作只更改上位机中的模拟设备，不会向真实家电发送指令。",
            style="PanelMuted.TLabel",
        ).pack(anchor="w", pady=(0, 8))
        self.home_history = ttk.Treeview(history, columns=("time", "source", "action", "result"), show="headings", height=8)
        for column, title, width in (
            ("time", "时间", 90),
            ("source", "来源", 120),
            ("action", "动作", 155),
            ("result", "结果", 310),
        ):
            self.home_history.heading(column, text=title)
            self.home_history.column(column, width=width, anchor="w")
        self.home_history.pack(fill="both", expand=True)
        self._update_home_device_cards()

    def _build_log_page(self) -> None:
        bar = ttk.Frame(self.log_page, style="Bg.TFrame")
        bar.pack(fill="x", pady=(0, 8))
        ttk.Button(bar, text="清空", command=self.clear_log).pack(side="left")
        ttk.Button(bar, text="导出", command=self.export_log).pack(side="left", padx=8)
        ttk.Button(bar, text="发送 help", command=lambda: self.send_command("help")).pack(side="left")
        ttk.Button(bar, text="发送 pro", command=lambda: self.send_command("pro")).pack(side="left", padx=8)
        self.log_text = tk.Text(self.log_page, bg="#080D18", fg="#DCE6F5", insertbackground="#FFFFFF", borderwidth=0, font=("Consolas", 10), wrap="none")
        self.log_text.pack(fill="both", expand=True)
        command = ttk.Frame(self.log_page, style="Bg.TFrame")
        command.pack(fill="x", pady=(8, 0))
        self.cmd_var = tk.StringVar()
        entry = ttk.Entry(command, textvariable=self.cmd_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _event: self.manual_send())
        ttk.Button(command, text="发送", style="Primary.TButton", command=self.manual_send).pack(side="left", padx=(8, 0))

    def refresh_ports(self, show_message: bool = False) -> None:
        infos = list_serial_ports()
        self.port_infos = {item.device: item for item in infos}
        names = [item.device for item in infos]
        current = self.port_var.get().strip()
        self.port_combo.configure(values=names)
        if current in names:
            self.port_var.set(current)
        elif names:
            self.port_var.set(names[0])
        else:
            self.port_var.set("")
        self._update_port_detail()
        if show_message:
            if names:
                self._append_log(f"[CONNECTION] 已检测到 {len(names)} 个串口：{', '.join(names)}")
            else:
                messagebox.showwarning("未检测到串口", "没有检测到可用串口。请检查设备、驱动和 USB 连接。")

    def _update_port_detail(self) -> None:
        port = self.port_var.get().strip()
        info = self.port_infos.get(port)
        if info:
            detail = info.description or "串口设备"
            if info.hwid:
                detail += "\n" + str(info.hwid)
            self.port_detail.configure(text=f"{info.device}\n{detail}")
        else:
            self.port_detail.configure(text="尚未检测到串口")

    def toggle_connect(self) -> None:
        if self.connection is not None:
            self.disconnect()
            return
        port = self.port_var.get().strip()
        if not port:
            messagebox.showwarning("未选择串口", "请先插入设备并点击“刷新串口”。")
            return
        try:
            baud = int(self.baud_var.get().strip())
        except ValueError:
            messagebox.showerror("波特率错误", "波特率必须是整数。")
            return
        connection = SerialConnection(port, baud, self._queue_line, self._queue_state)
        self.connection = connection
        self.connect_btn.configure(text="断开", style="Danger.TButton")
        self._set_controls_connected(True)
        connection.start()

    def disconnect(self) -> None:
        connection, self.connection = self.connection, None
        if connection:
            connection.close()
        self.connect_btn.configure(text="连接", style="Primary.TButton")
        self._set_controls_connected(False)
        self._set_link("未连接", COLORS["muted"])
        self.sedentary_monitor.reset()
        self.night_rise_monitor.reset()
        self.night_rise_state = self.night_rise_monitor.tick()

    def _set_controls_connected(self, connected: bool) -> None:
        self.port_combo.configure(state="disabled" if connected else "readonly")
        self.baud_combo.configure(state="disabled" if connected else "readonly")
        self.refresh_btn.configure(state="disabled" if connected else "normal")

    def _queue_line(self, line: str) -> None:
        self.events.put(("line", line))

    def _queue_state(self, state: str, message: str) -> None:
        self.events.put(("state", state, message))

    def _drain_events(self) -> None:
        try:
            for _ in range(300):
                event = self.events.get_nowait()
                if event[0] == "line":
                    self._handle_line(event[1])
                else:
                    self._handle_state(event[1], event[2])
        except queue.Empty:
            pass
        if not self._closing:
            self.root.after(40, self._drain_events)

    def _handle_state(self, state: str, message: str) -> None:
        self._diag(message)
        if state == "opening":
            self._set_link("正在打开", COLORS["warning"])
        elif state == "opened":
            self._set_link("串口已打开", COLORS["cyan"])
            self.root.after(250, lambda: self.send_command("status", quiet=True))
            if self._posture_poll_required():
                self.root.after(350, lambda: self.send_command("debug", quiet=True))
        elif state == "error":
            self._set_link("连接失败", COLORS["danger"])
            messagebox.showerror("串口连接失败", message)
        elif state == "closed":
            self.connection = None
            self.connect_btn.configure(text="连接", style="Primary.TButton")
            self._set_controls_connected(False)
            self._set_link("未连接", COLORS["muted"])

    def _handle_line(self, line: str) -> None:
        self._append_log(line)
        event = parse_line(line)
        if not event:
            return
        if event.kind in {"status", "debug"}:
            self.current_status.update(event.data)
            self.last_status_at = time.monotonic()
            self._set_link("设备在线", COLORS["success"])
            if event.kind == "debug":
                self._observe_posture(event.data)
            else:
                self._observe_home_gesture(event.data)
                if not _as_bool(self.current_status.get("person_active")):
                    self.sedentary_state = self.sedentary_monitor.observe(
                        person_active=False,
                        posture_class=None,
                        posture_valid=False,
                    )
                    self.night_rise_state = self.night_rise_monitor.observe(
                        person_active=False,
                        posture_class=None,
                        posture_valid=False,
                    )
            self._check_device_alerts()
            self._update_dashboard()
        elif event.kind == "error":
            self._diag("设备返回错误：" + str(event.data.get("message")))
        elif event.kind == "warning":
            self._diag("设备警告：" + str(event.data.get("message")))
        elif event.kind == "enroll_success":
            messagebox.showinfo("录入成功", f"ID：{event.data.get('person_id', '--')}\n名称：{event.data.get('display_name', '--')}")
        elif event.kind == "pet_danger_alert":
            self._pet_danger_active = True
            self._raise_alert("宠物危险区", "检测到猫或狗进入危险区域", "pet_zone")
        elif event.kind == "pet_danger_clear":
            self._pet_danger_active = False
        elif event.kind in {"mode_ack", "interval_ack"}:
            self.current_status.update(event.data)
            self.root.after(100, lambda: self.send_command("status", quiet=True))

    def _observe_posture(self, data: dict[str, object]) -> None:
        posture_class = data.get("posture_class", data.get("sc"))
        try:
            posture = int(posture_class) if posture_class is not None else None
        except (TypeError, ValueError):
            posture = None
        valid = _as_bool(data.get("posture_valid", data.get("sv", 0)))
        self.sedentary_state = self.sedentary_monitor.observe(
            person_active=_as_bool(self.current_status.get("person_active")),
            posture_class=posture,
            posture_valid=valid,
        )
        self._process_sedentary_state(self.sedentary_state)
        self.night_rise_state = self.night_rise_monitor.observe(
            person_active=_as_bool(self.current_status.get("person_active")),
            posture_class=posture,
            posture_valid=valid,
        )
        self._process_night_rise_state(self.night_rise_state)

    def _process_sedentary_state(self, state: SedentaryState) -> None:
        self.sedentary_state = state
        if state.triggered:
            minutes = self.sedentary_monitor.threshold_seconds / 60.0
            self._raise_alert("久坐告警", f"连续坐姿已达到 {minutes:g} 分钟", "sedentary")

    def _process_night_rise_state(self, state: NightRiseState) -> None:
        self.night_rise_state = state
        if state.triggered:
            self._raise_alert("起夜告警", "夜间检测到人员由躺卧转为坐姿或站立", "night_rise")

    def _observe_home_gesture(self, data: dict[str, object]) -> None:
        frame = _as_int(data.get("frame", data.get("f", 0)))
        if frame > 0 and frame == self.home_last_status_frame:
            return
        if frame > 0:
            self.home_last_status_frame = frame
        gesture = _as_int(data.get("gesture", data.get("gc", -1)), -1)
        score = _as_float(data.get("gesture_score", data.get("gs", 0.0)))
        gesture_text = GESTURES.get(gesture, "无有效手势")
        self.home_current_gesture_var.set(f"当前：{gesture_text} · 分数 {score:.3f}")
        event = self.home_controller.observe(gesture, score)
        self.home_hold_progress_var.set(self.home_controller.hold_progress() * 100.0)
        if not self.home_controller.enabled:
            self.home_trigger_status_var.set("手势模拟控制已停用")
        elif event is not None:
            kind = "长按" if event.gesture_kind == "hold" else "轻触"
            self.home_trigger_status_var.set(f"已执行{kind}：{event.action_label}；松手后可再次触发")
        elif self.home_controller.active_class is not None:
            active = self.home_controller.active_class
            if self.home_controller.invalid_since is not None:
                self.home_trigger_status_var.set(
                    f"正在确认松手；持续 {self.home_controller.release_seconds:g} 秒后执行轻触或解除长按锁定"
                )
            elif self.home_controller.hold_triggered:
                self.home_trigger_status_var.set("长按动作已触发；请松手解除锁定")
            else:
                progress = self.home_controller.hold_progress()
                hold_action = self.home_controller.hold_mapping.get(active, "none")
                self.home_trigger_status_var.set(
                    f"已确认：现在松手执行轻触；继续保持 "
                    f"{progress * self.home_controller.hold_seconds:.1f}/{self.home_controller.hold_seconds:g} 秒执行「{ACTION_LABELS[hold_action]}」"
                )
        elif not 0 <= gesture <= 4:
            self.home_trigger_status_var.set("等待有效手势；持续松手后会解除同手势锁定")
        elif score < self.home_controller.minimum_score:
            self.home_trigger_status_var.set(f"分数低于阈值 {self.home_controller.minimum_score:g}，未计入确认")
        elif gesture == self.home_controller.latched_class:
            self.home_trigger_status_var.set("当前手势已锁定，不会重复执行")
        else:
            self.home_trigger_status_var.set(
                f"连续确认 {self.home_controller.candidate_samples}/{self.home_controller.confirm_samples}"
            )
        if event is not None:
            self._handle_home_event(event)

    def apply_home_settings(self) -> None:
        mapping = {
            gesture_class: self.home_action_by_label.get(variable.get(), "none")
            for gesture_class, variable in self.home_mapping_vars.items()
        }
        hold_mapping = {
            gesture_class: self.home_action_by_label.get(variable.get(), "none")
            for gesture_class, variable in self.home_hold_mapping_vars.items()
        }
        try:
            self.home_controller.configure(
                enabled=bool(self.home_enabled_var.get()),
                minimum_score=float(self.home_minimum_score_var.get()),
                confirm_samples=int(self.home_confirm_samples_var.get()),
                release_seconds=float(self.home_release_seconds_var.get()),
                mapping=mapping,
                hold_seconds=float(self.home_hold_seconds_var.get()),
                hold_mapping=hold_mapping,
            )
        except (TypeError, ValueError) as exc:
            messagebox.showerror("设置无效", str(exc))
            return
        self.home_trigger_status_var.set("设置已应用，等待新的手势样本")
        self._save_user_settings()
        self._append_log("[SMART_HOME] 手势映射与防误触参数已更新")

    def reset_home_mapping(self) -> None:
        for gesture_class, action in DEFAULT_GESTURE_MAPPING.items():
            self.home_mapping_vars[gesture_class].set(ACTION_LABELS[action])
        for gesture_class, action in DEFAULT_HOLD_MAPPING.items():
            self.home_hold_mapping_vars[gesture_class].set(ACTION_LABELS[action])
        self.home_hold_seconds_var.set("2.5")
        self.apply_home_settings()

    def _manual_home_action(self, action: str) -> None:
        self._handle_home_event(self.home_controller.execute(action, source="manual"))

    def _handle_home_event(self, event: HomeControlEvent) -> None:
        self._update_home_device_cards()
        if event.source == "gesture" and event.gesture_class is not None:
            gesture = GESTURES.get(event.gesture_class, f"类别 {event.gesture_class}")
            kind = "长按" if event.gesture_kind == "hold" else "轻触"
            source = f"{gesture} · {kind}"
        else:
            source = "手动"
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.home_history.insert("", 0, values=(timestamp, source, event.action_label, event.result))
        children = self.home_history.get_children()
        if len(children) > 200:
            self.home_history.delete(*children[200:])
        self._append_log(f"[SMART_HOME][{source}] {event.action_label}：{event.result}")

    def _update_home_device_cards(self) -> None:
        if not hasattr(self, "home_device_state_labels"):
            return
        for device, label in self.home_device_state_labels.items():
            active = bool(self.home_controller.devices[device])
            label.configure(
                text=self.home_controller.device_state_text(device),
                fg=COLORS["success"] if active else COLORS["muted"],
            )

    def _check_device_alerts(self) -> None:
        danger = _as_bool(self.current_status.get("dza"))
        pet_danger = _as_bool(self.current_status.get("pza"))
        fall = str(self.current_status.get("fall_status", self.current_status.get("fs", ""))).upper() in {"FALL", "FL"}
        if danger and not self._danger_active:
            self._raise_alert("危险区域", "检测到人员进入危险区域", "person_zone")
        if pet_danger and not self._pet_danger_active:
            self._raise_alert("宠物危险区", "检测到猫或狗进入危险区域", "pet_zone")
        if fall and not self._fall_active:
            self._raise_alert("跌倒告警", "设备已确认人员跌倒", "fall")
        self._danger_active = danger
        self._pet_danger_active = pet_danger
        self._fall_active = fall

    def _raise_alert(self, title: str, message: str, key: str) -> None:
        now = time.monotonic()
        if key == self._last_alert_key and now - self._last_alert_at < 2.0:
            return
        self._last_alert_key = key
        self._last_alert_at = now
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.alert_var.set(f"{timestamp}  {title}：{message}")
        self.alert_banner.configure(bg=COLORS["danger"], fg="#FFFFFF")
        self._append_log(f"[ALERT][{title}] {message}")
        try:
            self.root.bell()
        except tk.TclError:
            pass

    def send_command(self, command: str, quiet: bool = False) -> bool:
        if not self.connection:
            if not quiet:
                messagebox.showwarning("未连接", "请先打开串口。")
            return False
        if not self.connection.is_running:
            if not quiet:
                messagebox.showwarning("串口未就绪", "串口线程尚未运行或已断开。")
            return False
        self.connection.send(command)
        self._append_log("> " + command)
        return True

    def _posture_poll_required(self) -> bool:
        return self.sedentary_monitor.enabled or (
            self.night_rise_monitor.enabled and self.night_rise_monitor.is_in_schedule()
        )

    def _status_poll(self) -> None:
        if not self._closing and self.connection is not None and self.connection.is_running:
            self.send_command("status", quiet=True)
            if self._posture_poll_required():
                self.send_command("debug", quiet=True)
        if not self._closing:
            self.root.after(1000, self._status_poll)

    def _update_health(self) -> None:
        if self.connection is not None:
            stats = self.connection.stats
            self._diag_status = f"RX {stats.rx_bytes} B / {stats.rx_lines} 行，TX {stats.tx_bytes} B"
        if self.connection is not None and self.last_status_at and time.monotonic() - self.last_status_at > 3.2:
            self._set_link("串口开·无状态", COLORS["warning"])
        self._process_sedentary_state(self.sedentary_monitor.tick())
        night_tick = self.night_rise_monitor.tick()
        if not night_tick.in_schedule or not self.night_rise_state.in_schedule:
            self.night_rise_state = night_tick
        self._update_sedentary_labels()
        self._update_night_rise_label()
        if not self._closing:
            self.root.after(500, self._update_health)

    def _update_dashboard(self) -> None:
        status = self.current_status
        person = _as_bool(status.get("person_active", status.get("p", 0)))
        self.m_person.configure(text="有人" if person else "无人")

        posture_class = _as_int(status.get("posture_class", status.get("sc", -1)), -1)
        posture_valid = _as_bool(status.get("posture_valid", status.get("sv", 0)))
        posture_label, posture_color = POSTURE_STATUS.get(posture_class, ("等待姿态", "muted")) if posture_valid else ("等待姿态", "muted")
        posture_details = []
        if self.sedentary_state.active:
            posture_details.append(_format_duration(self.sedentary_state.elapsed_seconds))
        if self.night_rise_state.armed:
            posture_details.append("起夜布防")
        posture_metric = posture_label + (" · " + " · ".join(posture_details) if posture_details else "")
        self.m_posture.configure(text=posture_metric, foreground=COLORS.get(posture_color, COLORS["text"]))

        fall = str(status.get("fall_status", status.get("fs", "--")))
        fall_label, fall_color = FALL_STATUS.get(fall.upper(), (fall, "muted"))
        self.m_fall.configure(text=fall_label, foreground=COLORS.get(fall_color, COLORS["text"]))

        danger = _as_bool(status.get("dza"))
        pet_danger = _as_bool(status.get("pza"))
        if danger or pet_danger:
            zone_label = "人员+宠物" if danger and pet_danger else ("人员进入" if danger else "宠物进入")
            zone_color = COLORS["danger"]
        elif _as_bool(status.get("dz")):
            zone_label, zone_color = "监测中", COLORS["success"]
        else:
            zone_label, zone_color = "未启用", COLORS["muted"]
        self.m_zone.configure(text=zone_label, foreground=zone_color)

        frame = _as_int(status.get("frame", status.get("f", 0)))
        now = time.monotonic()
        if self.last_frame is not None and frame >= self.last_frame and now > self.last_frame_time:
            instant = (frame - self.last_frame) / (now - self.last_frame_time)
            if 0 <= instant < 500:
                self.estimated_fps = instant if self.estimated_fps <= 0 else self.estimated_fps * 0.7 + instant * 0.3
        self.last_frame = frame
        self.last_frame_time = now
        device_fps = _as_float(status.get("fps_avg", status.get("fa", 0)))
        self.m_fps.configure(text=f"{device_fps if device_fps > 0 else self.estimated_fps:.1f}")

        gesture = _as_int(status.get("gesture", status.get("gc", -1)), -1)
        values = {
            "face_name": status.get("face_name", "--"),
            "posture": posture_label,
            "sedentary": _format_duration(self.sedentary_state.elapsed_seconds) if self.sedentary_state.active else "未计时",
            "night_rise": self._night_rise_status_text(),
            "danger": f"{_as_int(status.get('dzh'))} / {_as_int(status.get('pzh'))}",
            "gesture": GESTURES.get(gesture, str(gesture)),
            "gesture_score": f"{_as_float(status.get('gesture_score', status.get('gs', 0))):.3f}",
            "osd_mode": status.get("osd_mode", status.get("om", "--")),
            "frame": frame,
        }
        for key, value in values.items():
            self.status_labels[key].configure(text=str(value))
        self.field_tree.delete(*self.field_tree.get_children())
        for key, value in status.items():
            self.field_tree.insert("", "end", values=(key, value))

        self.zone_device_status_var.set(
            f"设备区域：{'开启' if _as_bool(status.get('dz')) else '关闭'} · "
            f"{_as_int(status.get('dzn'))} 点 · 人员命中 {_as_int(status.get('dzh'))} · 宠物命中 {_as_int(status.get('pzh'))}"
        )
        self.safety_fields_var.set(
            f"dza={_as_int(status.get('dza'))}  pza={_as_int(status.get('pza'))}  "
            f"dzt={_as_int(status.get('dzt'))}  dzh={_as_int(status.get('dzh'))}  "
            f"pzt={_as_int(status.get('pzt'))}  pzh={_as_int(status.get('pzh'))}"
        )
        self._update_sedentary_labels()
        self._update_night_rise_label()

    def _update_sedentary_labels(self) -> None:
        if not self.sedentary_enabled_var.get():
            text = "久坐监测已停用"
        elif self.sedentary_state.active:
            threshold = _format_duration(self.sedentary_monitor.threshold_seconds)
            text = f"连续坐姿 {_format_duration(self.sedentary_state.elapsed_seconds)} / {threshold}"
        else:
            text = "等待有效 SITTING 姿态"
        self.sedentary_status_var.set(text)

    def _night_rise_status_text(self) -> str:
        state = self.night_rise_state
        if not self.night_rise_monitor.enabled or state.phase == "disabled":
            return "已停用"
        if not state.in_schedule or state.phase == "outside_schedule":
            start = NightRiseMonitor.format_clock(self.night_rise_monitor.start_minute)
            end = NightRiseMonitor.format_clock(self.night_rise_monitor.end_minute)
            return f"非监测时段（{start}-{end}）"
        if state.phase == "confirming_lying":
            return f"确认躺卧 {state.elapsed_seconds:.0f}/{self.night_rise_monitor.lying_confirm_seconds:g} 秒"
        if state.phase == "armed" or state.armed and state.phase in {"no_person", "waiting_posture"}:
            return "已布防，等待起身"
        if state.phase == "confirming_rise":
            return f"确认起身 {state.elapsed_seconds:.1f}/{self.night_rise_monitor.rise_confirm_seconds:g} 秒"
        if state.phase == "alerted":
            return "已触发，等待再次躺卧"
        if state.phase == "no_person":
            return "画面无人，等待躺卧"
        if state.phase == "waiting_posture":
            return "等待有效姿态"
        return "等待躺卧后布防"

    def _update_night_rise_label(self) -> None:
        if hasattr(self, "night_rise_status_var"):
            self.night_rise_status_var.set(self._night_rise_status_text())

    def enroll_face(self) -> None:
        try:
            command = build_enroll(self.enroll_id.get(), self.enroll_name.get(), int(self.enroll_frames.get()))
        except (ValueError, TypeError) as exc:
            messagebox.showerror("输入错误", str(exc))
            return
        self.send_command(command)

    def set_interval(self) -> None:
        try:
            value = int(self.interval_var.get())
        except ValueError:
            messagebox.showerror("输入错误", "打印周期必须是正整数。")
            return
        if value <= 0:
            messagebox.showerror("输入错误", "打印周期必须大于 0。")
            return
        self.send_command(f"set print_interval {value}")

    def start_benchmark(self) -> None:
        try:
            seconds = int(self.bench_seconds_var.get())
            sensor_fps = float(self.bench_fps_var.get())
            if seconds <= 0 or sensor_fps <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("输入错误", "测试秒数和传感器 FPS 必须大于 0。")
            return
        self.send_command(f"test {self.bench_mode_var.get()} {seconds} {sensor_fps:g}")

    def apply_sedentary_settings(self) -> None:
        try:
            minutes = float(self.sedentary_minutes_var.get())
            if not 0.1 <= minutes <= 1440:
                raise ValueError
        except ValueError:
            self.sedentary_enabled_var.set(self.sedentary_monitor.enabled)
            messagebox.showerror("阈值错误", "久坐阈值必须在 0.1~1440 分钟之间。")
            return
        self.sedentary_monitor.configure(self.sedentary_enabled_var.get(), minutes * 60.0)
        self.sedentary_state = SedentaryState(False, 0.0)
        self._save_user_settings()
        self._update_sedentary_labels()
        self._append_log(f"[SETTINGS] 久坐监测={'on' if self.sedentary_enabled_var.get() else 'off'}，阈值={minutes:g} 分钟")

    def reset_sedentary(self) -> None:
        self.sedentary_monitor.reset()
        self.sedentary_state = SedentaryState(False, 0.0)
        self._update_sedentary_labels()
        self._append_log("[SETTINGS] 久坐计时已清零")

    def apply_night_rise_settings(self) -> None:
        try:
            self.night_rise_monitor.configure(
                enabled=self.night_rise_enabled_var.get(),
                start_time=self.night_rise_start_var.get(),
                end_time=self.night_rise_end_var.get(),
                lying_confirm_seconds=float(self.night_rise_lying_var.get()),
                rise_confirm_seconds=float(self.night_rise_confirm_var.get()),
            )
        except (TypeError, ValueError) as exc:
            self.night_rise_enabled_var.set(self.night_rise_monitor.enabled)
            self.night_rise_start_var.set(NightRiseMonitor.format_clock(self.night_rise_monitor.start_minute))
            self.night_rise_end_var.set(NightRiseMonitor.format_clock(self.night_rise_monitor.end_minute))
            self.night_rise_lying_var.set(f"{self.night_rise_monitor.lying_confirm_seconds:g}")
            self.night_rise_confirm_var.set(f"{self.night_rise_monitor.rise_confirm_seconds:g}")
            messagebox.showerror("起夜设置错误", str(exc))
            return
        self.night_rise_state = self.night_rise_monitor.tick()
        self._save_user_settings()
        self._update_night_rise_label()
        start = NightRiseMonitor.format_clock(self.night_rise_monitor.start_minute)
        end = NightRiseMonitor.format_clock(self.night_rise_monitor.end_minute)
        self._append_log(
            f"[SETTINGS] 起夜监测={'on' if self.night_rise_monitor.enabled else 'off'}，"
            f"时段={start}-{end}，躺卧={self.night_rise_monitor.lying_confirm_seconds:g}s，"
            f"起身={self.night_rise_monitor.rise_confirm_seconds:g}s"
        )

    def reset_night_rise(self) -> None:
        self.night_rise_monitor.reset()
        self.night_rise_state = self.night_rise_monitor.tick()
        self._update_night_rise_label()
        self._append_log("[SETTINGS] 起夜监测已重置，等待重新确认躺卧")

    def capture_zone_screenshot(self) -> None:
        if not pillow_available():
            messagebox.showerror("缺少组件", "截屏与多边形编辑需要 Pillow，请执行 pip install -r requirements.txt。")
            return
        try:
            ScreenRegionSelector(self.root, self._zone_screenshot_ready, lambda error: messagebox.showerror("截屏失败", error))
        except Exception as exc:
            messagebox.showerror("截屏失败", str(exc))

    def import_zone_screenshot(self) -> None:
        path = filedialog.askopenfilename(
            title="选择设备画面截图",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp *.webp"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            screenshot = open_image(path)
        except Exception as exc:
            messagebox.showerror("图片读取失败", str(exc))
            return
        self._zone_screenshot_ready(screenshot)

    def _zone_screenshot_ready(self, screenshot) -> None:
        screenshot.thumbnail((1920, 1280))
        self.zone_screenshot = screenshot
        self._draw_zone_preview()
        self.edit_zone_polygon()

    def edit_zone_polygon(self) -> None:
        if self.zone_screenshot is None:
            messagebox.showwarning("没有截图", "请先截取屏幕中的设备画面，或导入一张设备画面截图。")
            return
        ZoneEditorDialog(self.root, self.zone_screenshot, self.zone_points, self._zone_applied)

    def _zone_applied(self, points: list[tuple[float, float]], send_to_device: bool) -> None:
        self.zone_points = points
        self._save_user_settings()
        self._update_zone_summary()
        self._draw_zone_preview()
        if send_to_device:
            self._send_zone_points()

    def _send_zone_points(self) -> None:
        try:
            command = build_zone_set(self.zone_points)
        except ValueError as exc:
            messagebox.showwarning("区域未设置", str(exc))
            return
        if self.send_command(command):
            self.send_command("zone on")
            self.root.after(200, lambda: self.send_command("zone list", quiet=True))

    def send_test_zone_rect(self) -> None:
        self.zone_points = [(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)]
        self._save_user_settings()
        self._update_zone_summary()
        self._draw_zone_preview()
        command = build_zone_rect(0.2, 0.2, 0.8, 0.8)
        if self.send_command(command):
            self.send_command("zone on")
            self.root.after(200, lambda: self.send_command("zone list", quiet=True))

    def clear_zone(self) -> None:
        if not messagebox.askyesno("清空危险区域", "确定清空设备端和本地保存的危险区域吗？"):
            return
        self.zone_points = []
        self._save_user_settings()
        self._update_zone_summary()
        self._draw_zone_preview()
        self.send_command("zone clear")

    def _draw_zone_preview(self) -> None:
        if hasattr(self, "zone_preview"):
            draw_zone_preview(self.zone_preview, self.zone_screenshot, self.zone_points)

    def _update_zone_summary(self) -> None:
        if not hasattr(self, "zone_points_var"):
            return
        if not self.zone_points:
            self.zone_points_var.set("本地区域：尚未设置")
            return
        coords = "  ".join(f"P{index + 1}({x:.3f},{y:.3f})" for index, (x, y) in enumerate(self.zone_points))
        self.zone_points_var.set(f"本地区域：{len(self.zone_points)} 点 · {coords}")

    def _save_user_settings(self) -> None:
        try:
            minutes = _as_float(self.sedentary_minutes_var.get(), 30.0) if hasattr(self, "sedentary_minutes_var") else 30.0
            save_settings(
                {
                    "sedentary_enabled": bool(self.sedentary_enabled_var.get()) if hasattr(self, "sedentary_enabled_var") else True,
                    "sedentary_minutes": minutes,
                    "night_rise_enabled": self.night_rise_monitor.enabled,
                    "night_rise_start": NightRiseMonitor.format_clock(self.night_rise_monitor.start_minute),
                    "night_rise_end": NightRiseMonitor.format_clock(self.night_rise_monitor.end_minute),
                    "night_rise_lying_seconds": self.night_rise_monitor.lying_confirm_seconds,
                    "night_rise_confirm_seconds": self.night_rise_monitor.rise_confirm_seconds,
                    "zone_points": [[x, y] for x, y in self.zone_points],
                    "home_enabled": self.home_controller.enabled,
                    "home_minimum_score": self.home_controller.minimum_score,
                    "home_confirm_samples": self.home_controller.confirm_samples,
                    "home_release_seconds": self.home_controller.release_seconds,
                    "home_hold_seconds": self.home_controller.hold_seconds,
                    "home_gesture_mapping": {
                        str(gesture_class): action
                        for gesture_class, action in self.home_controller.mapping.items()
                    },
                    "home_hold_mapping": {
                        str(gesture_class): action
                        for gesture_class, action in self.home_controller.hold_mapping.items()
                    },
                }
            )
        except OSError as exc:
            self._append_log(f"[SETTINGS][WARN] 保存设置失败：{exc}")

    def manual_send(self) -> None:
        command = self.cmd_var.get().strip()
        if self.send_command(command):
            self.cmd_var.set("")

    def _set_link(self, text: str, color: str) -> None:
        self.link_text.set(text)
        self.link_label.configure(fg=color)

    def _diag(self, text: str) -> None:
        self._append_log(f"[CONNECTION] {text}")

    def _append_log(self, text: str) -> None:
        line = f"{datetime.now():%H:%M:%S.%f}"[:-3] + "  " + text
        self.log_cache.append(line)
        if len(self.log_cache) > 10000:
            self.log_cache = self.log_cache[-8000:]
        if hasattr(self, "log_text"):
            self.log_text.insert("end", line + "\n")
            self.log_text.see("end")

    def clear_log(self) -> None:
        self.log_cache.clear()
        self.log_text.delete("1.0", "end")

    def export_log(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".log", filetypes=[("日志", "*.log"), ("文本", "*.txt")])
        if path:
            Path(path).write_text("\n".join(self.log_cache), encoding="utf-8")

    def on_close(self) -> None:
        self._closing = True
        self._save_user_settings()
        connection, self.connection = self.connection, None
        if connection:
            connection.close()
        self.root.destroy()
