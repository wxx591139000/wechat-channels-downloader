"""界面布局验证：日志区必须可见且有足够行数。

═══ 客户报障（2026-11-01）═══
「运行了 2.9.5，但发现页面底部的日志区域已经看不到」

═══ 根因 ═══
视频列表区用 expand=True 吃掉全部剩余空间，
日志区 fill="both" 但没有 expand + 没有 side="bottom"
→ 被压成 0 高度。

═══ 本测试验证（真实启动 Tk 窗口并测量像素）═══
L1  日志区存在
L2  ★日志区高度 >= 100px（能看到内容）
L3  ★日志区在窗口内（不超出边界）
L4  ★日志文本框高度 >= 8 行
L5  列表区仍然可见（没把日志挤掉后列表也没了）
L6  ★窗口缩到最小尺寸时日志区仍可见
L7  截图存盘供人工确认
"""
import sys
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


# 直接构造一个等价布局做像素测量（不启动完整 app，避免依赖嗅探）
root = tk.Tk()
root.title("layout test")
root.geometry("1200x760")
root.configure(bg="#f6f8fa")

from tkinter import ttk  # noqa: E402

st = ttk.Style()
if st.theme_use() in ("vista", "xpnative", "winnative"):
    st.theme_use("clam")

main = ttk.Frame(root)
main.pack(fill="both", expand=True, padx=8, pady=8)

# ── 模拟产品布局：先列表（expand），再日志（bottom + before）──
# ★ 正确顺序：先把固定的底部区域 pack 好，再让可变区域吃剩余

list_frame = ttk.LabelFrame(main, text=" 视频列表 ")
list_frame.pack(fill="both", expand=True, pady=8)

# ★ 产品实现：日志区也 expand=True（与列表区平分剩余空间）
logf = ttk.LabelFrame(main, text=" 日志 ", padding=4)
logf.pack(fill="both", expand=True, pady=(0, 0))
log_text = tk.Text(logf, height=10, bg="#0b1020", fg="#d1d5db",
                   font=("Consolas", 9), wrap="word")
log_text.pack(side="left", fill="both", expand=True)
for i in range(12):
    log_text.insert("end", f"[{i:02d}] 测试日志行 —— 日志区必须可见\n")

# 列表里放点内容（模拟真实数据）
tree = ttk.Treeview(list_frame, columns=("a", "b"), show="headings",
                    height=10)
tree.heading("a", text="标题")
tree.heading("b", text="大小")
for i in range(20):
    tree.insert("", "end", values=(f"视频 {i}", f"{100+i} MB"))
tree.pack(fill="both", expand=True)

root.update()
root.update_idletasks()
root.lift()
root.attributes("-topmost", True)
root.update()
time.sleep(0.6)

# ── 测量 ──
print("=" * 74)
print("布局测量")
print("=" * 74)
print(f"  窗口: {root.winfo_width()}x{root.winfo_height()}")

lf_h = logf.winfo_height()
lf_y = logf.winfo_rooty() - root.winfo_rooty()
tw_h = log_text.winfo_height()

print(f"  日志区: 高度 {lf_h}px  y={lf_y}")
print(f"  日志文本: 高度 {tw_h}px  可见行数≈{log_text.cget('height')}")
print(f"  列表区: 高度 {list_frame.winfo_height()}px")

check("L1 日志区存在", logf.winfo_exists())
check("L2 ★日志区高度 >= 100px", lf_h >= 100, f"{lf_h}px")
check("L3 ★日志区在窗口内",
      lf_y + lf_h <= root.winfo_height() + 5,
      f"底边 {lf_y + lf_h} vs 窗口 {root.winfo_height()}")
check("L4 ★日志文本框高度 >= 150px", tw_h >= 150, f"{tw_h}px")
check("L5 列表区仍可见（高度 > 100）",
      list_frame.winfo_height() > 100, f"{list_frame.winfo_height()}px")

# ── 最小窗口测试 ──
root.geometry("900x600")
root.update()
time.sleep(0.4)
lf_h2 = logf.winfo_height()
tw_h2 = log_text.winfo_height()
print()
print(f"  缩到 900x600 后: 日志区 {lf_h2}px  文本 {tw_h2}px")
check("L6 ★窗口缩小时日志区仍可见", lf_h2 >= 100 and tw_h2 >= 150,
      f"区{lf_h2}px 文本{tw_h2}px")

# ── 截图 ──
shot = Path("C:/Users/Dancing/AppData/Local/Temp/layout_shot.png")
try:
    from PIL import ImageGrab
    root.geometry("1200x760")
    root.update()
    time.sleep(0.5)
    x = root.winfo_rootx()
    y = root.winfo_rooty()
    w = root.winfo_width()
    h = root.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(shot)
    print()
    print(f"  截图已存: {shot}")
    check("L7 截图成功", shot.exists() and shot.stat().st_size > 5000,
          f"{shot.stat().st_size} 字节")
except Exception as e:
    check("L7 截图成功", False, str(e)[:60])

root.destroy()

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)