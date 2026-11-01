"""v2.9.7 真实 exe 验证：日志框高度 + √ 符号 + 授权显示。

用 Win32 PrintWindow 截图（ImageGrab 在此环境截不到 Tk），
再读像素判断：
  L1  exe 能启动且不崩
  L2  ★日志框高度（深色区域）>= 200px（16 行 ≈ 280px）
  L3  ★授权状态可见（不该是"未授权"红条占满）
  L4  视频列表区仍可见
"""
import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE_DIR = ROOT / "dist" / "v2.9.7" / "微信视频号下载器"
EXE = EXE_DIR / "微信视频号下载器.exe"
if not EXE.exists():
    print(f"❌ 找不到 {EXE}")
    sys.exit(1)

_u = ctypes.windll.user32
_g = ctypes.windll.gdi32

print(f"启动 {EXE_DIR.parent.name}")
p = subprocess.Popen([str(EXE)], cwd=str(EXE_DIR))
time.sleep(14)


def find_main(keyword="sphdl v", timeout=25):
    out = []

    def cb(h, _):
        if not _u.IsWindowVisible(h):
            return True
        n = _u.GetWindowTextLengthW(h)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            _u.GetWindowTextW(h, b, n + 1)
            if keyword in b.value:
                r = wt.RECT()
                _u.GetWindowRect(h, ctypes.byref(r))
                w, hh = r.right - r.left, r.bottom - r.top
                if w > 400 and hh > 300:
                    out.append((w * hh, h, b.value, w, hh))
        return True

    t0 = time.time()
    while time.time() - t0 < timeout:
        out.clear()
        _u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
        if out:
            out.sort(reverse=True)
            return out[0]
        time.sleep(1)
    return None


w = find_main()
if not w:
    print("❌ 没找到主窗口（exe 可能启动失败）")
    p.terminate()
    sys.exit(1)
_, hwnd, title, ww, wh = w
print(f"窗口: {title!r}  {ww}x{wh}")

_u.SetForegroundWindow(hwnd)
time.sleep(2)


def grab(hwnd, path):
    r = wt.RECT()
    _u.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    hdc = _u.GetWindowDC(hwnd)
    mdc = _g.CreateCompatibleDC(hdc)
    bmp = _g.CreateCompatibleBitmap(hdc, w, h)
    _g.SelectObject(mdc, bmp)
    _u.PrintWindow(hwnd, mdc, 2)

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
    _g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    _g.DeleteObject(bmp)
    _g.DeleteDC(mdc)
    _u.ReleaseDC(hwnd, hdc)
    from PIL import Image
    Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1)\
         .convert("RGB").save(path)
    return w, h


shot = Path("C:/Users/Dancing/AppData/Local/Temp/v297_gui.png")
pw, ph = grab(hwnd, shot)
print(f"截图 {pw}x{ph} → {shot}")

from PIL import Image  # noqa: E402
im = Image.open(shot).convert("RGB")
px = im.load()

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


print()
print("=" * 74)
print("像素分析")
print("=" * 74)

# 每行的深色比例
rows = []
for y in range(ph):
    d = t = 0
    for x in range(0, pw, 5):
        r, g, b = px[x, y]
        t += 1
        if r < 75 and g < 75 and b < 110:
            d += 1
    rows.append(d / max(t, 1))

# 找连续深色段（>40% 深色）
segs = []
st = None
for y, d in enumerate(rows):
    if d > 0.40:
        if st is None:
            st = y
    else:
        if st is not None and y - st > 30:
            segs.append((st, y))
        st = None
if st is not None and ph - st > 30:
    segs.append((st, ph))

print(f"  深色区段（>40% 深色）: {len(segs)} 段")
for a, b in segs:
    print(f"    y={a}~{b}  高 {b-a}px  占 {(b-a)*100/ph:.1f}%  "
          f"{'底部' if a > ph*0.5 else '上部'}")

check("L1 exe 启动且窗口 > 800px 宽", pw > 800 and ph > 500,
      f"{pw}x{ph}")

# 日志框：底部深色段（#0b1020 很深）
bottom = [s for s in segs if s[0] > ph * 0.55]
if bottom:
    a, b = bottom[-1]
    hh = b - a
    check("L2 ★日志框高度 >= 200px（16 行）", hh >= 200, f"{hh}px")
else:
    check("L2 ★日志框高度 >= 200px（16 行）", False, "没找到底部深色段")

# 授权条：顶部应该有深蓝授权条（未授权时是红/橙色）
red_px = 0
for y in range(0, min(int(ph * 0.2), 160), 2):
    for x in range(0, pw, 6):
        r, g, b = px[x, y]
        if r > 140 and g < 110 and b < 110:
            red_px += 1
check("L3 ★顶部不是大面积红色（说明已授权或正常）",
      red_px < 300, f"红色像素 {red_px}")

# 列表区：中部应该有明显的白色区域
white_rows = 0
for y in range(int(ph * 0.3), int(ph * 0.55), 3):
    wc = 0
    for x in range(0, pw, 8):
        r, g, b = px[x, y]
        if r > 240 and g > 240 and b > 240:
            wc += 1
    if wc > (pw // 8) * 0.5:
        white_rows += 1
check("L4 列表区仍可见（白色区域）", white_rows > 10, f"{white_rows} 行")

time.sleep(1)
p.terminate()

print()
ok_n = sum(1 for _, c, _ in R if c)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, c, _ in R:
    if not c:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
