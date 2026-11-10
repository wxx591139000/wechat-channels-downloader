"""截 exe 主窗口（等尺寸稳定版）。

`tests/shot2.py` 抓到 314x50 的标题栏，是因为窗口刚创建、还没按 geometry
撑开就 PrintWindow 了。这里改成：轮询找窗口 → 等客户区尺寸连续两次一致
且宽度 > 800 → 再截。
"""
import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
u = ctypes.windll.user32
g = ctypes.windll.gdi32
PW_RENDERFULLCONTENT = 0x2


def find_main(title_key="微信视频号下载器", min_w=780, timeout=40.0):
    t0 = time.time()
    last = None
    stable = 0
    while time.time() - t0 < timeout:
        hits = []

        def cb(h, _):
            if not u.IsWindowVisible(h):
                return True
            n = u.GetWindowTextLengthW(h)
            if not n:
                return True
            b = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(h, b, n + 1)
            if title_key in b.value:
                r = wt.RECT()
                u.GetClientRect(h, ctypes.byref(r))
                hits.append((h, b.value, r.right - r.left, r.bottom - r.top))
            return True

        u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND,
                                         wt.LPARAM)(cb), 0)
        big = [x for x in hits if x[2] >= min_w and x[3] >= 400]
        if big:
            cur = max(big, key=lambda x: x[2] * x[3])
            if last == (cur[0], cur[2], cur[3]):
                stable += 1
                if stable >= 2:
                    return cur
            else:
                stable = 0
            last = (cur[0], cur[2], cur[3])
        time.sleep(0.4)
    return None


def grab(hwnd, out: Path):
    """按**整窗**建位图再裁客户区（P64-①）。

    主窗口是 overrideredirect（无系统边框），偏移本来是 (0,0)，
    但子窗口/以后的改动不一定还是；统一走裁剪路径，免得再被"底部少 31px"骗一次。
    """
    wrect = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(wrect))
    ww, wh = wrect.right - wrect.left, wrect.bottom - wrect.top
    crect = wt.RECT()
    u.GetClientRect(hwnd, ctypes.byref(crect))
    cw, ch = crect.right - crect.left, crect.bottom - crect.top
    pt = wt.POINT(0, 0)
    u.ClientToScreen(hwnd, ctypes.byref(pt))
    offx, offy = pt.x - wrect.left, pt.y - wrect.top

    hdc = u.GetDC(hwnd)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, ww, wh)
    old = g.SelectObject(mdc, bmp)
    u.PrintWindow(hwnd, mdc, PW_RENDERFULLCONTENT)

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG),
                    ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                    ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                    ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                    ("biClrImportant", wt.DWORD)]

    bi = BIH()
    bi.biSize = ctypes.sizeof(BIH)
    bi.biWidth, bi.biHeight = ww, -wh
    bi.biPlanes, bi.biBitCount = 1, 32
    buf = ctypes.create_string_buffer(ww * wh * 4)
    g.GetDIBits(mdc, bmp, 0, wh, buf, ctypes.byref(bi), 0)
    g.SelectObject(mdc, old)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(hwnd, hdc)

    from PIL import Image
    img = Image.frombuffer("RGBA", (ww, wh), buf, "raw", "BGRA", 0, 1)
    img = img.crop((offx, offy, offx + cw, offy + ch))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out)
    print(f"已存 {out}  {cw}x{ch}（边框偏移 {offx},{offy}）")


def main():
    exe = sys.argv[1] if len(sys.argv) > 1 else (
        "dist/v3.4.0/微信视频号下载器/微信视频号下载器.exe")
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else (
        ROOT / "docs" / "screenshots" / "v340-01-exe-unauth.png")
    p = subprocess.Popen([exe], cwd=str((ROOT / exe).parent))
    hit = None
    try:
        hit = find_main()
        if not hit:
            print("没找到主窗口")
            return 1
        hwnd, title, w, h = hit
        print(f"找到：{title}  {w}x{h}")
        time.sleep(0.8)
        grab(hwnd, out)
        return 0
    finally:
        # ★ P49：收尾用 WM_CLOSE 正常关闭。直接 terminate 会残留实例占着
        #   单实例互斥量，下次启动直接弹「已在运行」。
        u.PostMessageW(hit[0] if hit else 0, 0x0010, 0, 0)
        for _ in range(20):
            if p.poll() is not None:
                break
            time.sleep(0.2)
        if p.poll() is None:
            p.terminate()


if __name__ == "__main__":
    sys.exit(main())
