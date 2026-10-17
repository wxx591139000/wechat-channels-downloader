#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""截图统一清单（来源列 + 筛选效果）。"""
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

tdir = Path(tempfile.mkdtemp(prefix="shot_uni_"))
G.RECORDS_FILE = tdir / "records.json"
G.CONFIG_FILE = tdir / "config.json"
C.PROXY_BACKUP_FILE = tdir / "proxy_backup.json"
(tdir / "config.json").write_text(json.dumps({
    "port": 8899, "download_dir": str(tdir / "dl"),
    "auto_proxy": False, "auto_download": False,
    "download_images": True, "natural_capture": True,
    "guide_auto_shown": True}), encoding="utf-8")

app = G.App()
app.root.deiconify()


def pump(ms=400):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        try:
            app.root.update()
        except Exception:
            break
        time.sleep(0.01)


pump(600)
app._activate_license(lic.make_code(20000, product=lic.PRODUCT_VIDEO))
pump(300)

# 造几条混合数据
store = app.store
store.items.clear(); store.order.clear(); app._row_of.clear()
rows = [
    ("视频号", "婆媳关系如何处理 #原生家庭", 12_500_000, "done"),
    ("视频号", "孩子教育的关键期 #育儿", 8_200_000, "downloading"),
    ("视频号", "家庭排序谁第一 #家庭", 15_100_000, "waiting"),
    ("公众号", "手法医生也来学高雨整体力线？ #1", 21_900_000, "done"),
    ("公众号", "手法医生也来学高雨整体力线？ #2", 18_400_000, "waiting"),
    ("公众号", "手法医生也来学高雨整体力线？ #3", 20_100_000, "failed"),
]
for i, (plat, title, size, stt) in enumerate(rows):
    rec = G.Record(
        id=f"shot{i}", title=title, url=f"http://x/{i}", decode_key="",
        size=size, source=("公众号解析" if plat == "公众号" else "bridge钩子"),
        created=f"2026-10-17 10:0{i}:00", platform=plat, status=stt,
        video_id=f"v{i}", progress=(45 if stt == "downloading" else 0),
        file=(str(tdir / f"dl/{title[:20]}.mp4") if stt == "done" else ""),
        error=("HTTPError" if stt == "failed" else ""))
    store.items[rec.id] = rec
    store.order.append(rec.id)
app._render_filtered_tree()
pump(400)

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
    shot(h[0], out / "unified_all.png")
    print("全部:", out / "unified_all.png")

# 筛选公众号
app._filter_by_platform("公众号")
pump(500)
if h:
    shot(h[0], out / "unified_mp.png")
    print("筛选公众号:", out / "unified_mp.png")

pump(300)
app.root.destroy()
(ROOT / "sphdl" / "_lic_key.py").unlink(missing_ok=True)
