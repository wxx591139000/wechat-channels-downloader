"""对 v2.9.9 真实 exe 截图验证日志区。

关键：必须取**面积最大**的 sphdl 窗口（小 tooltip 会干扰）。
"""
import ctypes
import ctypes.wintypes as wt
import time
from pathlib import Path

u = ctypes.windll.user32
g = ctypes.windll.gdi32


def all_windows():
    out = []

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
                out.append((w * hh, h, b.value, w, hh))
        return True

    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
    out.sort(reverse=True)
    return out


wins = all_windows()
print(f"找到 {len(wins)} 个 sphdl 窗口：")
for a, h, t, w, hh in wins:
    print(f"  {w}x{hh}  hwnd={h}  {t[:45]!r}")

if not wins:
    raise SystemExit("❌ 无窗口")

_, hwnd, title, w, hh = wins[0]
print(f"\n使用最大窗口: {title!r} {w}x{hh}")

u.ShowWindow(hwnd, 9)          # SW_RESTORE
u.SetForegroundWindow(hwnd)
time.sleep(2)

# 重新取尺寸（可能刚被还原）
r = wt.RECT()
u.GetWindowRect(hwnd, ctypes.byref(r))
w, hh = r.right - r.left, r.bottom - r.top
print(f"截图尺寸: {w}x{hh}")

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
out = Path("C:/Users/Dancing/AppData/Local/Temp/v299_shot.png")
im.save(out)
print(f"已存 {out}")

px = im.load()
rows = []
for y in range(hh):
    d = c = 0
    for x in range(0, w, 4):
        rr, gg, b = px[x, y]
        c += 1
        if rr < 80 and gg < 80 and b < 120:
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

print()
for a, b in segs:
    print(f"  深色段 y={a}~{b}  高 {b-a}px  占 {(b-a)*100/hh:.1f}%")
bt = [s for s in segs if s[0] > hh * 0.45]
print()
if bt:
    a, b = bt[-1]
    hpx = b - a
    print(f"★ 日志区高度 {hpx}px  →  {'✅ 达标' if hpx >= 180 else '❌ 仍太小'}")
else:
    print("❌ 底部没有深色日志区")
