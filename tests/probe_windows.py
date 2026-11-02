"""诊断：启动 GUI 子进程，按 PID 列出它的所有顶层窗口（含不可见）。

用途：shot_window.py 报「没找到主窗口」时，用它看清到底有哪些窗口、
多大、是否可见 —— 不要靠猜（PITFALLS 铁律）。

用法：
  ../.venv/Scripts/python.exe tests/probe_windows.py
  ../.venv/Scripts/python.exe tests/probe_windows.py --exe dist/v3.1.3/xxx.exe
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


def dump(pid, label):
    print(f"--- {label} pid={pid} ---")
    rows = []

    def cb(h, _):
        own = wt.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(own))
        if own.value != pid:
            return True
        n = u.GetWindowTextLengthW(h)
        b = ctypes.create_unicode_buffer(n + 1)
        if n:
            u.GetWindowTextW(h, b, n + 1)
        c = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, c, 256)
        r = wt.RECT()
        u.GetWindowRect(h, ctypes.byref(r))
        rows.append((b.value, c.value, r.right - r.left, r.bottom - r.top,
                     bool(u.IsWindowVisible(h)), h))
        return True

    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
    for t, cls, w, h, vis, hwnd in rows:
        print(f"  hwnd={hwnd:<10} vis={int(vis)} {w}x{h:<6} {cls:<16} {t!r}")
    if not rows:
        print("  (无顶层窗口)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default="")
    ap.add_argument("--wait", type=float, default=15.0)
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
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
    deadline = time.time() + a.wait
    while time.time() < deadline:
        time.sleep(3)
        if proc.poll() is not None:
            print(f"❌ 进程已退出 rc={proc.returncode}")
            out = proc.stdout.read() if proc.stdout else b""
            print(out.decode("utf-8", "replace")[-3000:])
            return 1
        dump(proc.pid, f"t+{int(a.wait - (deadline - time.time()))}s")
    print("✅ 进程仍在运行，终止")
    proc.terminate()
    try:
        out = proc.stdout.read() if proc.stdout else b""
        if out:
            print(out.decode("utf-8", "replace")[-2000:])
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
