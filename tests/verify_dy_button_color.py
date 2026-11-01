"""抖音按钮文字可见性验证（客户报障：字看不见）。

客户：「刚对抖音做了授权，发现『抖音批量下载』几个字看不见，颜色要改」

先查根因：
  ttk.Button 在 Windows 的默认主题（vista/xpnative）下，
  **foreground 常被主题忽略** —— 配了白色，实际画出来可能是别的颜色，
  或者背景没生效导致白字白底。

本测试用真实 Tk 窗口渲染，然后把按钮区域**截图**，
读取像素判断：文字区域和背景的对比度是否足够（WCAG 4.5:1）。

覆盖：
C1  能创建按钮并应用 Dy.TButton 样式
C2  ★实际渲染后，文字像素与背景像素有明显差异（不是白字白底）
C3  ★对比度 >= 3.0（大字/粗体的最低可读标准）
C4  未授权态（DyLocked）也能看清
"""
import sys
import tkinter as tk
from tkinter import ttk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


def luminance(rgb):
    """相对亮度（WCAG）。"""
    def f(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb[:3]
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(c1, c2):
    l1, l2 = luminance(c1), luminance(c2)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


root = tk.Tk()
root.title("btn color test")
root.geometry("420x160")
root.configure(bg="#f6f8fa")

st = ttk.Style()
print(f"当前主题: {st.theme_use()}")
print(f"可用主题: {st.theme_names()}")
print()
print("═══ 各主题下 ttk.Button 是否支持自定义颜色 ═══")
print("（vista/xpnative 是原生主题，会忽略 background/foreground；")
print("  clam/alt/default/classic 是 Tk 自绘主题，支持颜色）")
for th in st.theme_names():
    try:
        st.theme_use(th)
        st.configure("T.TButton", background="#26a69a")
        got = st.lookup("T.TButton", "background")
        print(f"  {th:10s} → background 生效值: {got!r}  "
              f"{'❌ 忽略' if str(got) in ('', 'None') or str(got) == '#26a69a' and th in ('vista','xpnative') else ''}")
    except Exception as e:
        print(f"  {th:10s} → 测试失败: {e}")
# ★ 产品行为：切到 clam 才支持自定义配色
st.theme_use("clam")
print()
print(f"产品实际使用主题: {st.theme_use()}")

# 应用与产品一致的样式
st.configure("Dy.TButton", background="#26a69a", foreground="#ffffff")
st.map("Dy.TButton", background=[("active", "#4db6ac"),
                                 ("pressed", "#00897b")])
st.configure("DyLocked.TButton", background="#eceff1", foreground="#78909c")

f = tk.Frame(root, bg="#f6f8fa")
f.pack(pady=20)

b1 = ttk.Button(f, text="🎵 抖音批量下载", width=17, style="Dy.TButton")
b1.pack(side="left", padx=8)
b2 = ttk.Button(f, text="🎵 抖音批量下载 🔒", width=17, style="DyLocked.TButton")
b2.pack(side="left", padx=8)

tk.Label(root, text="↑ 已授权态（应为青底白字，清晰可见）",
         bg="#f6f8fa").pack()
root.update()
root.update_idletasks()

check("C1 两个按钮都创建成功", b1.winfo_exists() and b2.winfo_exists())

# ══════ 截图并分析像素 ══════
try:
    from PIL import ImageGrab
    root.update()
    root.lift()
    root.attributes("-topmost", True)
    root.update()
    import time
    time.sleep(0.8)

    x = b1.winfo_rootx()
    y = b1.winfo_rooty()
    w = b1.winfo_width()
    h = b1.winfo_height()
    print(f"按钮1 位置 ({x},{y}) 尺寸 {w}x{h}")

    img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    img.save("C:/Users/Dancing/AppData/Local/Temp/btn_shot.png")
    px = img.load()
    iw, ih = img.size

    # 统计颜色
    from collections import Counter
    cnt = Counter()
    for yy in range(ih):
        for xx in range(iw):
            cnt[px[xx, yy][:3]] += 1
    top = cnt.most_common(6)
    print("按钮区域主要颜色:")
    for col, n in top:
        print(f"  {col}  x{n}  ({n*100//(iw*ih)}%)")

    bg = top[0][0]
    # 文字像素 = 与背景差异大的
    txt_px = [c for c, n in cnt.items()
              if abs(c[0] - bg[0]) + abs(c[1] - bg[1]) + abs(c[2] - bg[2]) > 60]
    print(f"\n背景色: {bg}")
    print(f"文字候选色: {txt_px[:5]}")

    check("C2 ★文字与背景有差异（不是白字白底）", bool(txt_px),
          f"{len(txt_px)} 种文字色")

    if txt_px:
        # 取出现最多的文字色
        tcol = max(txt_px, key=lambda c: cnt[c])
        cr = contrast(bg, tcol)
        print(f"取文字色 {tcol}，对比度 {cr:.2f}")
        check("C3 ★对比度 >= 3.0", cr >= 3.0, f"{cr:.2f}")

    print(f"\n背景色实际是: {bg}")
    print(f"期望背景 #26a69a = {(38, 166, 154)}")
    is_teal = abs(bg[0] - 38) < 30 and abs(bg[1] - 166) < 30 and abs(bg[2] - 154) < 30
    check("C4 ★背景色真的生效了（青绿 #26a69a）", is_teal, str(bg))

    # 检查文字是白色吗
    if txt_px:
        tcol = max(txt_px, key=lambda c: cnt[c])
        is_white = all(v > 200 for v in tcol[:3])
        print(f"文字色 {tcol} 是否接近白色: {is_white}")
        check("C5 ★文字真的是白色", is_white, str(tcol))

    # 顺便截未授权态
    x2 = b2.winfo_rootx()
    img2 = ImageGrab.grab(bbox=(x2, y, x2 + b2.winfo_width(),
                                y + b2.winfo_height()))
    img2.save("C:/Users/Dancing/AppData/Local/Temp/btn_shot2.png")
    cnt2 = Counter()
    p2 = img2.load()
    for yy in range(img2.size[1]):
        for xx in range(img2.size[0]):
            cnt2[p2[xx, yy][:3]] += 1
    bg2 = cnt2.most_common(1)[0][0]
    txt2 = [c for c, n in cnt2.items()
            if abs(c[0] - bg2[0]) + abs(c[1] - bg2[1]) + abs(c[2] - bg2[2]) > 60]
    if txt2:
        tcol2 = max(txt2, key=lambda c: cnt2[c])
        cr2 = contrast(bg2, tcol2)
        print(f"\n未授权态: 背景 {bg2} 文字 {tcol2} 对比度 {cr2:.2f}")
        check("C6 未授权态也可读（对比度 >= 2.5）", cr2 >= 2.5, f"{cr2:.2f}")
except Exception as e:
    import traceback
    print(f"截图分析失败: {e}")
    traceback.print_exc()
    check("C2 ★文字与背景有差异", False, "截图失败")

root.destroy()

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
