from __future__ import annotations

import queue
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
from typing import Dict, Optional

from .config import APP_NAME, APP_VERSION, COLORS, FALL_STATUS, GESTURES, OSD_MODES, REPO_COMMIT
from .connection import PortInfo, SerialConnection, list_serial_ports
from .protocol import parse_line


class MainWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.geometry("1350x820")
        self.root.minsize(1050, 680)
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
        self.poll_enabled = True
        self.log_cache: list[str] = []
        self._closing = False

        self._styles()
        self._build()
        self.refresh_ports(show_message=False)
        self.root.after(40, self._drain_events)
        self.root.after(1000, self._status_poll)
        self.root.after(500, self._update_health)

    def _styles(self):
        s = ttk.Style()
        try: s.theme_use("clam")
        except tk.TclError: pass
        s.configure(".", font=("Microsoft YaHei UI", 10), background=COLORS["bg"], foreground=COLORS["text"])
        s.configure("Bg.TFrame", background=COLORS["bg"])
        s.configure("Panel.TFrame", background=COLORS["panel"])
        s.configure("Panel2.TFrame", background=COLORS["panel2"])
        s.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"])
        s.configure("Panel.TLabel", background=COLORS["panel"], foreground=COLORS["text"])
        s.configure("Muted.TLabel", background=COLORS["bg"], foreground=COLORS["muted"])
        s.configure("PanelMuted.TLabel", background=COLORS["panel"], foreground=COLORS["muted"])
        s.configure("Title.TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=("Microsoft YaHei UI", 20, "bold"))
        s.configure("CardTitle.TLabel", background=COLORS["panel"], foreground=COLORS["text"], font=("Microsoft YaHei UI", 12, "bold"))
        s.configure("Value.TLabel", background=COLORS["panel"], foreground=COLORS["text"], font=("Microsoft YaHei UI", 22, "bold"))
        s.configure("TButton", background=COLORS["panel2"], foreground=COLORS["text"], padding=(12, 8), borderwidth=0)
        s.map("TButton", background=[("active", COLORS["border"])])
        s.configure("Primary.TButton", background=COLORS["primary"], foreground="#fff", font=("Microsoft YaHei UI", 10, "bold"))
        s.map("Primary.TButton", background=[("active", "#6BA0FF")])
        s.configure("Danger.TButton", background=COLORS["danger"], foreground="#fff")
        s.configure("TEntry", fieldbackground=COLORS["panel2"], foreground=COLORS["text"], insertcolor=COLORS["text"], padding=7)
        s.configure("TCombobox", fieldbackground=COLORS["panel2"], background=COLORS["panel2"], foreground=COLORS["text"], padding=6)
        s.map("TCombobox", fieldbackground=[("readonly", COLORS["panel2"])], foreground=[("readonly", COLORS["text"])])
        s.configure("TNotebook", background=COLORS["bg"], borderwidth=0)
        s.configure("TNotebook.Tab", background=COLORS["panel"], foreground=COLORS["muted"], padding=(18, 10))
        s.map("TNotebook.Tab", background=[("selected", COLORS["primary"])], foreground=[("selected", "#fff")])
        s.configure("Treeview", background=COLORS["panel"], fieldbackground=COLORS["panel"], foreground=COLORS["text"], rowheight=29)
        s.configure("Treeview.Heading", background=COLORS["panel2"], foreground=COLORS["muted"], padding=7)

    def _build(self):
        top = ttk.Frame(self.root, style="Panel.TFrame", padding=(18, 12))
        top.pack(fill="x")
        ttk.Label(top, text="SSNE AI CONSOLE", style="Panel.TLabel", font=("Microsoft YaHei UI", 13, "bold")).pack(side="left")
        self.link_text = tk.StringVar(value="未连接")
        self.link_label = tk.Label(top, textvariable=self.link_text, bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 10, "bold"))
        self.link_label.pack(side="right")

        shell = ttk.Frame(self.root, style="Bg.TFrame", padding=(22, 18))
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text="仓库同步上位机", style="Title.TLabel").pack(anchor="w")
        ttk.Label(shell, text=f"v1.1 串口连接方式 · 对应仓库提交 {REPO_COMMIT[:8]}", style="Muted.TLabel").pack(anchor="w", pady=(3, 14))

        self.tabs = ttk.Notebook(shell)
        self.tabs.pack(fill="both", expand=True)
        self.dashboard = ttk.Frame(self.tabs, style="Bg.TFrame", padding=16)
        self.serial_page = ttk.Frame(self.tabs, style="Bg.TFrame", padding=16)
        self.enroll_page = ttk.Frame(self.tabs, style="Bg.TFrame", padding=16)
        self.log_page = ttk.Frame(self.tabs, style="Bg.TFrame", padding=16)
        self.tabs.add(self.dashboard, text="运行总览")
        self.tabs.add(self.serial_page, text="串口连接")
        self.tabs.add(self.enroll_page, text="人脸与控制")
        self.tabs.add(self.log_page, text="日志终端")
        self._build_dashboard()
        self._build_serial_page()
        self._build_enroll_page()
        self._build_log_page()

    def _card(self, master, title):
        f = ttk.Frame(master, style="Panel.TFrame", padding=16)
        ttk.Label(f, text=title, style="CardTitle.TLabel").pack(anchor="w", pady=(0, 10))
        return f

    def _metric(self, master, title):
        f = ttk.Frame(master, style="Panel.TFrame", padding=(16, 13))
        ttk.Label(f, text=title, style="PanelMuted.TLabel").pack(anchor="w")
        v = ttk.Label(f, text="--", style="Value.TLabel")
        v.pack(anchor="w", pady=(4, 0))
        return f, v

    def _build_dashboard(self):
        self.dashboard.columnconfigure((0,1,2,3), weight=1)
        for i, title in enumerate(("人员状态", "人脸 / 手", "跌倒状态", "估算帧率")):
            card, value = self._metric(self.dashboard, title)
            card.grid(row=0, column=i, sticky="ew", padx=(0 if i==0 else 6, 0 if i==3 else 6))
            setattr(self, ("m_person","m_counts","m_fall","m_fps")[i], value)

        left = self._card(self.dashboard, "检测状态")
        left.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(14,0), padx=(0,7))
        right = self._card(self.dashboard, "实时字段")
        right.grid(row=1, column=2, columnspan=2, sticky="nsew", pady=(14,0), padx=(7,0))
        self.dashboard.rowconfigure(1, weight=1)
        self.dashboard.columnconfigure((0,1,2,3), weight=1)

        self.status_labels = {}
        for key, label in (("face_name","识别身份"),("gesture","稳定手势"),("gesture_score","手势分数"),("osd_mode","OSD 模式"),("cfg_print_interval","自动打印周期"),("frame","帧号")):
            row = ttk.Frame(left, style="Panel.TFrame")
            row.pack(fill="x", pady=7)
            ttk.Label(row, text=label, style="PanelMuted.TLabel").pack(side="left")
            val = ttk.Label(row, text="--", style="Panel.TLabel", font=("Microsoft YaHei UI",10,"bold"))
            val.pack(side="right")
            self.status_labels[key] = val

        self.field_tree = ttk.Treeview(right, columns=("field","value"), show="headings")
        self.field_tree.heading("field", text="字段")
        self.field_tree.heading("value", text="当前值")
        self.field_tree.column("field", width=180)
        self.field_tree.column("value", width=240)
        self.field_tree.pack(fill="both", expand=True)

    def _build_serial_page(self):
        page = self.serial_page
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)

        cfg = self._card(page, "串口连接")
        cfg.grid(row=0, column=0, sticky="nsew")

        ttk.Label(cfg, text="串口", style="PanelMuted.TLabel").pack(anchor="w")
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(cfg, textvariable=self.port_var, state="readonly")
        self.port_combo.pack(fill="x", pady=(4, 12))
        self.port_combo.bind("<<ComboboxSelected>>", lambda _e: self._update_port_detail())

        ttk.Label(cfg, text="波特率", style="PanelMuted.TLabel").pack(anchor="w")
        self.baud_var = tk.StringVar(value="115200")
        self.baud_combo = ttk.Combobox(
            cfg,
            textvariable=self.baud_var,
            values=("115200", "230400", "460800", "921600", "1500000"),
            state="readonly",
        )
        self.baud_combo.pack(fill="x", pady=(4, 12))

        buttons = ttk.Frame(cfg, style="Panel.TFrame")
        buttons.pack(fill="x")
        self.connect_btn = ttk.Button(
            buttons,
            text="连接",
            style="Primary.TButton",
            command=self.toggle_connect,
        )
        self.connect_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.refresh_btn = ttk.Button(
            buttons,
            text="刷新串口",
            command=lambda: self.refresh_ports(True),
        )
        self.refresh_btn.pack(side="left", fill="x", expand=True, padx=(4, 0))

        self.port_detail = ttk.Label(
            cfg,
            text="尚未检测到串口",
            style="PanelMuted.TLabel",
            wraplength=760,
            justify="left",
        )
        self.port_detail.pack(anchor="w", pady=(14, 0))

        note = ttk.Frame(cfg, style="Panel2.TFrame", padding=14)
        note.pack(fill="x", pady=(18, 0))
        ttk.Label(
            note,
            text="连接方式与最初 v1.1 相同：固定 8-N-1，命令使用 LF 换行。程序优先使用 pyserial；未安装时在 Windows 上直接调用系统 COM 接口。",
            style="PanelMuted.TLabel",
            wraplength=820,
            justify="left",
        ).pack(anchor="w")

    def _build_enroll_page(self):
        left=self._card(self.enroll_page,"OSD 模式")
        left.pack(fill="x")
        row=ttk.Frame(left,style="Panel.TFrame"); row.pack(fill="x")
        for mode in OSD_MODES:
            ttk.Button(row,text=mode,command=lambda m=mode:self.send_command(f"mode {m.lower()}")).pack(side="left",expand=True,fill="x",padx=3)
        ctrl=self._card(self.enroll_page,"人脸录入")
        ctrl.pack(fill="x",pady=(14,0))
        fields=ttk.Frame(ctrl,style="Panel.TFrame"); fields.pack(fill="x")
        self.enroll_id=tk.StringVar(); self.enroll_name=tk.StringVar()
        for i,(label,var) in enumerate((("person_id",self.enroll_id),("display_name",self.enroll_name))):
            box=ttk.Frame(fields,style="Panel.TFrame"); box.pack(side="left",fill="x",expand=True,padx=(0 if i==0 else 6,6 if i==0 else 0))
            ttk.Label(box,text=label,style="PanelMuted.TLabel").pack(anchor="w")
            ttk.Entry(box,textvariable=var).pack(fill="x",pady=(4,0))
        actions=ttk.Frame(ctrl,style="Panel.TFrame"); actions.pack(fill="x",pady=(12,0))
        ttk.Button(actions,text="提交录入",style="Primary.TButton",command=self.enroll_face).pack(side="left",expand=True,fill="x",padx=(0,4))
        ttk.Button(actions,text="取消录入",command=lambda:self.send_command("cancel_enroll")).pack(side="left",expand=True,fill="x",padx=4)
        ttk.Button(actions,text="读取状态",command=lambda:self.send_command("status")).pack(side="left",expand=True,fill="x",padx=(4,0))
        param=self._card(self.enroll_page,"状态打印周期")
        param.pack(fill="x",pady=(14,0))
        self.interval_var=tk.StringVar(value="120")
        ttk.Entry(param,textvariable=self.interval_var).pack(side="left",fill="x",expand=True)
        ttk.Button(param,text="下发",command=self.set_interval).pack(side="left",padx=(8,0))

    def _build_log_page(self):
        bar=ttk.Frame(self.log_page,style="Bg.TFrame"); bar.pack(fill="x",pady=(0,8))
        ttk.Button(bar,text="清空",command=self.clear_log).pack(side="left")
        ttk.Button(bar,text="导出",command=self.export_log).pack(side="left",padx=8)
        ttk.Button(bar,text="发送 help",command=lambda:self.send_command("help")).pack(side="left")
        self.log_text=tk.Text(self.log_page,bg="#080D18",fg="#DCE6F5",insertbackground="#fff",borderwidth=0,font=("Consolas",10),wrap="none")
        self.log_text.pack(fill="both",expand=True)
        cmd=ttk.Frame(self.log_page,style="Bg.TFrame"); cmd.pack(fill="x",pady=(8,0))
        self.cmd_var=tk.StringVar(); entry=ttk.Entry(cmd,textvariable=self.cmd_var); entry.pack(side="left",fill="x",expand=True); entry.bind("<Return>",lambda _e:self.manual_send())
        ttk.Button(cmd,text="发送",style="Primary.TButton",command=self.manual_send).pack(side="left",padx=(8,0))

    def refresh_ports(self, show_message=False):
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

    def _update_port_detail(self):
        port = self.port_var.get().strip()
        info = self.port_infos.get(port)
        if info:
            detail = info.description or "串口设备"
            if info.hwid:
                detail += "\n" + str(info.hwid)
            self.port_detail.configure(text=str(info.device) + "\n" + detail)
        else:
            self.port_detail.configure(text="尚未检测到串口")

    def toggle_connect(self):
        if self.connection is not None:
            self.disconnect(); return
        port=self.port_var.get().strip()
        if not port:
            messagebox.showwarning("未选择串口", "请先插入设备并点击“刷新串口”。")
            return
        try: baud=int(self.baud_var.get().strip())
        except ValueError:
            messagebox.showerror("波特率错误","波特率必须是整数。"); return
        conn = SerialConnection(port, baud, self._queue_line, self._queue_state)
        self.connection=conn
        self.connect_btn.configure(text="断开", style="Danger.TButton")
        self._set_controls_connected(True)
        conn.start()

    def disconnect(self):
        conn,self.connection=self.connection,None
        if conn: conn.close()
        self.connect_btn.configure(text="连接", style="Primary.TButton")
        self._set_controls_connected(False)
        self._set_link("未连接",COLORS["muted"])

    def _set_controls_connected(self, connected):
        self.port_combo.configure(state="disabled" if connected else "readonly")
        self.baud_combo.configure(state="disabled" if connected else "readonly")
        self.refresh_btn.configure(state="disabled" if connected else "normal")

    def _queue_line(self,line): self.events.put(("line",line))
    def _queue_state(self,state,message): self.events.put(("state",state,message))

    def _drain_events(self):
        try:
            for _ in range(300):
                event=self.events.get_nowait()
                if event[0]=="line": self._handle_line(event[1])
                else: self._handle_state(event[1],event[2])
        except queue.Empty: pass
        if not self._closing: self.root.after(40,self._drain_events)

    def _handle_state(self,state,message):
        self._diag(message)
        if state=="opening": self._set_link("正在打开",COLORS["warning"])
        elif state=="opened":
            self._set_link("串口已打开",COLORS["cyan"])
            self.root.after(250,lambda:self.send_command("status",quiet=True))
        elif state=="error":
            self._set_link("连接失败",COLORS["danger"])
            messagebox.showerror("串口连接失败",message)
        elif state=="closed":
            # closed 只会由当前连接线程发出；无论正常断开还是打开失败，都恢复 UI。
            self.connection=None
            self.connect_btn.configure(text="连接", style="Primary.TButton")
            self._set_controls_connected(False)
            self._set_link("未连接",COLORS["muted"])

    def _handle_line(self,line):
        self._append_log(line)
        ev=parse_line(line)
        if not ev: return
        if ev.kind=="status":
            self.current_status.update(ev.data); self.last_status_at=time.monotonic(); self._set_link("设备在线",COLORS["success"]); self._update_dashboard()
        elif ev.kind=="error": self._diag("设备返回错误："+str(ev.data.get("message")))
        elif ev.kind=="enroll_success": messagebox.showinfo("录入成功",f"ID：{ev.data.get('person_id','--')}\n名称：{ev.data.get('display_name','--')}")
        elif ev.kind in {"mode_ack","interval_ack"}: self.root.after(100,lambda:self.send_command("status",quiet=True))

    def send_command(self,command,quiet=False):
        if not self.connection:
            if not quiet: messagebox.showwarning("未连接","请先打开串口。")
            return False
        if not self.connection.is_running:
            if not quiet: messagebox.showwarning("串口未就绪","串口线程尚未运行或已断开。")
            return False
        self.connection.send(command); self._append_log("> "+command); return True

    def _status_poll(self):
        if not self._closing and self.connection is not None and self.connection.is_running:
            self.send_command("status",quiet=True)
        if not self._closing: self.root.after(1000,self._status_poll)

    def _update_health(self):
        if self.connection is not None:
            rx=self.connection.stats.rx_bytes; tx=self.connection.stats.tx_bytes; lines=self.connection.stats.rx_lines
            self._diag_status=f"RX {rx} B / {lines} 行，TX {tx} B"
        if self.connection is not None and self.last_status_at and time.monotonic()-self.last_status_at>3.2:
            self._set_link("串口开·无状态",COLORS["warning"])
        if not self._closing: self.root.after(500,self._update_health)

    def _update_dashboard(self):
        s=self.current_status
        person=bool(int(s.get("person_active",0) or 0)); face=int(s.get("face",0) or 0); hand=int(s.get("hand",0) or 0)
        fall=str(s.get("fall_status","--")); flabel,color=FALL_STATUS.get(fall,(fall,"muted"))
        self.m_person.configure(text="有人" if person else "无人")
        self.m_counts.configure(text=f"{face} / {hand}")
        self.m_fall.configure(text=flabel,foreground=COLORS.get(color,COLORS["text"]))
        frame=int(s.get("frame",0) or 0); now=time.monotonic()
        if self.last_frame is not None and frame>=self.last_frame and now>self.last_frame_time:
            instant=(frame-self.last_frame)/(now-self.last_frame_time)
            if 0<=instant<500: self.estimated_fps=instant if self.estimated_fps<=0 else self.estimated_fps*0.7+instant*0.3
        self.last_frame=frame; self.last_frame_time=now
        self.m_fps.configure(text=f"{self.estimated_fps:.1f}")
        gesture=int(s.get("gesture",-1) or -1)
        values={
            "face_name":s.get("face_name","--"), "gesture":GESTURES.get(gesture,str(gesture)),
            "gesture_score":f"{float(s.get('gesture_score',0) or 0):.3f}", "osd_mode":s.get("osd_mode","--"),
            "cfg_print_interval":s.get("cfg_print_interval","--"), "frame":frame,
        }
        for k,v in values.items(): self.status_labels[k].configure(text=str(v))
        self.field_tree.delete(*self.field_tree.get_children())
        for k,v in s.items(): self.field_tree.insert("","end",values=(k,v))

    def enroll_face(self):
        pid=self.enroll_id.get().strip(); name=self.enroll_name.get().strip()
        if not pid or not name or " " in pid or " " in name:
            messagebox.showerror("输入错误","person_id 和 display_name 均不能为空，也不能包含空格。"); return
        self.send_command(f"enroll {pid} {name}")

    def set_interval(self):
        try: value=int(self.interval_var.get())
        except ValueError: messagebox.showerror("输入错误","打印周期必须是正整数。"); return
        if value<=0: messagebox.showerror("输入错误","打印周期必须大于 0。"); return
        self.send_command(f"set print_interval {value}")

    def manual_send(self):
        cmd=self.cmd_var.get().strip()
        if self.send_command(cmd): self.cmd_var.set("")

    def _set_link(self,text,color): self.link_text.set(text); self.link_label.configure(fg=color)
    def _diag(self, text):
        self._append_log(f"[CONNECTION] {text}")
    def _append_log(self,text):
        line=f"{datetime.now():%H:%M:%S.%f}"[:-3]+"  "+text; self.log_cache.append(line)
        if len(self.log_cache)>10000: self.log_cache=self.log_cache[-8000:]
        if hasattr(self,"log_text"): self.log_text.insert("end",line+"\n"); self.log_text.see("end")
    def clear_log(self): self.log_cache.clear(); self.log_text.delete("1.0","end")
    def export_log(self):
        path=filedialog.asksaveasfilename(defaultextension=".log",filetypes=[("日志","*.log"),("文本","*.txt")])
        if path: Path(path).write_text("\n".join(self.log_cache),encoding="utf-8")

    def on_close(self):
        self._closing=True
        conn,self.connection=self.connection,None
        if conn: conn.close()
        self.root.destroy()
