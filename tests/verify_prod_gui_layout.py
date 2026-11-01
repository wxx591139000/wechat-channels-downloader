"""启动真实的产品 GUI，用 Win32 截图验证日志区可见（v2.9.6）。

═══ 客户报障 ═══
「运行了 2.9.5，但发现页面底部的日志区域已经看不到」

═══ 排查过程（重要教训）═══
之前用 PIL ImageGrab 截图 → 三次都显示"没有日志区"，
差点让我以为布局没修好。**实际是 ImageGrab 在此环境
截不到 Tk 窗口内容**（截到的是桌面其他部分）。

改用 Win32 PrintWindow(hwnd, dc, PW_RENDERFULLCONTENT=2) 后
一次就看出真相：三种布局其实都正常显示。

本测试：启动真实 exe → PrintWindow 截图 → 读像素判断
日志区（深色 #0b1020）是否占比合理。
"""
import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "dist" / "v2.9.6" / "微信视频号下载器" / "微信视频号下载器.exe"
if not EXE.exists():
    # 找最新版本目录
    cands = sorted(ROOT.glob("dist/v2.9.*/微信视频号下载器/微信视频号下载器.exe"))
    EXE = cands[-1] if cands else None

if not EXE or not EXE.exists():
    print("❌ 找不到 exe")
    sys.exit(1)

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32

print(f"启动: {EXE.name}（{EXE.parent.parent.name}）")
proc = subprocess.Popen([str(EXE)], cwd=str(EXE.parent))
time.sleep(12)          # 等 GUI 起来


def find_window(keyword, timeout=30):
    """找面积最大的匹配窗口（排除 tooltip / 小提示窗）。"""
    found = []

    def cb(hwnd, _):
        if not _user32.IsWindowVisible(hwnd):
            return True
        n = _user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            _user32.GetWindowTextW(hwnd, buf, n + 1)
            tv = buf.value
            # ★ 精确匹配主窗口：标题必须含 "sphdl v"（排除同名文件夹的资源管理器）
            if keyword in tv and "sphdl v" in tv:
                rect = wt.RECT()
                _user32.GetWindowRect(hwnd, ctypes.byref(rect))
                area = (rect.right - rect.left) * (rect.bottom - rect.top)
                found.append((area, hwnd, buf.value))
        return True

    t0 = time.time()
    while time.time() - t0 < timeout:
        found.clear()
        _user32.EnumWindows(
            ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
        if found:
            found.sort(reverse=True)
            area, hwnd, t = found[0]
            print(f"  （候选 {len(found)} 个窗口，取最大 {area}px²）")
            return hwnd, t
        time.sleep(1)
    return None


win = find_window("视频号下载器")
if not win:
    print("❌ 没找到窗口")
    proc.terminate()
    sys.exit(1)
hwnd, title = win
print(f"窗口: {title!r} hwnd={hwnd}")

# 等界面完全渲染
time.sleep(3)
_user32.SetForegroundWindow(hwnd)
time.sleep(1)


def grab(hwnd, path):
    rect = wt.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    hdc = _user32.GetWindowDC(hwnd)
    mdc = _gdi32.CreateCompatibleDC(hdc)
    bmp = _gdi32.CreateCompatibleBitmap(hdc, w, h)
    _gdi32.SelectObject(mdc, bmp)
    _user32.PrintWindow(hwnd, mdc, 2)

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
    _gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    _gdi32.DeleteObject(bmp)
    _gdi32.DeleteDC(mdc)
    _user32.ReleaseDC(hwnd, hdc)

    from PIL import Image
    Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1)\
         .convert("RGB").save(path)
    return w, h


shot = Path("C:/Users/Dancing/AppData/Local/Temp/prod_gui.png")
w, h = grab(hwnd, shot)
print(f"截图: {w}x{h}  → {shot}")
if w < 600 or h < 400:
    print(f"⚠ 窗口尺寸异常（{w}x{h}），可能抓错了窗口")
    proc.terminate()
    sys.exit(1)

# 分析：找深色区域的位置和占比
from PIL import Image  # noqa: E402
im = Image.open(shot).convert("RGB")
pw, ph = im.size
px = im.load()

rows = []
for y in range(ph):
    dark = 0
    for x in range(0, pw, 6):
        r, g, b = px[x, y]
        if r < 70 and g < 70 and b < 100:
            dark += 1
    rows.append(dark / max(pw // 6, 1))

# 找连续的深色行段
segments = []
start = None
for y, d in enumerate(rows):
    if d > 0.25:
        if start is None:
            start = y
    else:
        if start is not None and y - start > 40:
            segments.append((start, y))
        start = None
if start is not None and ph - start > 40:
    segments.append((start, ph))

print()
print("=" * 74)
print("深色区域检测（日志区背景是 #0b1020）")
print("=" * 74)
if not segments:
    print("  ❌ 没找到连续深色区域 —— 日志区可能真看不见")
    ok = False
else:
    ok = True
    for (a, b) in segments:
        pct = (b - a) * 100 / ph
        print(f"  ✅ 深色区域 y={a}~{b}  高度 {b-a}px（占窗口 {pct:.1f}%）")
        print(f"     位置: {'底部' if a > ph * 0.5 else '中部/顶部'}")

    # 日志区应该在底部
    bottom_segs = [s for s in segments if s[0] > ph * 0.4]
    if bottom_segs:
        a, b = bottom_segs[-1]
        pct = (b - a) * 100 / ph
        print()
        print(f"  ★日志区（底部深色段）: {b-a}px，占 {pct:.1f}%")
        ok = pct >= 10      # 至少占 10% 才算"看得见"
        print(f"  {'✅ 日志区可见且高度合理' if ok else '❌ 日志区仍然太矮'}")

time.sleep(1)
proc.terminate()
print()
print(f"RESULT: {'PASS' if ok else 'FAIL'}")
sys.exit(0 if ok else 1)
