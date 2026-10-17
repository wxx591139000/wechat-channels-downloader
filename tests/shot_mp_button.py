#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""截图公众号按钮（未授权 / 已授权两种状态）+ 下载图片默认关。"""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sphdl import license as lic
sec = (ROOT / ".license_secret").read_text(encoding="utf-8").strip()
(ROOT / "sphdl" / "_lic_key.py").write_text(
    '"""t"""\nK = %r\n' % lic.obfuscate(bytes.fromhex(sec)), encoding="utf-8")
import importlib
importlib.reload(lic)

import tkinter as tk
rt = tk.Tk(); rt.withdraw(); rt.destroy()
import sphdl.gui as G
import sphdl.config as C

tdir = Path(tempfile.mkdtemp(prefix="shotbtn_"))
G.RECORDS_FILE = tdir / "records.json"
G.CONFIG_FILE = tdir / "config.json"
C.PROXY_BACKUP_FILE = tdir / "proxy_backup.json"
# 故意不写 download_images，验证出厂默认是 False
(tdir / "config.json").write_text(json.dumps({
    "port": 8899, "download_dir": str(tdir / "dl"),
    "auto_proxy": False, "auto_download": False,
    "natural_capture": True, "guide_auto_shown": True}), encoding="utf-8")

app = G.App()
app.root.deiconify()


def pump(ms=500):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        try:
            app.root.update()
        except Exception:
            break
        time.sleep(0.01)


pump(700)
print("download_images 默认值:", app.cfg.download_images, "（应为 False）")
print("图片开关勾选状态:", app.images_var.get(), "（应为 0/False）")

import win32gui, win32con, win32ui, ctypes


def shot(hwnd, out):
    l, t, r, b = win32gui.GetClientRect(hwnd)
    w, h = r - l, b - t
    dc = win32gui.GetWindowDC(hwnd)
    m = win32ui.CreateDCFromHandle(dc)
    s = m.CreateCompatibleDC()
    bm = win32ui.CreateBitmap()
    bm.CreateCompatibleBitmap(m, w, h)
    s.SelectObject(bm)
    ctypes.windll.user32.PrintWindow(hwnd, s.GetSafeHdc(), 2)
    bm.SaveBitmapFile(s, str(out))
    win32gui.DeleteObject(bm.GetHandle())
    s.DeleteDC(); m.DeleteDC(); win32gui.ReleaseDC(hwnd, dc)


h = []
def cb(hw, _):
    if win32gui.IsWindowVisible(hw) and "视频号下载器" in win32gui.GetWindowText(hw):
        h.append(hw)
    return True
win32gui.EnumWindows(cb, None)

out = Path("E:/tmp"); out.mkdir(exist_ok=True)
if h:
    win32gui.ShowWindow(h[0], win32con.SW_RESTORE)
    win32gui.SetWindowPos(h[0], win32con.HWND_TOP, 40, 20, 1360, 880,
                          win32con.SWP_SHOWWINDOW)
    pump(600)

    # ① 未授权状态
    shot(h[0], out / "btn_locked.png")
    print("未授权:", out / "btn_locked.png")

    # ② 激活公众号码
    app._activate_license(lic.make_code(0, product=lic.PRODUCT_MP_ARTICLE))
    pump(500)
    shot(h[0], out / "btn_unlocked.png")
    print("已授权:", out / "btn_unlocked.png")
    print("按钮文本:", app.btn_mp.cget("text"))
    print("按钮样式:", app.btn_mp.cget("style"))

pump(300)
app.root.destroy()
(ROOT / "sphdl" / "_lic_key.py").unlink(missing_ok=True)
