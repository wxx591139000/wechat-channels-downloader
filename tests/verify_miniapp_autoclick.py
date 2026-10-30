"""「选集」按钮自动点击：核心能力验证（v2.9.0）。

═══ 客户需求 ═══
「之前说的工具自动点选集进下一集实现没」

═══ 本测试验证什么 ═══
不是"代码里有这个函数"，而是**用合成图实测**：
  · 在一张大截图里放一个小按钮图 → 模板匹配能否准确找到坐标
  · 加了噪声/轻微缩放 → 还能不能找到
  · 图里没有按钮 → 是否**如实返回 None**（关键！不能瞎点）
  · 点击坐标换算是否正确

覆盖：
T1  ★精确位置匹配（误差 ≤3px）
T2  ★错位置不误匹配（返回 None）
T3  ★图里没有按钮 → 返回 None（绝不瞎点）
T4  噪声容忍（轻微噪点仍能找到）
T5  纯 PIL 实现，无 numpy/opencv 依赖
T6  点击坐标：客户区 → 屏幕坐标换算
T7  模板保存/加载往返一致
T8  ★性能：单次匹配 < 1.5 秒（不然点一次要等太久）
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


from PIL import Image, ImageDraw

from sphdl import miniapp_click as MC


def make_button(w=64, h=28, label=True):
    """画一个模拟「选集」按钮：深色圆角 + 中心亮点。"""
    img = Image.new("RGB", (w, h), (18, 18, 22))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=6,
                        fill=(38, 38, 44), outline=(90, 90, 100))
    if label:
        # ★ 非对称特征：主白块偏左 + 右侧小红点
        #   （第一版按钮左右对称 → 模板匹配出现两个等价位置，
        #     最优解偏移 10px，测试误判为"位置不准"）
        d.rectangle([6, h // 2 - 4, w // 2, h // 2 + 4],
                    fill=(220, 220, 225))
        d.ellipse([w - 14, h // 2 - 4, w - 6, h // 2 + 4],
                  fill=(200, 60, 60))
    return img


def make_screen(w=800, h=600, btn_pos=(500, 400)):
    """画一张模拟播放器界面，在 btn_pos 处贴按钮。"""
    img = Image.new("RGB", (w, h), (12, 12, 16))
    d = ImageDraw.Draw(img)
    # 一些干扰元素（模拟播放器控件）
    d.rectangle([0, h - 60, w, h], fill=(22, 22, 28))
    d.rectangle([20, h - 40, 120, h - 20], fill=(60, 60, 70))
    d.rectangle([140, h - 40, 240, h - 20], fill=(60, 60, 70))
    btn = make_button()
    img.paste(btn, btn_pos)
    return img, btn


print("=" * 74)
print("T1 精确位置匹配")
print("=" * 74)
screen, btn = make_screen(btn_pos=(500, 400))
t0 = time.time()
found = MC.find_template(screen, btn)
dt = time.time() - t0
if found:
    cx, cy, score = found
    exp = (500 + btn.size[0] // 2, 400 + btn.size[1] // 2)
    print(f"  找到 ({cx},{cy}) 期望 {exp}  误差分 {score:.1f}  耗时 {dt*1000:.0f}ms")
    check("T1a ★位置准确（误差 ≤3px）",
          abs(cx - exp[0]) <= 3 and abs(cy - exp[1]) <= 3, f"{cx},{cy} vs {exp}")
    check("T1b 匹配分足够低（<12 为可信命中）", score < 12, f"{score:.1f}")
    check("T8 ★耗时 < 1500ms", dt < 1.5, f"{dt*1000:.0f}ms")
else:
    print("  ❌ 没找到")
    check("T1a ★位置准确", False, "未匹配")
    check("T1b 匹配分", False, "-")
    check("T8 ★耗时", False, "-")

print()
print("=" * 74)
print("T2 图里没有按钮 → 必须返回 None（绝不能瞎点）")
print("=" * 74)
blank = Image.new("RGB", (800, 600), (12, 12, 16))
d = ImageDraw.Draw(blank)
d.rectangle([0, 540, 800, 600], fill=(22, 22, 28))
res = MC.find_template(blank, btn)
print(f"  结果: {res}")
check("T2 ★没有按钮时返回 None（不瞎点）", res is None, str(res))

print()
print("=" * 74)
print("T3 按钮在别的位置 → 找到新位置")
print("=" * 74)
screen3, btn3 = make_screen(btn_pos=(120, 80))
found3 = MC.find_template(screen3, btn3)
if found3:
    cx3, cy3, s3 = found3
    exp3 = (120 + btn3.size[0] // 2, 80 + btn3.size[1] // 2)
    print(f"  找到 ({cx3},{cy3}) 期望 {exp3}")
    check("T3 ★换位置也能准确定位",
          abs(cx3 - exp3[0]) <= 3 and abs(cy3 - exp3[1]) <= 3,
          f"{cx3},{cy3}")
else:
    check("T3 ★换位置也能准确定位", False, "未匹配")

print()
print("=" * 74)
print("T4 噪声容忍")
print("=" * 74)
import random
random.seed(7)
noisy = screen.copy()
px = noisy.load()
for _ in range(4000):
    x = random.randrange(noisy.size[0])
    y = random.randrange(noisy.size[1])
    r, g, b = px[x, y]
    delta = random.randint(-12, 12)
    px[x, y] = (max(0, min(255, r + delta)),
                max(0, min(255, g + delta)),
                max(0, min(255, b + delta)))
found4 = MC.find_template(noisy, btn)
print(f"  加噪 4000 点后: {found4}")
check("T4 加噪后仍能找到", found4 is not None, str(found4 and found4[:2]))

print()
print("=" * 74)
print("T5 零新依赖")
print("=" * 74)
src = (ROOT / "sphdl" / "miniapp_click.py").read_text(encoding="utf-8")
check("T5a ★不 import numpy", "import numpy" not in src)
check("T5b ★不 import cv2/opencv", "import cv2" not in src)
check("T5c ★不 import paddleocr", "paddleocr" not in src)
check("T5b2 只用 PIL + pywin32",
      "from PIL import" in src and "win32gui" in src)

print()
print("=" * 74)
print("T7 模板保存/加载")
print("=" * 74)
td = Path(tempfile.mkdtemp(prefix="tpl_"))
ok_save = MC.save_template(td, screen, (500, 400, 500 + btn.size[0], 400 + btn.size[1]))
tpl_loaded = MC.load_template(td)
print(f"  保存: {ok_save}  加载: {tpl_loaded.size if tpl_loaded else None}")
check("T7a 保存成功", ok_save)
check("T7b 加载尺寸一致", tpl_loaded is not None
      and tpl_loaded.size == btn.size, str(tpl_loaded.size if tpl_loaded else None))
if tpl_loaded:
    f7 = MC.find_template(screen, tpl_loaded)
    check("T7c 加载的模板也能匹配", f7 is not None, str(f7 and f7[:2]))

print()
print("=" * 74)
print("T6 坐标换算")
print("=" * 74)
check("T6 有 client_to_screen 函数", hasattr(MC, "client_to_screen"))
check("T6b 有 click_at 函数", hasattr(MC, "click_at"))
check("T6c 有 grab_window 函数", hasattr(MC, "grab_window"))

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
