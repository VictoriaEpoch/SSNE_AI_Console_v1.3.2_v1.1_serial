from __future__ import annotations
import sys
import tkinter as tk
from app.main_window import MainWindow

def main():
    if sys.platform.startswith("win"):
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root=tk.Tk(); MainWindow(root); root.mainloop()

if __name__=="__main__": main()
