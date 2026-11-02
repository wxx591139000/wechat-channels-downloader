"""截图（自愈版）：启动 GUI → 按标题找主窗口 → 关掉「使用说明」弹窗 → PrintWindow。

为什么不能用 tests/shot_window.py：
  · 它按 **子进程 pid** 找窗口，而本程序会**提权重启**（系统代理/证书要管理员），
    真正画界面的进程是另一个 pid，于是永远「没找到主窗口」。
  · 它固定等 N 秒，启动慢时也会误报。

用法：
  ../.venv/Scripts/python.exe tests/shot2.py --out docs/screenshots/a.png
  ../.venv/Scripts/python.exe tests/shot2.py --exe dist/xxx/xxx.exe --out b.png
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
u = ctypes.windll.user32
g = ctypes.windll.gdi32
TITLES = ("微信视频号下载器", "sphdl")


def windows():
    rows = []

    def cb(h, _):
        if not u.IsWindowVisible(h):
            return True
        n = u.GetWindowTextLengthW(h)
        if not n:
            return True
        b = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(h, b, n + 1)
        c = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, c, 256)
        r = wt.RECT()
        u.GetWindowRect(h, ctypes.byref(r))
        rows.append((h, b.value, c.value, r.right - r.left, r.bottom - r.top))
        return True

    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
    return rows


def close_window(hwnd):
    u.PostMessageW(hwnd, 0x0010, 0, 0)     # WM_CLOSE


def capture(hwnd, out: pathlib.Path):
    from PIL import Image
    r = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    hdc = u.GetWindowDC(hwnd)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mdc, bmp)
    ok = u.PrintWindow(hwnd, mdc, 2)

    class BMIH(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
                    ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
                    ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD),
                    ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long),
                    ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

    bi = BMIH()
    bi.biSize = ctypes.sizeof(BMIH)
    bi.biWidth, bi.biHeight = w, -h
    bi.biPlanes, bi.biBitCount, bi.biCompression = 1, 32, 0
    buf = ctypes.create_string_buffer(w * h * 4)
    g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(hwnd, hdc)
    im = Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.convert("RGB").save(out)
    return w, h, ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default="")
    ap.add_argument("--out", default=str(ROOT / "docs/screenshots/ui.png"))
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--settle", type=float, default=4.0)
    ap.add_argument("--keep-open", action="store_true")
    a = ap.parse_args()

    if a.exe:
        proc = subprocess.Popen([a.exe], cwd=str(pathlib.Path(a.exe).parent))
    else:
        sec = (ROOT / ".license_secret").read_text().strip()
        env = dict(os.environ)
        env["SPHDL_LIC_SECRET"] = sec
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen([sys.executable, "main.py", "--gui"],
                                env=env, cwd=str(ROOT),
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)

    deadline = time.time() + a.timeout
    main_hwnd = None
    while time.time() < deadline:
        for hwnd, title, cls, w, h in windows():
            if cls != "TkTopLevel":
                continue
            if title == "使用说明":
                close_window(hwnd)      # 挡住主窗口，先关掉
                continue
            if any(t in title for t in TITLES) and w >= 700 and h >= 450:
                main_hwnd = hwnd
        if main_hwnd:
            break
        time.sleep(0.5)

    if not main_hwnd:
        print("❌ 超时没找到主窗口")
        proc.terminate()
        return 1

    time.sleep(a.settle)
    for hwnd, title, cls, w, h in windows():   # 二次清理：弹窗可能在等待期间才出来
        if cls == "TkTopLevel" and title == "使用说明":
            close_window(hwnd)
    time.sleep(1.5)

    w, h, ok = capture(main_hwnd, pathlib.Path(a.out))
    print(f"✅ 截图 {w}x{h} PrintWindow={ok} → {a.out}")

    if not a.keep_open:
        r = wt.RECT()
        u.GetWindowRect(main_hwnd, ctypes.byref(r))
        close_window(main_hwnd)     # 走正常关闭，避免残留实例占互斥量
        time.sleep(1.0)
        try:
            proc.terminate()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
