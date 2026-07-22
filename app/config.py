APP_NAME = "SSNE AI Console"
APP_VERSION = "1.3.2"
REPO_FULL_NAME = "mudongwu761-star/ssne_ai_demo"
REPO_COMMIT = "7aae0664d8e32d4a2379cd17938743d1ebc03712"

COLORS = {
    "bg": "#0B1020", "sidebar": "#0F172A", "panel": "#111B2E",
    "panel2": "#17243A", "border": "#263550", "text": "#E8EEF8",
    "muted": "#91A2BA", "primary": "#4F8CFF", "success": "#2AC49A",
    "warning": "#F0B44D", "danger": "#F06472", "cyan": "#45C8E8",
    "purple": "#A879FF", "grid": "#26344D",
}

FALL_STATUS = {
    "NO_PERSON": ("无人", "muted"),
    "NP": ("无人", "muted"),
    "LOST_TARGET": ("目标丢失", "warning"),
    "LT": ("目标丢失", "warning"),
    "TRACKING": ("跟踪中", "cyan"),
    "TR": ("跟踪中", "cyan"),
    "NORMAL": ("正常", "success"),
    "NM": ("正常", "success"),
    "PRE_FALL": ("预跌倒", "warning"),
    "PF": ("预跌倒", "warning"),
    "FALL": ("跌倒告警", "danger"),
    "FL": ("跌倒告警", "danger"),
}

POSTURE_STATUS = {
    0: ("躺卧", "warning"),
    1: ("坐姿", "cyan"),
    2: ("站立", "success"),
}

OSD_MODES = ("BASE", "HAND", "FACE", "POSE", "ALL")
GESTURES = {
    -1: "无有效手势", 0: "类别 0", 1: "类别 1", 2: "类别 2", 3: "类别 3",
    4: "类别 4", 5: "类别 5", 6: "类别 6", 7: "类别 7", 8: "类别 8",
}
