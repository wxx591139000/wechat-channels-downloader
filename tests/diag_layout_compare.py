"""布局诊断：为什么日志区 geometry 有高度但截图看不见？

对比三种布局方式，找出真正能在 Windows Tk 上画出日志区的方案：
  A. pack + 双 expand（当前，geometry 说有 325px 但截图没画出来）
  B. pack + 日志 side=bottom + 固定高度
  C. grid + weight（最可靠）

每种都截图 + 测量实际像素（读图片像素判断底部有没有深色区域）
"""
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


def measure_dark_region(img_path, win_h):
    """读截图底部 40% 区域，统计深色像素（日志区 bg=#0b1020）。"""
    from PIL import Image
    im = Image.open(img_path).convert("RGB")
    w, h = im.size
    top = int(h * 0.55)
    dark = 0
    total = 0
    px = im.load()
    for y in range(top, h, 3):
        for x in range(0, w, 5):
            r, g, b = px[x, y]
            total += 1
            if r < 60 and g < 60 and b < 90:      # 深蓝黑
                dark += 1
    return dark / max(total, 1)


def build(mode):
    root = tk.Tk()
    root.title(f"layout-{mode}")
    root.geometry("1200x760")
    root.configure(bg="#f6f8fa")

    st = ttk.Style()
    if st.theme_use() in ("vista", "xpnative", "winnative"):
        st.theme_use("clam")

    main = ttk.Frame(root)
    main.pack(fill="both", expand=True, padx=8, pady=8)

    if mode == "A":
        # A：双 expand（当前实现）
        list_frame = ttk.LabelFrame(main, text=" 视频列表 ")
        list_frame.pack(fill="both", expand=True, pady=8)
        logf = ttk.LabelFrame(main, text=" 日志 ", padding=4)
        logf.pack(fill="both", expand=True, pady=(0, 0))
        tree_parent = list_frame
        log_parent = logf
    elif mode == "B":
        # B：日志 side=bottom + 固定高度（先 pack）
        logf = ttk.LabelFrame(main, text=" 日志 ", padding=4)
        logf.pack(side="bottom", fill="x", pady=(0, 0))
        list_frame = ttk.LabelFrame(main, text=" 视频列表 ")
        list_frame.pack(fill="both", expand=True, pady=8)
        tree_parent = list_frame
        log_parent = logf
    else:
        # C：grid + weight
        main.rowconfigure(0, weight=3)
        main.rowconfigure(1, weight=1)
        main.columnconfigure(0, weight=1)
        list_frame = ttk.LabelFrame(main, text=" 视频列表 ")
        list_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 6))
        logf = ttk.LabelFrame(main, text=" 日志 ", padding=4)
        logf.grid(row=1, column=0, sticky="nsew")
        tree_parent = list_frame
        log_parent = logf

    tree = ttk.Treeview(tree_parent, columns=("a",), show="headings", height=8)
    tree.heading("a", text="标题")
    for i in range(15):
        tree.insert("", "end", values=(f"视频 {i}",))
    tree.pack(fill="both", expand=True)

    log_text = tk.Text(log_parent, height=8, bg="#0b1020", fg="#d1d5db",
                       font=("Consolas", 9), wrap="word")
    log_text.pack(fill="both", expand=True)
    for i in range(10):
        log_text.insert("end", f"[{i:02d}] 日志内容测试行\n")

    root.update()
    root.update_idletasks()
    root.lift()
    root.attributes("-topmost", True)
    root.update()
    time.sleep(0.8)

    geo = {
        "log_h": logf.winfo_height(),
        "log_y": logf.winfo_rooty() - root.winfo_rooty(),
        "text_h": log_text.winfo_height(),
        "list_h": list_frame.winfo_height(),
        "win_h": root.winfo_height(),
    }

    shot = Path(f"C:/Users/Dancing/AppData/Local/Temp/layout_{mode}.png")
    try:
        from PIL import ImageGrab
        x, y = root.winfo_rootx(), root.winfo_rooty()
        w, h = root.winfo_width(), root.winfo_height()
        ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(shot)
    except Exception as e:
        print(f"  截图失败: {e}")

    root.destroy()
    return geo, shot


print("=" * 74)
print("三种布局方式对比（关键：geometry 说的高度 vs 截图里真实的深色像素）")
print("=" * 74)

results = {}
for mode in ("A", "B", "C"):
    geo, shot = build(mode)
    dark = measure_dark_region(shot, geo["win_h"]) if shot.exists() else 0
    results[mode] = (geo, dark, shot)
    print(f"\n【{mode}】")
    print(f"  geometry: 日志区 {geo['log_h']}px (y={geo['log_y']}) "
          f"文本 {geo['text_h']}px 列表 {geo['list_h']}px")
    print(f"  ★截图深色占比: {dark*100:.1f}%（底部 45% 区域内）")
    print(f"  截图: {shot}")

print()
print("=" * 74)
print("判定")
print("=" * 74)
best = None
for mode, (geo, dark, _) in results.items():
    ok = dark > 0.05 and geo["text_h"] >= 100
    print(f"  {mode}: 深色 {dark*100:5.1f}%  文本高 {geo['text_h']:3d}px  "
          f"{'✅ 真的画出来了' if ok else '❌ 没画出来'}")
    if ok and (best is None or dark > results[best][1]):
        best = mode

print()
if best:
    print(f"→ 最佳方案: {best}")
    print(f"  日志区高度 {results[best][0]['log_h']}px，"
          f"深色占比 {results[best][1]*100:.1f}%")
else:
    print("→ 三种都不行，需要进一步排查")

check("至少一种方案能让日志区真正显示", best is not None,
      f"最佳={best}" if best else "")
sys.exit(0 if best else 1)
