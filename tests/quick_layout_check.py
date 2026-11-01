"""快速验证：启动源码 GUI → PrintWindow 截图 → 测日志区高度。

用法: python tests/quick_layout_check.py
输出: 日志区像素高度 + 截图路径
"""
import ctypes
import ctypes.wintypes as wt
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(r"E:/myHermesWorkSpace/wechat-channels-downloader/sphdl")
os.chdir(ROOT)

sec = (ROOT / ".license_secret").read_text().strip()
env = dict(os.environ)
env["SPHDL_LIC_SECRET"] = sec
env["PYTHONIOENCODING"] = "utf-8"

p = subprocess.Popen([sys.executable, "main.py", "--gui"], env=env,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(11)

u = ctypes.windll.user32
g = ctypes.windll.gdi32
found = []


def cb(h, _):
    if u.IsWindowVisible(h):
        n = u.GetWindowTextLengthW(h)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(h, b, n + 1)
            if "sphdl" in b.value.lower():
                r = wt.RECT()
                u.GetWindowRect(h, ctypes.byref(r))
                found.append((h, b.value, r.right - r.left, r.bottom - r.top))
    return True


u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
if not found:
    print("❌ 无窗口")
    p.terminate()
    sys.exit(1)

hwnd, title, w, h = found[0]
print(f"窗口: {title!r}  {w}x{h}")
u.SetForegroundWindow(hwnd)
time.sleep(1.5)

hdc = u.GetWindowDC(hwnd)
mdc = g.CreateCompatibleDC(hdc)
bmp = g.CreateCompatibleBitmap(hdc, w, h)
g.SelectObject(mdc, bmp)
u.PrintWindow(hwnd, mdc, 2)


class BIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG),
                ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


bi = BIH()
bi.biSize = ctypes.sizeof(BIH)
bi.biWidth, bi.biHeight = w, -h
bi.biPlanes, bi.biBitCount, bi.biCompression = 1, 32, 0
buf = ctypes.create_string_buffer(w * h * 4)
g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
g.DeleteObject(bmp)
g.DeleteDC(mdc)
u.ReleaseDC(hwnd, hdc)

from PIL import Image  # noqa: E402
im = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1)\
         .convert("RGB")
out = pathlib.Path("C:/Users/Dancing/AppData/Local/Temp/quick_layout.png")
im.save(out)
px = im.load()

# 逐行统计深色比例（日志背景 #0b1020）
rows = []
for y in range(h):
    d = t = 0
    for x in range(0, w, 4):
        r, gg, b = px[x, y]
        t += 1
        if r < 80 and gg < 80 and b < 120:
            d += 1
    rows.append(d / max(t, 1))

segs = []
st = None
for y, dd in enumerate(rows):
    if dd > 0.35:
        if st is None:
            st = y
    else:
        if st is not None and y - st > 20:
            segs.append((st, y))
        st = None
if st is not None and h - st > 20:
    segs.append((st, h))

print(f"\n深色区段: {len(segs)} 段")
for a, b in segs:
    pct = (b - a) * 100 / h
    pos = "底部" if a > h * 0.5 else "上部"
    print(f"  y={a}~{b}  高 {b-a}px  占 {pct:.1f}%  {pos}")

bottom = [s for s in segs if s[0] > h * 0.5]
if bottom:
    a, b = bottom[-1]
    hh = b - a
    print(f"\n★ 日志区高度: {hh}px  （目标 >= 200px）")
    print(f"  {'✅ 达标' if hh >= 200 else '❌ 仍太小'}")
else:
    print("\n❌ 底部没有深色日志区")

print(f"截图: {out}")
time.sleep(1)
p.terminate()
