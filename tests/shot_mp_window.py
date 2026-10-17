#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""截图公众号视频窗口（人工核对界面效果）。

跑法：python tests/shot_mp_window.py
输出：E:/tmp/mp_window.png
"""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sphdl import license as lic  # noqa: E402

# 准备密钥 → 生成完整版码
sec = (ROOT / ".license_secret").read_text(encoding="utf-8").strip()
(ROOT / "sphdl" / "_lic_key.py").write_text(
    '"""tmp"""\nK = %r\n' % lic.obfuscate(bytes.fromhex(sec)), encoding="utf-8")
import importlib  # noqa: E402
importlib.reload(lic)
CODE_FULL = lic.make_code(20000, flags=lic.FLAG_ALL)

import tkinter as tk  # noqa: E402
r = tk.Tk()
r.withdraw()
r.destroy()

import sphdl.gui as G  # noqa: E402
import sphdl.config as C  # noqa: E402

tmpdir = Path(tempfile.mkdtemp(prefix="shot_mp_"))
G.RECORDS_FILE = tmpdir / "records.json"
G.CONFIG_FILE = tmpdir / "config.json"
C.PROXY_BACKUP_FILE = tmpdir / "proxy_backup.json"
(tmpdir / "config.json").write_text(json.dumps({
    "port": 8899, "download_dir": str(tmpdir / "dl"),
    "auto_proxy": False, "auto_download": False,
    "download_images": True, "natural_capture": True,
    "guide_auto_shown": True}), encoding="utf-8")

app = G.App()
app.root.deiconify()
app.root.update_idletasks()


def pump(ms=400):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        try:
            app.root.update()
        except Exception:
            break
        time.sleep(0.01)


pump(600)
app._activate_license(CODE_FULL)
pump(400)

# 截图主窗口（含公众号按钮）
import win32gui  # noqa: E402
import win32con  # noqa: E402
import win32ui  # noqa: E402
import ctypes  # noqa: E402


def shot(hwnd, out):
    left, top, right, bot = win32gui.GetClientRect(hwnd)
    w, h = right - left, bot - top
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, w, h)
    save_dc.SelectObject(bmp)
    # PrintWindow 支持后台窗口
    ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2)
    bmp.SaveBitmapFile(save_dc, str(out))
    win32gui.DeleteObject(bmp.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)


hwnds = []
def cb(hw, _):
    if win32gui.IsWindowVisible(hw) and "视频号下载器" in win32gui.GetWindowText(hw):
        hwnds.append(hw)
    return True
win32gui.EnumWindows(cb, None)

out = Path("E:/tmp")
out.mkdir(parents=True, exist_ok=True)
if hwnds:
    win32gui.ShowWindow(hwnds[0], win32con.SW_RESTORE)
    win32gui.SetWindowPos(hwnds[0], win32con.HWND_TOP, 60, 30, 1300, 860,
                          win32con.SWP_SHOWWINDOW)
    pump(500)
    shot(hwnds[0], out / "mp_main.png")
    print("主窗口截图:", out / "mp_main.png")
else:
    print("未找到主窗口")

# 打开公众号窗口并截图
app._open_mp_window()
pump(800)
hwnds2 = []
def cb2(hw, _):
    t = win32gui.GetWindowText(hw)
    if win32gui.IsWindowVisible(hw) and "公众号" in t:
        hwnds2.append(hw)
    return True
win32gui.EnumWindows(cb2, None)
if hwnds2:
    shot(hwnds2[0], out / "mp_window.png")
    print("公众号窗口截图:", out / "mp_window.png")
else:
    print("未找到公众号窗口")

pump(300)
app.root.destroy()
(ROOT / "sphdl" / "_lic_key.py").unlink(missing_ok=True)
