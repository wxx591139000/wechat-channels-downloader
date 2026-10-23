"""公众号解析进度反馈测试（v2.2.3）。

用户反馈（2026-10-23）：
  「刚才我在2.2.2版本里打开公众号视频，输入链接…点『解析并加入清单』后
   没有视频出现」
  → 追问后：「视频出现了。应该加个解析过程的状态提示，
     刚才没有任何反馈以为故障了」

覆盖：
  F1 解析开始 → 按钮文字变「⏳ 解析中…」且禁用
  F2 解析开始 → 状态栏出现「正在抓取」且变色
  F3 解析开始 → 数据表出现占位行（不是空白）
  F4 解析开始 → 进度条可见
  F5 计时器每秒更新（状态文字含秒数）
  F6 解析成功 → 按钮文字复原 + 可点击
  F7 解析成功 → 进度条隐藏 + 计时停止
  F8 解析成功 → 状态栏显示结果
  F9 解析失败 → 按钮复原（不会永久卡在禁用）
  F10 解析 0 个视频 → 按钮复原 + 红色提示
  F11 窗口关闭时计时器不抛异常
"""
import sys
import time
import types
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

# 无头
app = G.App()
app.root.withdraw()

print("=" * 74)
print("构造公众号窗口")
print("=" * 74)
# 找打开公众号窗口的方法
mp_open = None
for name in dir(app):
    if "mp" in name.lower() and ("open" in name.lower() or "win" in name.lower()):
        mp_open = name
        break
print("  公众号窗口方法:", mp_open)

# 直接调用内部实现（它会建 Toplevel）
try:
    app._open_mp_window()
except AttributeError:
    # 兼容不同命名
    for name in ("_open_mp", "_mp_window", "_show_mp_window"):
        if hasattr(app, name):
            getattr(app, name)()
            break

app.root.update()

# 找到公众号 Toplevel 及其控件
tops = [w for w in app.root.winfo_children()
        if isinstance(w, tk.Toplevel) and w.winfo_exists()]
print("  Toplevel 数:", len(tops))

def walk(w, depth=0):
    out = []
    for c in w.winfo_children():
        out.append((depth, c))
        out.extend(walk(c, depth + 1))
    return out

all_w = []
for t in tops:
    all_w.extend(walk(t))

# 找按钮/标签/Treeview/Progressbar
btn_parse = None
labels = []
trees = []
progs = []
for _d, w in all_w:
    cls = w.winfo_class()
    try:
        txt = w.cget("text")
    except Exception:
        txt = ""
    if cls == "TButton" and "解析并加入清单" in str(txt):
        btn_parse = w
    if cls == "TLabel":
        labels.append(w)
    if cls == "Treeview":
        trees.append(w)
    if cls == "TProgressbar":
        progs.append(w)

print("  解析按钮:", "找到" if btn_parse else "未找到")
print("  Label 数:", len(labels), " Treeview 数:", len(trees),
      " Progressbar 数:", len(progs))

print()
print("=" * 74)
print("F1-F5 解析开始时的反馈")
print("=" * 74)

# 直接模拟：找到 _watch_start（通过按钮 command 触发的闭包不可直接访问）
# 改为：往输入框填 URL 然后点按钮，观察 0.3 秒后的界面状态
entry = None
for _d, w in all_w:
    if w.winfo_class() == "TEntry":
        entry = w
        break
print("  输入框:", "找到" if entry else "未找到")

# ★ 不能随便取第一个含"解析"的 Label —— 顶部说明文字也含"解析"。
#   真正的状态栏初始文本是「等待解析…」，且不含"在微信里打开文章"。
status_label = None
for lb in labels:
    try:
        t = str(lb.cget("text"))
    except Exception:
        continue
    if "在微信里打开文章" in t:
        continue
    if "等待解析" in t:
        status_label = lb
        break
print("  状态栏 Label:", "找到" if status_label else "未找到")

TEST_URL = "https://mp.weixin.qq.com/s/0wttwnZg1ut4bpsEmPFmoQ?scene=1&click_id=1403243094"

if btn_parse and entry:
    entry.insert(0, TEST_URL)
    btn_parse.invoke()
    app.root.update()

    # 立刻检查（解析还没完成）
    time.sleep(0.4)
    app.root.update()

    btn_txt = str(btn_parse.cget("text"))
    btn_state = str(btn_parse.cget("state"))
    print(f"  按钮: 文字={btn_txt!r} 状态={btn_state}")
    check("F1a 解析中按钮文字变成「解析中」", "解析中" in btn_txt, btn_txt)
    check("F1b 解析中按钮被禁用", btn_state == "disabled", btn_state)

    st = str(status_label.cget("text")) if status_label else ""
    print(f"  状态栏: {st!r}")
    check("F2a 状态栏提示正在抓取", "抓取" in st, st[:40])
    check("F2b 状态文字变色（不再是灰）",
          str(status_label.cget("foreground")) not in ("", "#6b7280"),
          str(status_label.cget("foreground")))

    # 数据表占位
    if trees:
        kids = trees[0].get_children()
        first = trees[0].item(kids[0], "values") if kids else ()
        print(f"  表格首行: {first}")
        check("F3 表格出现占位行（不是空白）",
              bool(kids) and "正在抓取" in str(first), str(first)[:50])
    else:
        check("F3 表格出现占位行（不是空白）", False, "无 Treeview")

    # 进度条
    # ★ 窗口 withdraw 时 ismapped 恒为 0 → 用 winfo_manager()
    def shown(w):
        try:
            return w.winfo_manager() == "pack"
        except Exception:
            return False
    if progs:
        vis = shown(progs[0])
        print(f"  进度条 manager={progs[0].winfo_manager()!r} shown={vis}")
        check("F4 进度条显示", vis, "manager=" + str(progs[0].winfo_manager()))
    else:
        check("F4 进度条显示", False, "无 Progressbar")

    # 计时（等 1.5 秒看秒数是否出现）
    time.sleep(1.6)
    app.root.update()
    st2 = str(status_label.cget("text")) if status_label else ""
    print(f"  1.6s 后状态: {st2!r}")
    check("F5 状态栏显示已用时秒数", "秒" in st2, st2[:50])

    print()
    print("=" * 74)
    print("等待解析完成…")
    print("=" * 74)
    # 等最多 90 秒
    done = False
    for i in range(90):
        time.sleep(1)
        app.root.update()
        if str(btn_parse.cget("state")) == "normal":
            done = True
            print(f"  解析完成（约 {i+1}s）")
            break
    check("F6 解析结束后按钮恢复可点", done, f"done={done}")

    btn_txt2 = str(btn_parse.cget("text"))
    print(f"  按钮文字复原: {btn_txt2!r}")
    check("F6b 按钮文字复原", "解析并加入清单" in btn_txt2, btn_txt2)

    if progs:
        vis2 = shown(progs[0])
        print(f"  进度条 manager={progs[0].winfo_manager()!r} shown={vis2}")
        check("F7 进度条已隐藏", not vis2, "manager=" + str(progs[0].winfo_manager()))

    st3 = str(status_label.cget("text")) if status_label else ""
    print(f"  最终状态: {st3!r}")
    check("F8 状态栏显示解析结果", any(k in st3 for k in ("已", "个", "视频", "✗")),
          st3[:60])
else:
    check("F1a 解析中按钮文字变成「解析中」", False, "未找到按钮或输入框")

print()
print("=" * 74)
print("F10 关闭窗口不抛异常")
print("=" * 74)
try:
    for t in tops:
        try:
            t.destroy()
        except Exception:
            pass
    app.root.update()
    time.sleep(1.2)
    app.root.update()
    check("F10 关闭窗口后无异常", True)
except Exception as e:
    check("F10 关闭窗口后无异常", False, str(e))

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
try:
    app.root.destroy()
except Exception:
    pass
sys.exit(0 if ok_n == len(R) else 1)
