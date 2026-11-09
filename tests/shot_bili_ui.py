"""给「B 站视频下载」子窗口截图（留档 + 肉眼验收用）。

为什么单独写：`tests/shot2.py` 只截**主窗口**（它按「微信视频号下载器」找标题）。
B 站窗口是 Tk 子窗口，标题是「B 站视频下载」，得单独按标题找。

做法：
  1. 起一个隐藏的 Tk root，造 _FakeApp + BilibiliWindow
  2. 灌一批假数据（3 个分P + 各种状态），让表格有内容可看
  3. 按窗口标题找 hwnd → PrintWindow(PW_RENDERFULLCONTENT) → 存 png

用法：
  ../.venv/Scripts/python.exe tests/shot_bili_ui.py [输出png]
"""
import ctypes
import ctypes.wintypes as wt
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else (
    ROOT / "docs" / "screenshots" / "v320-03-bili-window.png")
TITLE = "B 站视频下载"

u = ctypes.windll.user32
g = ctypes.windll.gdi32

PW_RENDERFULLCONTENT = 0x2


def find_window(title_key: str, timeout: float = 12.0):
    """按标题找可见窗口，最多等 timeout 秒。"""
    t0 = time.time()
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
                hits.append((h, b.value))
            return True

        u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND,
                                         wt.LPARAM)(cb), 0)
        if hits:
            return hits[0]
        time.sleep(0.3)
    return None, None


def grab(hwnd, out_path: Path):
    rect = wt.RECT()
    u.GetClientRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        raise SystemExit(f"❌ 客户区尺寸异常: {w}x{h}")

    hdc = u.GetDC(hwnd)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    old = g.SelectObject(mdc, bmp)
    ok = u.PrintWindow(hwnd, mdc, PW_RENDERFULLCONTENT)
    g.SelectObject(mdc, old)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG),
                    ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                    ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                    ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                    ("biClrImportant", wt.DWORD)]

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth, bi.biHeight = w, -h        # 负高度 = 从上到下
    bi.biPlanes, bi.biBitCount = 1, 32
    buf = ctypes.create_string_buffer(w * h * 4)
    g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)

    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(hwnd, hdc)

    # BGRA → PNG（不引 PIL 的话自己写太啰嗦，项目环境有 PIL）
    from PIL import Image
    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path)
    print(f"✅ 截图 {w}x{h} PrintWindow={ok} → {out_path}")
    return ok


def main():
    import tkinter as tk
    from sphdl import bilibili as b

    root = tk.Tk()
    root.withdraw()

    class _FakeApp:
        def __init__(self, r, out_dir):
            import queue
            self.root = r
            self.ui_q = queue.Queue()
            self.dir_var = tk.StringVar(master=r, value=str(out_dir))
            self.logs = []

        def push_log(self, lv, msg):
            self.logs.append((lv, msg))

    from sphdl.bilibili_ui import BilibiliWindow
    app = _FakeApp(root, ROOT / "dist" / "_shot_dl")
    win = BilibiliWindow(app)

    # 灌假数据：一个 6 分P 合集，故意做出三种状态（待下载 / 下载中 / 完成）
    v = b.BiliVideo(bvid="BV1TgWp6MEHJ", aid=1,
                    title="【2026全球总决赛】11月9日 BLG vs T1", owner="哔哩哔哩英雄联盟赛事")
    v.parts = [b.BiliPart(index=i, cid=43448467681 + i,
                          title=f"第{i}局", duration=3061 + i * 100)
               for i in range(1, 7)]
    win.apply_video(v, 0)
    win.apply_dl_start(0, 1, 6)
    win.apply_dl_prog(0, 62, 100, 1, 6)
    win.apply_dl_done(1, {"ok": True, "size": 218 * 1048576,
                          "quality_name": "720P"}, 2, 6)
    win.apply_dl_done(2, {"ok": False, "error": "取流被拒（-404）：稿件不可见"}, 3, 6)
    win.apply_dl_prog(3, 38, 100, 4, 6)
    win.lbl_count.config(text="6 个待下载（1 个视频）")
    win._set_busy(True, "下载中 4/6  38%")
    win.pbar.configure(value=38)

    win.win.deiconify()
    win.win.lift()
    root.update()
    for _ in range(20):
        root.update()
        time.sleep(0.05)
    time.sleep(0.6)

    hwnd, title = find_window(TITLE)
    if not hwnd:
        print(f"❌ 没找到标题含「{TITLE}」的窗口")
        return 1
    print(f"找到窗口: {title}  hwnd={hwnd}")
    try:
        grab(hwnd, OUT)
    finally:
        try:
            win.win.destroy()
            root.destroy()
        except Exception:                          # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
