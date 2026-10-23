#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成闲鱼商品配图（公众号视频下载器）。

闲鱼图片要求（实测经验）：
  · 首图必须 1 秒看懂卖什么（大标题 + 大字 + 高对比）
  · 手机上看，字不能小（最小 28px @1080 宽）
  · 不要堆满文字，一张图讲一个点
  · 尺寸 1080×1440（3:4）在闲鱼列表里显示最完整

产出 3 张：
  01_封面.png    主卖点（最抓眼）
  02_三步操作.png  怎么用（降低决策门槛）
  03_功能对比.png  为什么选这个（差异化）

用法：python tools/make_xianyu_images.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ★ 平台销售资料统一放在 docs/平台销售资料/<平台>/ 下，不散落在 docs 根目录
OUT = (Path(__file__).resolve().parent.parent.parent / "docs"
       / "平台销售资料" / "闲鱼" / "配图-公众号版")
OUT.mkdir(parents=True, exist_ok=True)

W, H = 1080, 1440

# 配色（深蓝主色 + 亮青点缀，和软件 UI 一致）
BG = "#0f2744"
BG2 = "#16365c"
CARD = "#1e4976"
ACCENT = "#4fc3f7"
WHITE = "#ffffff"
GREY = "#a8c0d8"
GREEN = "#4ade80"
ORANGE = "#fbbf24"

FONT_DIR = Path("C:/Windows/Fonts")
F_BOLD = FONT_DIR / "msyhbd.ttc"
F_REG = FONT_DIR / "msyh.ttc"


def font(size: int, bold: bool = False):
    return ImageFont.truetype(str(F_BOLD if bold else F_REG), size)


def center_text(d: ImageDraw.ImageDraw, y, text, f, fill=WHITE):
    bbox = d.textbbox((0, 0), text, font=f)
    w = bbox[2] - bbox[0]
    d.text(((W - w) // 2, y), text, font=f, fill=fill)
    return y + (bbox[3] - bbox[1])


def left_text(d, x, y, text, f, fill=WHITE):
    d.text((x, y), text, font=f, fill=fill)
    bbox = d.textbbox((x, y), text, font=f)
    return bbox[3]


def gradient_bg():
    """深蓝垂直渐变背景。"""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    top = (15, 39, 68)
    bot = (22, 54, 92)
    for y in range(H):
        t = y / H
        c = tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3))
        d.line([(0, y), (W, y)], fill=c)
    return img


def rounded_card(d, box, radius=24, fill=CARD, outline=None, width=2):
    x1, y1, x2, y2 = box
    d.rounded_rectangle(box, radius=radius, fill=fill,
                        outline=outline, width=width)


# ============================================================
# 图 1：封面
# ============================================================
def make_cover():
    img = gradient_bg()
    d = ImageDraw.Draw(img)

    # 顶部小标签
    y = 70
    tag = "公众号文章视频专用版"
    f_tag = font(34, True)
    bbox = d.textbbox((0, 0), tag, font=f_tag)
    tw = bbox[2] - bbox[0]
    d.rounded_rectangle([(W - tw) // 2 - 34, y, (W + tw) // 2 + 34, y + 64],
                        radius=32, fill="#1e4976", outline=ACCENT, width=2)
    d.text(((W - tw) // 2, y + 10), tag, font=f_tag, fill=ACCENT)

    # 主标题（两行，超大字）
    y = 200
    y = center_text(d, y, "公众号视频", font(120, True))
    y = center_text(d, y + 10, "批量下载器", font(120, True), ACCENT)

    # 副标题
    y += 40
    y = center_text(d, y, "粘个链接，文章里的视频全给你扒出来", font(44), "#c9dcf0")

    # 三个核心卖点卡片
    y += 90
    # ★ 不写「不用装证书/不用开代理」—— 会显得在踩视频号版或其他工具。
    #   只讲公众号版自己怎么用（粘链接、看清单、批量存）。
    items = [
        "粘个链接，视频全列出来",
        "一篇文章的视频一次全抓",
        "批量下载 / 单个按需下载",
    ]
    y += 30                      # ★ 卖点区整体下移，填补底部空白
    f_item = font(50, True)
    for text in items:
        rounded_card(d, (80, y, W - 80, y + 132), radius=20)
        # ★ 用几何图形画对勾（微软雅黑不含 emoji，✅ 会变成空心方框）
        cx, cy = 150, y + 66
        d.ellipse([cx - 32, cy - 32, cx + 32, cy + 32], fill=GREEN)
        d.line([(cx - 15, cy + 1), (cx - 4, cy + 14)], fill=BG, width=8)
        d.line([(cx - 4, cy + 14), (cx + 16, cy - 13)], fill=BG, width=8)
        d.text((205, y + 36), text, font=f_item, fill=WHITE)
        y += 156

    # 底部强调条
    y = H - 200
    rounded_card(d, (80, y, W - 80, y + 140), radius=24,
                 fill="#0d3a2e", outline=GREEN, width=3)
    center_text(d, y + 18, "先解析看清单，确认好用再买", font(50, True), GREEN)
    center_text(d, y + 84, "拉取免费 · 存到本地需授权", font(36), WHITE)

    p = OUT / "01_封面.png"
    img.save(p, quality=95)
    print(f"✅ {p.name}")
    return p


# ============================================================
# 图 2：三步操作
# ============================================================
def make_steps():
    img = gradient_bg()
    d = ImageDraw.Draw(img)

    y = 70
    y = center_text(d, y, "三步搞定", font(76, True)) + 10
    y = center_text(d, y, "不用教程也能上手", font(36), GREY)

    y += 70
    steps = [
        ("1", "复制链接",
         "微信打开文章 → 右上角「…」\n→ 复制链接"),
        ("2", "粘贴解析",
         "粘进软件 → 点「解析并加入清单」\n文章里的视频全部列出来"),
        ("3", "全选下载",
         "点「全选」→ 点「下载选中」\n排队自动下完，本地 MP4"),
    ]

    f_num = font(72, True)
    f_title = font(48, True)
    f_desc = font(32)

    for num, title, desc in steps:
        card_h = 250
        rounded_card(d, (70, y, W - 70, y + card_h), radius=24)

        # 序号圆
        cx, cy, r = 140, y + 125, 48
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=ACCENT)
        bbox = d.textbbox((0, 0), num, font=f_num)
        d.text((cx - (bbox[2] - bbox[0]) // 2, cy - (bbox[3] - bbox[1]) // 2 - 8),
               num, font=f_num, fill=BG)

        # 标题 + 描述
        d.text((220, y + 42), title, font=f_title, fill=WHITE)
        ty = y + 118
        for line in desc.split("\n"):
            d.text((220, ty), line, font=f_desc, fill=GREY)
            ty += 46

        y += card_h + 36

    # 底部提示
    y = H - 210
    rounded_card(d, (70, y, W - 70, y + 150), radius=24,
                 fill="#3d2f0a", outline=ORANGE, width=3)
    # ★ 原写「不需要装证书/不用改系统代理」+「和视频号版本最大的区别」= 踩同行嫌疑，
    #   且会让人以为在贬低自家视频号版。改为只讲本产品怎么做。
    center_text(d, y + 20, "复制链接就能用，全程三步搞定", font(44, True), ORANGE)
    center_text(d, y + 84, "解析出清单后，可先确认再决定下哪些", font(32), WHITE)

    p = OUT / "02_三步操作.png"
    img.save(p, quality=95)
    print(f"✅ {p.name}")
    return p


# ============================================================
# 图 3：功能清单 / 差异化
# ============================================================
def make_features():
    img = gradient_bg()
    d = ImageDraw.Draw(img)

    y = 70
    y = center_text(d, y, "为什么选这个", font(72, True)) + 10
    # ★ 原为「不是那种点一下就提示「已失效」的假把式」= 直接贬低同类工具，
    #   改为客观列出能力（客户自己会比较）。
    y = center_text(d, y, "8 个实用细节，日常下载够用", font(34), GREY)

    y += 60
    feats = [
        ("粘链接就出清单", "文章里 5 个视频？一次全列出来"),
        ("自动选最高画质", "多清晰度自动合并去重，取原画质"),
        ("批量 / 单个都行", "全选下、Ctrl 加选、Shift 连选、只下一条"),
        ("带序号，按顺序下", "想只下前几条？Shift 点范围就行"),
        ("断点续传", "中断了不用从头再来，点「重试失败」接着下"),
        ("标准 MP4", "手机电脑都能放，离线随时看"),
        ("绿色便携版", "解压即用，不用装 Python"),
        ("一码一档可追溯", "正规售卖，非破解版"),
    ]

    f_num2 = font(40, True)
    f_title = font(40, True)
    f_desc = font(28)

    card_h = 112
    for i, (title, desc) in enumerate(feats, 1):
        rounded_card(d, (70, y, W - 70, y + card_h), radius=18)
        # ★ 几何圆形序号（替代 emoji —— 雅黑不含 emoji 会变方框）
        cx, cy, r = 128, y + 56, 30
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=ACCENT)
        num = str(i)
        bbox = d.textbbox((0, 0), num, font=f_num2)
        d.text((cx - (bbox[2]-bbox[0])//2, cy - (bbox[3]-bbox[1])//2 - 4),
               num, font=f_num2, fill=BG)
        d.text((185, y + 16), title, font=f_title, fill=WHITE)
        d.text((185, y + 66), desc, font=f_desc, fill=GREY)
        y += card_h + 16

    p = OUT / "03_功能清单.png"
    img.save(p, quality=95)
    print(f"✅ {p.name}")
    return p


if __name__ == "__main__":
    print("生成闲鱼配图 →", OUT)
    print()
    ps = [make_cover(), make_steps(), make_features()]
    print()
    for p in ps:
        with Image.open(p) as im:
            print(f"  {p.name}: {im.size[0]}×{im.size[1]}  {p.stat().st_size/1024:.0f} KB")
