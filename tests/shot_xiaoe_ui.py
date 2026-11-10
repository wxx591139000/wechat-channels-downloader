"""给「小鹅通课程下载」子窗口截图（留档 + 肉眼验收用）。

做法同 `shot_bili_ui.py`：
  1. 起隐藏 Tk root，造 _FakeApp + XiaeoWindow
  2. 灌几条假课程（含各种状态：待下载/下载中/完成/失败）
  3. 按窗口标题找 hwnd → PrintWindow(PW_RENDERFULLCONTENT) → 存 png

为什么要 PrintWindow 而不是 ImageGrab：
  主窗口常被别的窗口盖住，ImageGrab 会截到遮挡物；PrintWindow 让窗口
  自己重绘到内存 DC，拿到的是窗口真实的像素。

用法：python tests/shot_xiaoe_ui.py [输出png]
"""
import ctypes
import ctypes.wintypes as wt
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else (
    ROOT / "docs" / "screenshots" / "v340-01-xiaoe-window.png")
TITLE = "小鹅通课程下载"

u = ctypes.windll.user32
g = ctypes.windll.gdi32
PW_RENDERFULLCONTENT = 0x2


def find_window(title_key: str, timeout: float = 12.0):
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
            # ★ P37：按面积取最大的那个。鼠标恰好停在行上时会多出一个
            #   tooltip 窗口（同进程、标题可能为空但枚举顺序靠前），
            #   取第一个会抓到那个小窗。
            def _area(h):
                r = wt.RECT()
                u.GetWindowRect(h, ctypes.byref(r))
                return (r.right - r.left) * (r.bottom - r.top)
            hits.sort(key=lambda x: _area(x[0]), reverse=True)
            return hits[0]
        time.sleep(0.3)
    return None, None


def grab(hwnd, out_path: Path):
    """抓窗口**客户区**像素。

    ★ P64-① 踩过的坑：`PrintWindow` 渲染的是**整个窗口**（含系统标题栏/边框），
      而这里如果只按 `GetClientRect` 的尺寸建位图，系统标题栏那 ~31px 就会被
      算在图里 → 画面整体下移、**底部 31px 被切掉**（状态条正好在那里消失，
      一度被我误判成"布局把控件挤出去了"）。
    正确做法：位图按 `GetWindowRect` 尺寸建，抓完再按客户区在窗口里的偏移裁掉边框。
    """
    wrect = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(wrect))
    ww, wh = wrect.right - wrect.left, wrect.bottom - wrect.top

    crect = wt.RECT()
    u.GetClientRect(hwnd, ctypes.byref(crect))
    cw, ch = crect.right - crect.left, crect.bottom - crect.top
    if cw <= 0 or ch <= 0 or ww <= 0 or wh <= 0:
        raise SystemExit(f"窗口尺寸异常: 窗口 {ww}x{wh} 客户区 {cw}x{ch}")

    pt = wt.POINT(0, 0)
    u.ClientToScreen(hwnd, ctypes.byref(pt))
    offx, offy = pt.x - wrect.left, pt.y - wrect.top
    print(f"窗口 {ww}x{wh} 客户区 {cw}x{ch} 边框偏移 ({offx},{offy})")

    hdc = u.GetDC(hwnd)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, ww, wh)
    old = g.SelectObject(mdc, bmp)
    ok = u.PrintWindow(hwnd, mdc, PW_RENDERFULLCONTENT)
    if not ok:
        u.PrintWindow(hwnd, mdc, 0)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG),
                    ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                    ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                    ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                    ("biClrImportant", wt.DWORD)]

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
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
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path)
    print(f"已存 {out_path}  {cw}x{ch}")


class _FakeQ:
    def __init__(self):
        self.items = []

    def put(self, x):
        self.items.append(x)


class _FakeCfg(dict):
    def get(self, k, d=None):
        return dict.get(self, k, d)


class _FakeApp:
    def __init__(self, root):
        self.root = root
        self.config = _FakeCfg({"download_dir": str(Path.home() / "Downloads")})
        self.ui_q = _FakeQ()
        self.logs = []

    def push_log(self, lv, msg):
        self.logs.append((lv, msg))
        print(f"[{lv}] {msg}")


def main():
    import tkinter as tk

    from sphdl.xiaoe import XiaeoVideo
    from sphdl.xiaoe_ui import XiaeoWindow

    root = tk.Tk()
    root.withdraw()
    app = _FakeApp(root)
    win = XiaeoWindow(app)
    win.win.geometry("1020x760+120+80")

    # 灌假数据：覆盖四种状态
    demo = [
        ("陆军：中国城市精细化管理系统与智慧升级（上）", 1730, "720P", "pending"),
        ("陆军：中国城市精细化管理系统与智慧升级（下）", 1682, "720P", "downloading"),
        ("城市治理公开课 · 第二讲：数据底座怎么搭", 2410, "1080P", "done"),
        ("城市治理公开课 · 第三讲：考核指标设计", 1980, "720P", "failed"),
    ]
    for i, (title, dur, q, st) in enumerate(demo):
        v = XiaeoVideo(appid="appkxt2fvhl1688",
                       resource_id=f"v_64b4b3cce4b007b201a6d98{i}",
                       product_id="p_62d7b05de4b00a4f372892372",
                       title=title, duration=dur, quality=q)
        win.apply_video(v)

    kids = win.tree.get_children()
    if len(kids) >= 4:
        win.tree.set(kids[1], "status", "312/865 片 · 44.1 MB")
        win.tree.set(kids[1], "size", "44.1 MB")
        win.tree.set(kids[2], "status", "完成 118.4 MB")
        win.tree.set(kids[2], "size", "118.4 MB")
        win.tree.set(kids[2], "sel", "☐")
        win.tree.set(kids[3], "status", "失败：需要登录 Cookie")
    win.pbar.config(value=36)
    win.lbl_stat.config(text="第 2/4 节：正在下载（312/865 片，8 路并发）")
    win.lbl_count.config(text="4 节待下载")

    win.show()
    root.update()
    time.sleep(1.6)
    root.update()

    hwnd, title = find_window(TITLE)
    if not hwnd:
        print("没找到窗口，放弃截图")
        win.win.destroy()
        root.destroy()
        return 1
    print("找到窗口：", title)
    grab(hwnd, OUT)
    win.win.destroy()
    root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
