from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable, Optional

try:
    from PIL import Image, ImageGrab, ImageTk
except ImportError:  # The rest of the serial console remains usable without Pillow.
    Image = ImageGrab = ImageTk = None

from .commands import MAX_ZONE_POINTS
from .config import COLORS


Point = tuple[float, float]


def pillow_available() -> bool:
    return Image is not None and ImageGrab is not None and ImageTk is not None


def open_image(path: str | Path):
    if Image is None:
        raise RuntimeError("截屏与多边形编辑需要 Pillow，请先执行 pip install -r requirements.txt。")
    with Image.open(path) as source:
        return source.convert("RGB")


def _fit_rect(image_size: tuple[int, int], canvas_size: tuple[int, int], padding: int = 14):
    image_w, image_h = image_size
    canvas_w, canvas_h = canvas_size
    available_w = max(1, canvas_w - padding * 2)
    available_h = max(1, canvas_h - padding * 2)
    scale = min(available_w / image_w, available_h / image_h)
    width = max(1, int(image_w * scale))
    height = max(1, int(image_h * scale))
    left = (canvas_w - width) // 2
    top = (canvas_h - height) // 2
    return left, top, width, height


def draw_zone_preview(canvas: tk.Canvas, screenshot, points: list[Point]) -> None:
    canvas.delete("all")
    width = max(2, canvas.winfo_width())
    height = max(2, canvas.winfo_height())
    if screenshot is None or ImageTk is None:
        canvas.create_text(
            width // 2,
            height // 2,
            text="截取或导入设备画面后，可在这里预览危险区域",
            fill=COLORS["muted"],
            font=("Microsoft YaHei UI", 11),
        )
        canvas._zone_photo = None  # type: ignore[attr-defined]
        return

    left, top, draw_w, draw_h = _fit_rect(screenshot.size, (width, height))
    resized = screenshot.resize((draw_w, draw_h), Image.Resampling.LANCZOS)
    photo = ImageTk.PhotoImage(resized)
    canvas._zone_photo = photo  # type: ignore[attr-defined]
    canvas.create_image(left, top, image=photo, anchor="nw")
    if points:
        coords = [
            value
            for x, y in points
            for value in (left + x * draw_w, top + y * draw_h)
        ]
        if len(points) >= 3:
            canvas.create_polygon(
                coords,
                fill=COLORS["danger"],
                stipple="gray25",
                outline=COLORS["danger"],
                width=3,
            )
        elif len(points) >= 2:
            canvas.create_line(*coords, fill=COLORS["danger"], width=3)
        for index, (x, y) in enumerate(points, start=1):
            px, py = left + x * draw_w, top + y * draw_h
            canvas.create_oval(px - 5, py - 5, px + 5, py + 5, fill="#FFFFFF", outline=COLORS["danger"], width=2)
            canvas.create_text(px + 10, py - 10, text=str(index), fill="#FFFFFF", anchor="sw", font=("Consolas", 9, "bold"))


class ScreenRegionSelector(tk.Toplevel):
    """Transparent desktop overlay used to choose the video/screenshot region."""

    def __init__(
        self,
        master: tk.Misc,
        on_captured: Callable[[object], None],
        on_error: Callable[[str], None],
    ):
        if not pillow_available():
            raise RuntimeError("截屏与多边形编辑需要 Pillow，请先执行 pip install -r requirements.txt。")
        super().__init__(master)
        self._on_captured = on_captured
        self._on_error = on_error
        self._start: Optional[tuple[int, int]] = None
        self._rect = None

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", 0.30)
        except tk.TclError:
            pass
        self.configure(bg="#000000", cursor="crosshair")
        x = self.winfo_vrootx()
        y = self.winfo_vrooty()
        width = self.winfo_vrootwidth()
        height = self.winfo_vrootheight()
        self.geometry(f"{width}x{height}{x:+d}{y:+d}")

        self.canvas = tk.Canvas(self, bg="#000000", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(
            width // 2,
            42,
            text="拖动选择设备视频画面区域 · Esc 取消",
            fill="#FFFFFF",
            font=("Microsoft YaHei UI", 16, "bold"),
        )
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.focus_force()
        self.grab_set()

    def _press(self, event) -> None:
        self._start = (event.x, event.y)
        if self._rect is not None:
            self.canvas.delete(self._rect)
        self._rect = self.canvas.create_rectangle(
            event.x,
            event.y,
            event.x,
            event.y,
            outline="#FFFFFF",
            width=3,
        )

    def _drag(self, event) -> None:
        if self._start is not None and self._rect is not None:
            self.canvas.coords(self._rect, self._start[0], self._start[1], event.x, event.y)

    def _release(self, event) -> None:
        if self._start is None:
            return
        x1, y1 = self._start
        x2, y2 = event.x, event.y
        left, right = sorted((x1, x2))
        top, bottom = sorted((y1, y2))
        if right - left < 30 or bottom - top < 30:
            self._start = None
            if self._rect is not None:
                self.canvas.delete(self._rect)
                self._rect = None
            return
        bbox = (
            self.winfo_rootx() + left,
            self.winfo_rooty() + top,
            self.winfo_rootx() + right,
            self.winfo_rooty() + bottom,
        )
        self.grab_release()
        self.destroy()
        self.master.after(180, lambda: self._capture(bbox))

    def _capture(self, bbox: tuple[int, int, int, int]) -> None:
        try:
            screenshot = ImageGrab.grab(bbox=bbox, all_screens=True).convert("RGB")
            self._on_captured(screenshot)
        except Exception as exc:
            self._on_error(f"截屏失败：{exc}")


class ZoneEditorDialog(tk.Toplevel):
    def __init__(
        self,
        master: tk.Misc,
        screenshot,
        initial_points: list[Point],
        on_apply: Callable[[list[Point], bool], None],
    ):
        super().__init__(master)
        self.title("危险区域多边形编辑")
        self.geometry("1060x760")
        self.minsize(760, 560)
        self.configure(bg=COLORS["bg"])
        self.transient(master)
        self._screenshot = screenshot
        self._points = list(initial_points[:MAX_ZONE_POINTS])
        self._on_apply = on_apply
        self._drag_index: Optional[int] = None
        self._image_rect = (0, 0, 1, 1)
        self._photo = None

        help_text = (
            "左键空白处添加点，拖动白色圆点可调整；右键圆点删除，右键空白或 Backspace 撤销。"
            f"最多 {MAX_ZONE_POINTS} 点，至少 3 点。"
        )
        ttk.Label(self, text=help_text, style="Muted.TLabel", padding=(16, 12)).pack(fill="x")
        self.canvas = tk.Canvas(self, bg="#050912", highlightthickness=1, highlightbackground=COLORS["border"])
        self.canvas.pack(fill="both", expand=True, padx=16)
        self.canvas.bind("<Configure>", lambda _event: self._redraw())
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", lambda _event: self._stop_drag())
        self.canvas.bind("<Button-3>", self._right_click)
        self.bind("<BackSpace>", lambda _event: self._undo())
        self.bind("<Escape>", lambda _event: self.destroy())

        bar = ttk.Frame(self, style="Bg.TFrame", padding=16)
        bar.pack(fill="x")
        self.count_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.count_var, style="Muted.TLabel").pack(side="left")
        ttk.Button(bar, text="撤销一点", command=self._undo).pack(side="right", padx=(6, 0))
        ttk.Button(bar, text="清空", command=self._clear).pack(side="right", padx=(6, 0))
        ttk.Button(bar, text="保存区域", command=lambda: self._commit(False)).pack(side="right", padx=(12, 0))
        ttk.Button(bar, text="下发并启用", style="Primary.TButton", command=lambda: self._commit(True)).pack(side="right", padx=(6, 0))
        self._update_count()
        self.after_idle(self._redraw)
        self.grab_set()

    @property
    def points(self) -> list[Point]:
        return list(self._points)

    def _redraw(self) -> None:
        self.canvas.delete("all")
        canvas_size = (max(2, self.canvas.winfo_width()), max(2, self.canvas.winfo_height()))
        left, top, width, height = _fit_rect(self._screenshot.size, canvas_size)
        self._image_rect = (left, top, width, height)
        resized = self._screenshot.resize((width, height), Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(resized)
        self.canvas.create_image(left, top, image=self._photo, anchor="nw")
        if self._points:
            coords = self._canvas_coords()
            if len(self._points) >= 3:
                self.canvas.create_polygon(
                    coords,
                    fill=COLORS["danger"],
                    stipple="gray25",
                    outline=COLORS["danger"],
                    width=3,
                )
            elif len(self._points) >= 2:
                self.canvas.create_line(*coords, fill=COLORS["danger"], width=3)
            for index, (x, y) in enumerate(self._points):
                px, py = self._to_canvas(x, y)
                self.canvas.create_oval(
                    px - 7,
                    py - 7,
                    px + 7,
                    py + 7,
                    fill="#FFFFFF",
                    outline=COLORS["danger"],
                    width=3,
                )
                self.canvas.create_text(
                    px + 11,
                    py - 11,
                    text=str(index + 1),
                    fill="#FFFFFF",
                    anchor="sw",
                    font=("Consolas", 10, "bold"),
                )

    def _canvas_coords(self) -> list[float]:
        return [value for point in self._points for value in self._to_canvas(*point)]

    def _to_canvas(self, x: float, y: float) -> tuple[float, float]:
        left, top, width, height = self._image_rect
        return left + x * width, top + y * height

    def _to_normalized(self, x: float, y: float) -> Optional[Point]:
        left, top, width, height = self._image_rect
        if not (left <= x <= left + width and top <= y <= top + height):
            return None
        return (
            max(0.0, min(1.0, (x - left) / width)),
            max(0.0, min(1.0, (y - top) / height)),
        )

    def _nearest_index(self, x: float, y: float, radius: float = 14.0) -> Optional[int]:
        best_index = None
        best_distance = radius * radius
        for index, point in enumerate(self._points):
            px, py = self._to_canvas(*point)
            distance = (px - x) ** 2 + (py - y) ** 2
            if distance <= best_distance:
                best_index = index
                best_distance = distance
        return best_index

    def _press(self, event) -> None:
        nearest = self._nearest_index(event.x, event.y)
        if nearest is not None:
            self._drag_index = nearest
            return
        point = self._to_normalized(event.x, event.y)
        if point is None:
            return
        if len(self._points) >= MAX_ZONE_POINTS:
            messagebox.showwarning("点数已满", f"设备最多支持 {MAX_ZONE_POINTS} 个危险区顶点。", parent=self)
            return
        self._points.append(point)
        self._drag_index = len(self._points) - 1
        self._update_count()
        self._redraw()

    def _drag(self, event) -> None:
        if self._drag_index is None:
            return
        point = self._to_normalized(event.x, event.y)
        if point is not None:
            self._points[self._drag_index] = point
            self._redraw()

    def _stop_drag(self) -> None:
        self._drag_index = None

    def _right_click(self, event) -> None:
        nearest = self._nearest_index(event.x, event.y)
        if nearest is None:
            self._undo()
        else:
            self._points.pop(nearest)
            self._update_count()
            self._redraw()

    def _undo(self) -> None:
        if self._points:
            self._points.pop()
            self._update_count()
            self._redraw()

    def _clear(self) -> None:
        self._points.clear()
        self._update_count()
        self._redraw()

    def _update_count(self) -> None:
        self.count_var.set(f"当前 {len(self._points)} / {MAX_ZONE_POINTS} 点")

    def _commit(self, send_to_device: bool) -> None:
        if len(self._points) < 3:
            messagebox.showwarning("区域未闭合", "请至少设置 3 个顶点。", parent=self)
            return
        self._on_apply(list(self._points), send_to_device)
        self.grab_release()
        self.destroy()
