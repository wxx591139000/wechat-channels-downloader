"""测量各区域当前高度占比，为重新分配做基线。"""
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
                     cwd=str(ROOT), stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
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
print(f"窗口 {w}x{hh}")

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
out = pathlib.Path("C:/Users/Dancing/AppData/Local/Temp/baseline.png")
im.save(out)
px = im.load()

# 逐行分类：白=列表, 深=日志, 绿=主按钮, 浅红=危险按钮, 灰=其他
print()
print("行类型分布（每 10 行采样）")
print("  y     白%   深%   绿%   红%   灰%   判定")
last = None
for y in range(0, hh, 10):
    wh = dk = gr = rd = 0
    tot = 0
    for x in range(0, w, 6):
        rr, gg, bb = px[x, y]
        tot += 1
        if rr > 245 and gg > 245 and bb > 245:
            wh += 1
        elif rr < 80 and gg < 80 and bb < 120:
            dk += 1
        elif rr < 90 and gg > 130 and bb < 110:
            gr += 1
        elif rr > 240 and 215 < gg < 240:
            rd += 1
    wp, dp, gp, rp = (wh*100//tot, dk*100//tot, gr*100//tot, rd*100//tot)
    tag = ("日志" if dp > 40 else "列表" if wp > 50 else
           "按钮区" if (gp + rp) > 2 else ".")
    if tag != last:
        print(f"  {y:4d} {wp:4d} {dp:4d} {gp:4d} {rp:4d}   {tag}")
        last = tag

print()
print(f"截图: {out}")
time.sleep(1)
p.terminate()
