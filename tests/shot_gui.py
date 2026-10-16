"""给 sphdl GUI 窗口截图（留档用）。

用法: python tests/shot_gui.py [输出png路径]
窗口标题匹配「微信视频号下载器」；最多等 25s 出现。

实现说明：
  · 主路径 PrintWindow(PW_RENDERFULLCONTENT)：即使窗口未置前/被遮挡也能抓到内容
  · 备路径 ImageGrab 抓窗口矩形（PrintWindow 失败时兜底）
  · pywin32 坑（v2.0.17 修）：PyCDC 没有 GetBitmapBits/Detach，位图数据必须
    从 PyCBitmap 取（bmp.GetBitmapBits(True)），DC 直接 DeleteDC 即可
"""
import ctypes
import sys
import time
from pathlib import Path

import win32con
import win32gui
import win32ui
from PIL import Image, ImageGrab

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else (
    Path(__file__).resolve().parent.parent.parent / "docs" / "screenshots" / "main.png")
TITLE_KEY = "微信视频号下载器"


def find_sphdl():
    res = []

    def cb(hwnd, _):
        try:
            if win32gui.IsWindowVisible(hwnd) and TITLE_KEY in win32gui.GetWindowText(hwnd):
                res.append((hwnd, win32gui.GetWindowText(hwnd)))
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return res


def shoot_printwindow(hwnd, path):
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w, h = r - l, b - t
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, w, h)
    save_dc.SelectObject(bmp)
    ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2)  # 2=PW_RENDERFULLCONTENT
    info = bmp.GetInfo()
    bits = bmp.GetBitmapBits(True)          # ← 从 PyCBitmap 取（PyCDC 无此方法）
    img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bits,
                           "raw", "BGRX", 0, 1)
    img.save(path)
    win32gui.DeleteObject(bmp.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)
    return img.size


def main():
    found = None
    for _ in range(50):
        found = find_sphdl()
        if found:
            break
        time.sleep(0.5)
    if not found:
        print("NOT_FOUND")
        return 1
    hwnd, title = found[0]
    print("FOUND:", title)
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    except Exception:
        pass
    time.sleep(1.2)
    try:
        size = shoot_printwindow(hwnd, OUT)
        print(f"saved(PrintWindow) {OUT} size={size[0]}x{size[1]}")
    except Exception as e:
        print("PrintWindow 失败，改用 ImageGrab:", e)
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        img = ImageGrab.grab(bbox=(l, t, r, b))
        img.save(OUT)
        print(f"saved(ImageGrab) {OUT} size={img.size[0]}x{img.size[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
