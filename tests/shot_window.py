"""通用截图：启动真实 GUI（源码或 exe）→ PrintWindow → PNG。

★ 铁律：Windows Tk 窗口必须用 PrintWindow(PW_RENDERFULLCONTENT)，
   PIL.ImageGrab 截不到 Tk 控件内容（PITFALLS P37）。
★ 找窗口按面积取最大，否则会抓到 tooltip（实测抓过 314x50 的提示窗）。

用法：
  python tests/shot_window.py                       # 源码模式
  python tests/shot_window.py --exe <path.exe> --out a.png
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


def capture(hwnd, out: pathlib.Path) -> tuple[int, int]:
    from PIL import Image
    u = ctypes.windll.user32
    g = ctypes.windll.gdi32
    r = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top

    hdc = u.GetWindowDC(hwnd)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mdc, bmp)
    ok = u.PrintWindow(hwnd, mdc, 2)      # 2 = PW_RENDERFULLCONTENT

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
                    ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
                    ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD),
                    ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long),
                    ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
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


def find_main_window(timeout=25.0):
    u = ctypes.windll.user32
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = []

        def cb(h, _):
            if not u.IsWindowVisible(h):
                return True
            n = u.GetWindowTextLengthW(h)
            if n:
                b = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(h, b, n + 1)
                cb2 = ctypes.create_unicode_buffer(256)
                u.GetClassNameW(h, cb2, 256)
                # ★ 排除资源管理器窗口（文件夹名也叫「微信视频号下载器」，
                #   实测抓到过 157x25 的假窗口）
                if cb2.value in ("CabinetWClass", "ExploreWClass",
                                 "WorkerW", "Shell_TrayWnd"):
                    return True
                r = wt.RECT()
                u.GetWindowRect(h, ctypes.byref(r))
                w, hh = r.right - r.left, r.bottom - r.top
                if w < 700 or hh < 450:      # 主窗口最小 820x600
                    return True
                if ("sphdl" in b.value.lower()
                        or "视频号下载器" in b.value):
                    found.append((w * hh, h, b.value))
            return True

        u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
        found.sort(reverse=True)
        if found:
            return found[0][1], found[0][2]
        time.sleep(1.0)
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default="")
    ap.add_argument("--out", default=str(ROOT / "docs/screenshots/ui_now.png"))
    ap.add_argument("--wait", type=float, default=14.0)
    ap.add_argument("--keep-open", action="store_true")
    a = ap.parse_args()

    out = pathlib.Path(a.out)
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

    hwnd, title = find_main_window(a.wait + 12)
    if not hwnd:
        print("❌ 没找到主窗口")
        proc.terminate()
        return 1

    time.sleep(max(0.0, a.wait - 6))     # 等界面把数据铺完
    w, h, ok = capture(hwnd, out)
    print(f"✅ 窗口 {title!r}  {w}x{h}  PrintWindow={ok}  → {out}")

    if not a.keep_open:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
