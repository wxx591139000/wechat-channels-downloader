"""抖音窗口关闭不中断任务 + 窗口按钮（v2.5.2）。

═══ 客户报障（2026-10-27）═══
1. 「点击抖音批量下载界面的 X 关闭后，不应该影响解析、下载中的抖音任务」
2. 「抖音批量下载的页面要增加最小化按钮、最大化按钮」

═══ 根因 ═══
1. ✕ 按钮直接 `self.win.destroy()` —— 窗口对象被摧毁，后台线程还在跑，
   但回填结果时访问已销毁的 Treeview → 抛异常被吞掉 → **进度丢失**，
   客户看起来就像"任务被中断了"。
2. 窗口创建时用了 `transient(self.root)` —— Windows 上 transient 是
   「工具窗口」，标题栏**没有最小化/最大化按钮**。

═══ 修法 ═══
1. ✕ 改成 withdraw（隐藏）：
   · 后台线程照常跑，结果照常回填到隐藏的表格
   · 再点主界面【🎵 抖音批量下载】→ deiconify，能看到真实进度
   · 真想停任务用界面里的【停止】按钮
   · 拦截系统标题栏 X（WM_DELETE_WINDOW）走同一逻辑
   · 所有回填方法加 `_alive()` 保护（万一被销毁也不抛异常）
2. 去掉 transient + 页面内加 最小化/最大化/关闭 三个按钮

覆盖：
  W1  ✕ 不销毁窗口（is_alive 仍为 True）
  W2  隐藏后 _busy 状态保留（任务还在跑）
  W3  隐藏后仍能回填结果（apply_item / apply_dl_prog 不报错）
  W4  重新显示能看到隐藏期间的回填
  W5  系统标题栏 X 走同一逻辑（WM_DELETE_WINDOW 已绑定）
  W6  没有 transient（标题栏有最小化/最大化）
  W7  页面内有 — / □ / ✕ 三个按钮
  W8  最大化切换能工作（state 变 zoomed / 恢复）
  W9  真正销毁窗口后回填不崩（_alive 保护）
  W10 源码层面：没有把 ✕ 绑到 destroy
"""
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


import tkinter as tk
import sphdl.gui as G

cfgdir = Path(tempfile.mkdtemp(prefix="dywin_"))
G.CONFIG_FILE = cfgdir / "config.json"
G.RECORDS_FILE = cfgdir / "records.json"
G.CONFIG_FILE.write_text("{}", encoding="utf-8")
G.RECORDS_FILE.write_text("[]", encoding="utf-8")

app = G.App()
app.root.deiconify()
app.root.geometry("1100x700+30+30")
for _ in range(8):
    app.root.update()
    time.sleep(0.06)

app._on_douyin()
for _ in range(10):
    app.root.update()
    time.sleep(0.08)
w = app._dy_win
check("准备：抖音窗口已创建", w is not None)

if not w:
    print("RESULT: FAIL (0/1)")
    sys.exit(1)

print()
print("=" * 74)
print("W6 标题栏按钮（transient 已去掉）")
print("=" * 74)
src = (ROOT / "sphdl" / "douyin_ui.py").read_text(encoding="utf-8")
has_transient = "self.win.transient(" in src
print(f"  源码里有 transient: {has_transient}")
check("W6 transient 已移除（否则没有最小化/最大化按钮）", not has_transient)
try:
    trans = bool(w.win.tk.call("wm", "transient", str(w.win)))
except Exception:
    trans = False
print(f"  运行时 wm transient: {trans}")
check("W6b 运行时不是 transient 窗口", not trans)
check("W6c 声明可缩放", w.win.resizable() == (1, 1) or
      "resizable(True" in src, str(w.win.resizable()))

print()
print("=" * 74)
print("W7 页面内三个窗口按钮")
print("=" * 74)


def walk(x, acc):
    for c in x.winfo_children():
        acc.append(c)
        walk(c, acc)
    return acc


allc = walk(w.win, [])
btns = [(c.cget("text"), c) for c in allc
        if isinstance(c, tk.Button)]
texts = [t for t, _ in btns]
print(f"  页面按钮: {texts}")
for sym, label in (("—", "最小化"), ("□", "最大化"), ("✕", "关闭")):
    check(f"W7 有 {label} 按钮（{sym}）", sym in texts, str(texts))

print()
print("=" * 74)
print("W1-W4 关闭不中断任务")
print("=" * 74)
# 模拟：正在解析
w._busy = True
w.videos = [("https://u1", None), ("https://u2", None)]
for i in range(2):
    iid = w.tree.insert("", "end",
                        values=("☑", f"（待解析）{i}", "", "", "", "排队中"))
    w._rows[iid] = i
app.root.update()
print(f"  隐藏前：_busy={w._busy} 窗口可见={w.win.winfo_viewable()}")

# 点 ✕（内部调用 _hide_window）
w._hide_window()
for _ in range(6):
    app.root.update()
    time.sleep(0.06)

alive = w._alive()
viewable = False
try:
    viewable = bool(w.win.winfo_viewable())
except Exception:
    pass
print(f"  隐藏后：alive={alive} 可见={viewable} _busy={w._busy}")
check("W1 ✕ 不销毁窗口（仍存活）", alive is True)
check("W1b 窗口已隐藏（withdraw）", viewable is False)
check("W2 任务状态保留（_busy 仍 True）", w._busy is True)

# 隐藏期间后台回填 —— 关键：不能崩，数据要进表格
from sphdl.douyin import DouyinVideo
before_err = None
try:
    w.apply_item(0, DouyinVideo(aweme_id="1", title="隐藏期间解析的视频",
                                author="作者A", duration_ms=61000,
                                play_url="https://p", size=25_000_000,
                                width=1080, height=1920))
    w.apply_dl_prog(0, 12_500_000, 25_000_000, 1, 2)
    app.root.update()
except Exception as e:
    before_err = f"{type(e).__name__}: {e}"
print(f"  隐藏期间回填结果：错误={before_err}")
check("W3 隐藏期间能正常回填（不抛异常）", before_err is None, str(before_err))

iids = w.tree.get_children()
v0 = w.tree.item(iids[0], "values")
print(f"  隐藏期间写入的行: {v0}")
check("W3b 数据真的写进表格了", "隐藏期间解析的视频" in str(v0), str(v0[1]))

# 重新显示
w.win.deiconify()
app._on_douyin()
for _ in range(8):
    app.root.update()
    time.sleep(0.06)
try:
    vis2 = bool(w.win.winfo_viewable())
except Exception:
    vis2 = False
print(f"  重新显示：可见={vis2} _busy={w._busy}")
check("W4 重新显示能看到隐藏期间的数据", vis2 is True)
check("W4b 复用同一窗口（不新建）", app._dy_win is w)

print()
print("=" * 74)
print("W5 系统标题栏 X 走同一逻辑")
print("=" * 74)
proto = ""
try:
    proto = w.win.protocol("WM_DELETE_WINDOW")
except Exception:
    pass
print(f"  WM_DELETE_WINDOW = {proto!r}")
check("W5a 已绑定关闭协议", bool(proto))
check("W5b 绑的不是 destroy", "destroy" not in str(proto), str(proto))
check("W5c 源码里 protocol 绑定 _hide_window",
      'protocol("WM_DELETE_WINDOW", self._hide_window)' in src)

print()
print("=" * 74)
print("W8 最大化切换")
print("=" * 74)
try:
    st0 = w.win.state()
    w._toggle_max()
    for _ in range(6):
        app.root.update()
        time.sleep(0.06)
    st1 = w.win.state()
    print(f"  初始={st0} → 切换后={st1}")
    check("W8 最大化切换生效", st1 in ("zoomed", "normal") and st1 != st0,
          f"{st0} → {st1}")
    if st1 == "zoomed":
        check("W8b 按钮图标变还原符号", w.btn_max.cget("text") == "❐",
              w.btn_max.cget("text"))
    w._toggle_max()
    for _ in range(4):
        app.root.update()
        time.sleep(0.05)
    st2 = w.win.state()
    print(f"  再切换回来={st2}")
    check("W8c 能切回普通状态", st2 == "normal", st2)
except Exception as e:
    check("W8 最大化切换生效", False, f"{type(e).__name__}: {e}")

print()
print("=" * 74)
print("W9/W10 销毁后的安全 + 源码约束")
print("=" * 74)
try:
    w.win.destroy()
except Exception:
    pass
app.root.update()
err = None
try:
    w.apply_item(1, DouyinVideo(error="x"))
    w.apply_dl_prog(1, 1, 2, 1, 2)
    w.apply_all_done(1, 0, 1)
    app.root.update()
except Exception as e:
    err = f"{type(e).__name__}: {e}"
print(f"  销毁后回填：错误={err}")
check("W9 销毁后回填静默跳过（不抛异常）", err is None, str(err))

check("W10 源码没有把 ✕ 绑到 destroy",
      '_btn("✕", self.win.destroy' not in src)
check("W10b 存在 _hide_window", "def _hide_window" in src)
check("W10c 存在 _alive 保护", "def _alive" in src)

try:
    app.root.destroy()
except Exception:
    pass

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
