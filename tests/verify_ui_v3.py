"""UI 优化验证：启动真实 GUI → 截图 → 检查配色/布局指标。"""
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
                     cwd=str(ROOT),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(13)

u = ctypes.windll.user32
g = ctypes.windll.gdi32
found = []


def cb(h, _):
    if not u.IsWindowVisible(h):
        return True
    n = u.GetWindowTextLengthW(h)
    if n:
        b = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(h, b, n + 1)
        if "sphdl" in b.value.lower():
            r = wt.RECT()
            u.GetWindowRect(h, ctypes.byref(r))
            w, hh = r.right - r.left, r.bottom - r.top
            found.append((w * hh, h, b.value))
    return True


u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
found.sort(reverse=True)
if not found:
    print("❌ 无窗口")
    p.terminate()
    sys.exit(1)

_, hwnd, title = found[0]
u.SetForegroundWindow(hwnd)
time.sleep(2)

r = wt.RECT()
u.GetWindowRect(hwnd, ctypes.byref(r))
w, hh = r.right - r.left, r.bottom - r.top
print(f"窗口 {w}x{hh}  {title!r}")

hdc = u.GetWindowDC(hwnd)
mdc = g.CreateCompatibleDC(hdc)
bmp = g.CreateCompatibleBitmap(hdc, w, hh)
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
bi.biWidth, bi.biHeight = w, -hh
bi.biPlanes, bi.biBitCount, bi.biCompression = 1, 32, 0
buf = ctypes.create_string_buffer(w * hh * 4)
g.GetDIBits(mdc, bmp, 0, hh, buf, ctypes.byref(bi), 0)
g.DeleteObject(bmp)
g.DeleteDC(mdc)
u.ReleaseDC(hwnd, hdc)

from PIL import Image  # noqa: E402
im = Image.frombuffer("RGBA", (w, hh), buf, "raw", "BGRA", 0, 1)\
         .convert("RGB")
out = pathlib.Path("C:/Users/Dancing/AppData/Local/Temp/ui_v3.png")
im.save(out)
px = im.load()

# ① 绿色像素（Primary 按钮 #16a34a）
green = 0
# ② 浅红像素（Danger 按钮 #fee2e2）
light_red = 0
# ③ 深色区（日志）
for y in range(hh):
    for x in range(0, w, 2):
        rr, gg, bb = px[x, y]
        if rr < 90 and gg > 130 and bb < 110:
            green += 1
        elif rr > 240 and 215 < gg < 240 and 215 < bb < 240:
            light_red += 1

print(f"绿色像素(Primary): {green}  {'✅' if green > 150 else '❌'}")
print(f"浅红像素(Danger):  {light_red}  {'✅' if light_red > 150 else '❌'}")

# 日志区
rows = []
for y in range(hh):
    d = c = 0
    for x in range(0, w, 4):
        rr, gg, bb = px[x, y]
        c += 1
        if rr < 80 and gg < 80 and bb < 120:
            d += 1
    rows.append(d / max(c, 1))
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
if st is not None and hh - st > 20:
    segs.append((st, hh))
bt = [s for s in segs if s[0] > hh * 0.45]
if bt:
    a, b2 = bt[-1]
    print(f"日志区: {b2-a}px  {'✅' if b2-a >= 150 else '❌'}")

print(f"截图: {out}")
time.sleep(1)
p.terminate()
