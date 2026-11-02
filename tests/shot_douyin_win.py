"""截图：抖音批量下载子窗口（检查 v3.1.2 主题在子窗口里有没有白块/割裂）。

子窗口主体用的是 tk.Frame(bg="#f6f8fa") + ttk 控件；
v3.1.2 把 TLabel/TCheckbutton 背景设成白卡片色，若父容器是浅灰
就会出现一块块白斑 —— 必须用像素验证，不能只看代码。
"""
import ctypes
import ctypes.wintypes as wt
import queue
import sys
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sphdl.douyin_ui as DYUI       # noqa: E402
from tests.shot_window import capture  # noqa: E402


class FakeApp:
    def __init__(self, root):
        self.root = root
        self.dir_var = tk.StringVar(master=root, value=str(ROOT / "downloads"))
        self.ui_q = queue.Queue()

    def push_log(self, *a):
        pass


root = tk.Tk()
root.withdraw()
w = DYUI.DouyinWindow(FakeApp(root))
w.win.deiconify()
w.win.update()
time.sleep(1.2)
w.win.update()

hwnd = w.win.winfo_id()
# Tk 的 winfo_id 是子窗口句柄，PrintWindow 需要顶层窗口
u = ctypes.windll.user32
top = u.GetAncestor(hwnd, 2) or hwnd
out = ROOT / "docs/screenshots/douyin_win.png"
size = capture(top, out)
print("✅ 抖音子窗口", size, "→", out)
w.win.destroy()
root.destroy()
