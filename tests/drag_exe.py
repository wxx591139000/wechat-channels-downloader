"""用**真实 Windows 鼠标消息**验证打包后的 exe：分隔条能不能拖动。

为什么不在源码里测就收工：
    源码里 `event_generate` 走的是同一段绑定，但 exe 里可能是另一份代码。
    这里把 WM_LBUTTONDOWN/MOUSEMOVE/LBUTTONUP 直接投给**分隔条那个控件的
    HWND**，走的是 Windows 给真实鼠标用的同一条消息路径。

★ 两个坑（都实测踩过）：
  ① 本机是 200% 缩放：屏幕物理 2520x1680，Tk 报 1260x840。
     坐标必须统一到**物理像素**，否则点偏一半屏幕。
  ② Tk 每个控件都是独立子窗口（class=TkChild），消息要投给控件自己的
     HWND，投给顶层窗口不生效（实测 0px）。

用法：python tests/drag_exe.py --exe "dist/v3.1.3/微信视频号下载器/微信视频号下载器.exe"
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import pathlib
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.shot_window import capture, find_main_window   # noqa: E402

GRIP_COLOR = (170, 184, 204)      # 握把三条短杠 #aab8cc
DARK_LOG = (11, 16, 32)           # 日志文本区 #0b1020


def dpi_aware():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def find_rows(img_path, color, min_pixels=40):
    from PIL import Image
    import numpy as np
    a = np.asarray(Image.open(img_path).convert("RGB"))
    m = np.all(a == np.array(color), axis=2)
    return [y for y in range(a.shape[0]) if m[y].sum() >= min_pixels]


def log_top(img_path):
    from PIL import Image
    import numpy as np
    a = np.asarray(Image.open(img_path).convert("RGB"))
    m = np.all(a == np.array(DARK_LOG), axis=2)
    ys = [y for y in range(a.shape[0]) if m[y].sum() > 600]
    return ys[0] if ys else -1


def child_windows(hwnd):
    u = ctypes.windll.user32
    out = []

    def cb(h, _):
        rr = wt.RECT()
        u.GetWindowRect(h, ctypes.byref(rr))
        cls = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(h, cls, 64)
        out.append({"hwnd": h, "cls": cls.value,
                    "x": rr.left, "y": rr.top,
                    "w": rr.right - rr.left, "h": rr.bottom - rr.top})
        return True

    u.EnumChildWindows(hwnd,
                       ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    a = ap.parse_args()

    dpi_aware()
    u = ctypes.windll.user32
    exe = pathlib.Path(a.exe).resolve()
    # 用临时数据目录（拷真实配置但把 log_pane 清 0）：
    #   否则上一次拖动记住的高度会让这次一开始就顶在上限，拖不动是正常的
    import json
    import shutil
    data = pathlib.Path(tempfile.mkdtemp(prefix="sphdl_exe_drag_"))
    src = pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "sphdl" / "config.json"
    if src.exists():
        d = json.loads(src.read_text(encoding="utf-8"))
        d["log_pane"] = 0
        (data / "config.json").write_text(json.dumps(d), encoding="utf-8")
    env = dict(os.environ, SPHDL_DATA_DIR=str(data))
    proc = subprocess.Popen([str(exe)], cwd=str(exe.parent), env=env)
    hwnd, title = find_main_window(30)
    if not hwnd:
        print("❌ 没找到主窗口")
        proc.terminate()
        return 1
    print(f"窗口 {title!r}")
    time.sleep(10)

    r = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    win_w, win_h = r.right - r.left, r.bottom - r.top
    print(f"窗口物理尺寸 {win_w}x{win_h}")

    before = ROOT / "docs/screenshots/_exe_drag_before.png"
    after = ROOT / "docs/screenshots/_exe_drag_after.png"
    capture(hwnd, before)

    # ★ 别用"扫颜色"定位握把：界面里别处也有同色像素，会把位置平均歪
    #   （实测算出来 609，真实在 1246）。直接读分隔条控件的 HWND 几何。
    kids = child_windows(hwnd)
    grip_win = None
    for k in kids:
        if k["cls"] != "TkChild":
            continue
        if k["w"] > win_w * 0.85 and 24 <= k["h"] <= 48:
            if grip_win is None or k["h"] < grip_win["h"]:
                grip_win = k
    if not grip_win:
        print("❌ 没找到分隔条控件 HWND")
        for k in kids[:8]:
            print("   ", k)
        proc.terminate()
        return 1
    grip_y = grip_win["y"]
    log_y0 = log_top(before)
    print(f"分隔条控件 0x{grip_win['hwnd']:x}  几何 {grip_win['w']}x{grip_win['h']} "
          f"@({grip_win['x']},{grip_y})  日志深色区 top={log_y0}")

    # ---- 真实拖动：先把窗口切到前台（否则鼠标事件落到别的窗口上）----
    fg = u.GetForegroundWindow()
    if fg != hwnd:
        tid_fg = u.GetWindowThreadProcessId(fg, None)
        tid_me = ctypes.windll.kernel32.GetCurrentThreadId()
        u.AttachThreadInput(tid_me, tid_fg, True)
        u.ShowWindow(hwnd, 9)                  # SW_RESTORE
        u.SetForegroundWindow(hwnd)
        u.BringWindowToTop(hwnd)
        u.AttachThreadInput(tid_me, tid_fg, False)
        time.sleep(0.6)
    print("前台窗口 =", hex(u.GetForegroundWindow()),
          "== 目标?", u.GetForegroundWindow() == hwnd)

    before_kids = {k["hwnd"]: (k["x"], k["y"], k["w"], k["h"])
                   for k in child_windows(hwnd)}

    # 拖动量：100 逻辑像素；窗口物理宽 / 1240 逻辑宽 = 缩放比
    scale = win_w / 1240.0
    dy = int(100 * scale)
    sx = grip_win["x"] + grip_win["w"] // 2
    sy = grip_win["y"] + grip_win["h"] // 2
    print(f"按下点 ({sx},{sy})  上移 {dy}px（scale={scale:.2f}）")
    u.SetCursorPos(sx, sy)
    time.sleep(0.3)
    u.mouse_event(0x0002, 0, 0, 0, 0)                    # LEFTDOWN
    for i in range(1, 13):
        u.SetCursorPos(sx, sy - int(dy * i / 12))
        time.sleep(0.04)
    u.mouse_event(0x0004, 0, 0, 0, 0)                    # LEFTUP
    time.sleep(1.0)

    capture(hwnd, after)
    log_y1 = log_top(after)
    grip2 = -1
    for k in child_windows(hwnd):
        if k["hwnd"] == grip_win["hwnd"]:
            grip2 = k["y"]
            after_kids = {kk["hwnd"]: (kk["x"], kk["y"], kk["w"], kk["h"])
                          for kk in child_windows(hwnd)}
    moved = grip_y - grip2
    log_moved = log_y0 - log_y1
    print("高度变化 >50px 的控件：")
    for h, r0 in before_kids.items():
        r1 = after_kids.get(h)
        if r1 and abs(r1[3] - r0[3]) > 50:
            print(f"   0x{h:x}  h {r0[3]} → {r1[3]}  (Δ{r1[3]-r0[3]:+d})  y {r0[1]}→{r1[1]}")
    print(f"拖动后握把 y={grip2}（原 {grip_y}，Δ{moved}）")

    # 判定用**控件几何**，不用扫颜色（颜色扫描会把别处的同色像素算进来）
    exp = dy
    grown = [r1[3] - r0[3] for h, r0 in before_kids.items()
             if (r1 := after_kids.get(h)) and r1[3] - r0[3] > exp * 0.5]
    shrunk = [r0[3] - r1[3] for h, r0 in before_kids.items()
              if (r1 := after_kids.get(h)) and r0[3] - r1[3] > exp * 0.5]
    print(f"变高的控件 {len(grown)} 个（日志侧）{grown[:3]}，"
          f"变矮 {len(shrunk)} 个（列表侧）{shrunk[:3]}")

    ok1 = abs(moved - exp) <= exp * 0.15
    # 日志不可能是满幅（已顶到上限）时，拖动会被夹紧 —— 那时只要求"有让位"
    ok2 = bool(grown) and bool(shrunk) and abs(grown[0] - shrunk[0]) <= 4
    print(("✅" if ok1 else "❌") + f" 握把上移 {moved}px（期望 ≈{exp}）")
    print(("✅" if ok2 else "❌") +
          f" 日志变高 {grown[:1]} / 列表变矮 {shrunk[:1]}（应等量）")

    try:
        proc.terminate()
        proc.wait(timeout=8)
    except Exception:
        pass
    if ok1 and ok2:
        print("✅ exe 里真实鼠标拖动生效")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
