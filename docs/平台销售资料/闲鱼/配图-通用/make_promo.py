# -*- coding: utf-8 -*-
"""生成 微信视频号下载器 平台宣传图（效果图风格，PIL 渲染）。"""
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent
YAHEI = r"C:\Windows\Fonts\msyh.ttc"
YAHEI_B = r"C:\Windows\Fonts\msyhbd.ttc"

RED = (255, 60, 95)        # 主红
RED_D = (214, 40, 72)
PINK_BG = (255, 247, 249)
PINK2 = (255, 233, 239)
INK = (31, 41, 55)         # 深字
GRAY = (107, 114, 128)
BLUE = (37, 99, 235)
GREEN = (16, 185, 129)
DARK = (11, 16, 32)
WHITE = (255, 255, 255)
BORDER = (229, 231, 235)
ROW_ALT = (249, 250, 251)


def F(sz):
    return ImageFont.truetype(YAHEI, sz)


def FB(sz):
    return ImageFont.truetype(YAHEI_B, sz)


def tsize(d, text, font):
    b = d.textbbox((0, 0), text, font=font)
    return b[2] - b[0], b[3] - b[1]


def ctext(d, cx, cy, text, font, fill):
    w, h = tsize(d, text, font)
    d.text((cx - w / 2, cy - h / 2), text, font=font, fill=fill)


def ltext(d, x, cy, text, font, fill):
    _, h = tsize(d, text, font)
    d.text((x, cy - h / 2), text, font=font, fill=fill)


def rtext(d, xr, cy, text, font, fill):
    w, h = tsize(d, text, font)
    d.text((xr - w, cy - h / 2), text, font=font, fill=fill)


def vgrad(w, h, top, bottom):
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        for x in range(w):
            px[x, y] = c
    return img


def check_box(d, x, cy, checked=True, size=15):
    y0 = cy - size / 2
    d.rounded_rectangle((x, y0, x + size, y0 + size), radius=3,
                        fill=RED if checked else WHITE,
                        outline=RED if checked else BORDER, width=2)
    if checked:
        d.line((x + 3, y0 + size / 2, x + 6, y0 + size - 3), fill=WHITE, width=2)
        d.line((x + 6, y0 + size - 3, x + size - 2, y0 + 3), fill=WHITE, width=2)


def btn(d, x, y, w, h, label, font, bg, fg, radius=7):
    d.rounded_rectangle((x, y, x + w, y + h), radius=radius, fill=bg)
    ctext(d, x + w / 2, y + h / 2, label, font, fg)


def entry(d, x, y, w, h, text, font, fill=INK, bg=WHITE):
    d.rounded_rectangle((x, y, x + w, y + h), radius=6, fill=bg, outline=BORDER, width=2)
    ltext(d, x + 10, y + h / 2, text, font, fill)


def chip(d, x, y, w, h, label, bg, fg, font):
    d.rounded_rectangle((x, y, x + w, y + h), radius=h / 2, fill=bg)
    ctext(d, x + w / 2, y + h / 2, label, font, fg)


def icon_down(d, cx, cy, s, color):
    d.line((cx, cy - s / 2, cx, cy + s / 2 - 2), fill=color, width=4)
    d.polygon([(cx - s / 2, cy + s / 2 - 2), (cx + s / 2, cy + s / 2 - 2),
               (cx, cy + s / 2 + 3)], fill=color)


def icon_stack(d, cx, cy, s, color):
    for i, dy in enumerate((0, 4, 8)):
        w = s - i * 3
        x0 = cx - w / 2
        d.rounded_rectangle((x0, cy - s / 2 + dy, x0 + w, cy - s / 2 + dy + 6),
                            radius=2, fill=color)


def icon_target(d, cx, cy, s, color):
    d.ellipse((cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2), outline=color, width=3)
    d.ellipse((cx - s / 5, cy - s / 5, cx + s / 5, cy + s / 5), fill=color)


def icon_x(d, cx, cy, s, color):
    d.line((cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2), fill=color, width=4)
    d.line((cx - s / 2, cy + s / 2, cx + s / 2, cy - s / 2), fill=color, width=4)


def icon_num(d, cx, cy, s, color, n):
    d.ellipse((cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2), fill=color)
    ctext(d, cx, cy + 1, n, FB(int(s * 0.72)), WHITE)


def load_titles():
    try:
        data = json.loads(
            (Path(r"E:\myHermesWorkSpace\wechat-channels-downloader\sphdl\records.json"))
            .read_text(encoding="utf-8"))
        return [r.get("title", "") for r in data if r.get("title")][:10]
    except Exception:
        return ["潮汕英歌舞精彩瞬间，万人围观现场直击！",
                "非遗传承人带你走进潮汕文化的一天",
                "老城烟火气：凌晨四点的早市记录",
                "手工潮绣全过程，一针一线皆是功夫",
                "潮汕美食图鉴：从清晨到深夜",
                "乡村慢直播：田野上的四季变化",
                "传统木雕工艺：刻刀下的百年传承",
                "她力量：致敬每一位平凡而伟大的女性",
                "中秋习俗知多少？拜月娘的由来",
                "龙舟竞渡现场：鼓声里的热血青春"]


def render_ui(scale=1.0, mock=None):
    """渲染主界面效果图，返回 (img, base_w)。"""
    W, H = 1360, 860
    img = Image.new("RGB", (W, H), (248, 250, 252))
    d = ImageDraw.Draw(img)
    f18, f16, f15, f13 = FB(18), F(16), F(15), F(13)

    # 窗口
    d.rounded_rectangle((24, 24, W - 24, H - 24), radius=14, fill=WHITE,
                        outline=BORDER, width=2)
    d.rounded_rectangle((24, 24, W - 24, 70), radius=14, fill=(250, 250, 252))
    d.rectangle((24, 52, W - 24, 70), fill=(250, 250, 252))
    for i, c in enumerate(((237, 106, 94), (245, 191, 79), (98, 196, 84))):
        d.ellipse((46 + i * 22, 42, 60 + i * 22, 56), fill=c)
    ltext(d, 122, 47, "微信视频号下载器  sphdl v2.0.15", f16, INK)
    rtext(d, W - 50, 47, "—  □  ×", f16, GRAY)

    # ① 嗅探引擎
    y = 84
    btn(d, 44, y, 150, 38, "启动嗅探", f16, BLUE, WHITE)
    ltext(d, 220, y + 19, "代理端口", f15, INK)
    entry(d, 300, y + 2, 66, 34, "8899", f15)
    check_box(d, 392, y + 19)
    ltext(d, 414, y + 19, "自动设置系统代理", f15, INK)
    btn(d, 588, y, 128, 38, "恢复网络代理", f15, WHITE, INK)
    ltext(d, 748, y + 19, "CA证书:", f15, INK)
    ltext(d, 812, y + 19, "✓ 已安装", f15, GREEN)
    btn(d, 900, y, 88, 38, "安装/检测", f15, WHITE, INK)
    ltext(d, 1016, y + 19, "状态: 运行中", f15, GREEN)
    btn(d, 1120, y, 100, 38, "使用说明", f15, RED, WHITE)

    # ② 捕获设置
    y = 136
    ltext(d, 44, y + 19, "捕获设置", FB(15), INK)
    check_box(d, 130, y + 19)
    ltext(d, 152, y + 19, "自动下载（捕获即入队）", f15, INK)
    check_box(d, 384, y + 19)
    ltext(d, 406, y + 19, "下载图片", f15, INK)
    check_box(d, 520, y + 19)
    ltext(d, 542, y + 19, "自然捕获", f15, INK)
    btn(d, 660, y, 108, 38, "批量拉取", f15, RED, WHITE)
    rtext(d, W - 44, y + 19, "自动下载: 开 | 图片: 关 | 自然捕获", f13, GRAY)

    # ③ 下载目录
    y = 188
    ltext(d, 44, y + 17, "下载目录:", f15, INK)
    entry(d, 120, y + 1, 620, 34, r"E:\视频号下载", f15)
    btn(d, 756, y, 78, 34, "更改…", f15, WHITE, INK)
    btn(d, 848, y, 110, 34, "打开文件夹", f15, WHITE, INK)

    # ④ 视频列表
    ly0, ly1 = 236, 640
    d.rounded_rectangle((36, ly0, W - 36, ly1), radius=8, fill=WHITE,
                        outline=BORDER, width=2)
    cols = [(56, 150, "状态"), (206, 360, "标题"), (566, 90, "大小"),
            (656, 100, "时间"), (756, 150, "来源"), (906, 400, "文件")]
    for x, w, name in cols:
        ltext(d, x, ly0 + 17, name, FB(14), GRAY)
        d.line((x, ly0 + 30, x, ly0 + 30), fill=BORDER)
    d.line((36, ly0 + 34, W - 36, ly0 + 34), fill=BORDER, width=2)

    titles = load_titles()
    rows = []
    for i in range(9):
        st = ["已完成 ✓", "下载中 62%", "待下载", "排队中", "已完成 ✓",
              "失败 ✗", "待下载", "已完成 ✓", "待下载"][i]
        size = [84.6, 12.3, 96.8, 45.2, 15.0, 8.8, 120.4, 33.5, 22.1][i]
        src = "bridge钩子" if i % 2 == 0 else "API直嗅"
        rows.append((st, titles[i % len(titles)], size, src))
    for i, (st, title, size, src) in enumerate(rows):
        y0 = ly0 + 34 + i * 44
        if i % 2 == 1:
            d.rectangle((38, y0, W - 38, y0 + 44), fill=ROW_ALT)
        col = {"已完成 ✓": GREEN, "下载中 62%": BLUE, "待下载": GRAY,
               "排队中": (15, 118, 110), "失败 ✗": (220, 38, 38)}[st]
        ltext(d, 56, y0 + 22, st, f13, col)
        ltext(d, 206, y0 + 22, title[:17], f15, INK)
        ltext(d, 566, y0 + 22, f"{size:.1f} MB", f15, INK)
        ltext(d, 656, y0 + 22, "08-27 09:23", f13, GRAY)
        ltext(d, 756, y0 + 22, src, f13, GRAY)
        ltext(d, 906, y0 + 22, "—", f13, GRAY)
        d.line((36, y0 + 44, W - 36, y0 + 44), fill=BORDER, width=1)

    # 工具栏
    ty = 654
    tb = [("下载选中", BLUE, WHITE), ("停止下载", WHITE, INK),
          ("停止所有下载", WHITE, INK), ("下载全部待下载", WHITE, INK),
          ("取消所有排队", WHITE, INK), ("重试失败", WHITE, INK),
          ("重新下载", WHITE, INK)]
    x = 44
    for label, bg, fg in tb:
        w = max(96, 16 * len(label) + 24)
        btn(d, x, ty, w, 36, label, f13, bg, fg)
        x += w + 12
    ty2 = 700
    tb2 = [("打开文件", WHITE), ("打开文件夹", WHITE), ("删除记录", WHITE),
           ("删除文件+记录", WHITE), ("清空已完成", WHITE), ("清空失败", WHITE),
           ("删除全部记录", WHITE)]
    x = 44
    for label, bg in tb2:
        w = max(88, 15 * len(label) + 20)
        btn(d, x, ty2, w, 34, label, f13, bg, INK)
        x += w + 10

    # ⑤ 日志
    d.rounded_rectangle((36, 748, W - 36, 804), radius=8, fill=DARK)
    ltext(d, 52, 762, "日志", FB(13), (209, 213, 219))
    ltext(d, 52, 782, "[17:03:47] 心跳 嗅探运行中 | 钩子数据 2s前 | 捕获 32 个",
          f13, (125, 211, 252))
    ltext(d, 560, 782, "[17:03:50] 完成 下载并解密 … (15.0 MB)",
          f13, (134, 239, 172))
    ltext(d, 1000, 782, "[17:03:52] 警告 大小仅为元数据的 17%",
          f13, (253, 224, 71))

    # ⑥ 状态栏
    d.rounded_rectangle((36, 816, W - 36, 842), radius=6, fill=(243, 244, 246))
    ltext(d, 52, 829,
          "嗅探: 运行中 127.0.0.1:8899 | 系统代理: 已接管 | CA: ✓ | "
          "钩子: 2s前 | 列表: 共 32 条 · 待下载 8 · 完成 20 · 失败 4",
          f13, GRAY)

    if mock:
        return img
    if scale != 1.0:
        img = img.resize((int(W * scale), int(H * scale)), Image.LANCZOS)
    return img


def cover_base(w, h):
    img = vgrad(w, h, PINK_BG, PINK2)
    d = ImageDraw.Draw(img)
    d.ellipse((-120, -140, 320, 300), fill=(255, 224, 233))
    d.ellipse((w - 260, h - 260, w + 120, h + 120), fill=(255, 224, 233))
    d.ellipse((w - 420, -160, w - 60, 200), fill=(255, 234, 240))
    return img, d


def make_xhs_cover(ui_img):
    W, H = 1080, 1440
    img, d = cover_base(W, H)
    chip(d, W / 2 - 190, 86, 380, 58, "微信视频号 视频/图片保存工具",
         RED, WHITE, FB(24))
    ctext(d, W / 2, 200, "视频号视频", FB(66), INK)
    ctext(d, W / 2, 292, "一键批量保存", FB(66), RED)
    ctext(d, W / 2, 356, "批量抓取 · 自动连续下载 · 按需单个下载", F(28), GRAY)

    mw = 900
    mh = int(ui_img.height * mw / ui_img.width)
    img.paste(ui_img.resize((mw, mh), Image.LANCZOS), (int((W - mw) / 2), 400))

    y = 400 + mh + 48
    cards = [
        ("批量抓取", "整个视频号列表\n一键全部拉取", icon_stack),
        ("自动连续下载", "捕获即入队\n挂机一次下完", icon_down),
        ("按需单个下载", "列表按需勾选\n只下你点的那条", icon_target),
    ]
    cw, gap = 280, 24
    x0 = int((W - cw * 3 - gap * 2) / 2)
    for i, (t, sub, ic) in enumerate(cards):
        x = x0 + i * (cw + gap)
        d.rounded_rectangle((x, y, x + cw, y + 190), radius=18, fill=WHITE,
                            outline=(255, 214, 224), width=3)
        ic(d, x + cw / 2, y + 46, 38, RED)
        ctext(d, x + cw / 2, y + 96, t, FB(26), INK)
        for j, line in enumerate(sub.split("\n")):
            ctext(d, x + cw / 2, y + 136 + j * 24, line, F(19), GRAY)
    ctext(d, W / 2, H - 58,
          "Windows 免安装 · 本地 MP4 · 图片可按需收录", F(24), GRAY)
    return img


def make_idlefish_main(ui_img):
    W, H = 1080, 1080
    img, d = cover_base(W, H)
    chip(d, W / 2 - 130, 64, 260, 50, "微信视频号下载器", RED, WHITE, FB(24))
    ctext(d, W / 2, 166, "视频号视频 · 批量下载神器", FB(50), INK)
    ctext(d, W / 2, 230, "批量抓取 | 自动连续下载 | 按需单个下载", F(26), GRAY)

    mw = 820
    mh = int(ui_img.height * mw / ui_img.width)
    img.paste(ui_img.resize((mw, mh), Image.LANCZOS), (int((W - mw) / 2), 272))

    y = 272 + mh + 34
    cw, gap = 300, 20
    x0 = int((W - cw * 3 - gap * 2) / 2)
    items = [("批量抓取", icon_stack), ("自动连续下载", icon_down),
             ("按需单个下载", icon_target)]
    for i, (t, ic) in enumerate(items):
        x = x0 + i * (cw + gap)
        d.rounded_rectangle((x, y, x + cw, y + 78), radius=39, fill=WHITE,
                            outline=(255, 214, 224), width=3)
        ic(d, x + 52, y + 39, 30, RED)
        ctext(d, x + cw / 2 + 14, y + 39, t, FB(24), INK)
    ctext(d, W / 2, H - 52,
          "解压即用 · Windows 10/11 · 永久更新 · 绿色便携版", F(24), GRAY)
    return img


def make_detail_long():
    W, H = 1080, 2040
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 300), fill=RED)
    d.ellipse((-140, -160, 300, 320), fill=(255, 96, 126))
    d.ellipse((W - 320, -180, W + 120, 260), fill=(255, 96, 126))
    ctext(d, W / 2, 110, "微信视频号下载器", FB(54), WHITE)
    ctext(d, W / 2, 186, "批量抓取 · 自动连续下载 · 按需单个下载", F(28), (255, 236, 240))
    ctext(d, W / 2, 248, "v2.0.15 · Windows 10/11 · 绿色免安装", F(22), (255, 220, 227))

    y = 360
    ltext(d, 70, y + 16, "这些场景，你遇到过吗？", FB(32), INK)
    y += 52
    pains = ["喜欢的博主视频太多，一条条手动保存到手酸",
             "视频链接会过期，想存的时候已经失效",
             "合集想全下，却要守在电脑前一整天"]
    for p in pains:
        icon_x(d, 90, y + 16, 22, RED)
        ltext(d, 128, y + 16, p, F(27), INK)
        y += 54

    y += 28
    ltext(d, 70, y + 16, "核心功能", FB(32), INK)
    y += 56
    feats = [
        ("批量抓取", "打开任意视频号主页，一键拉取该号全部视频清单；",
         "也可批量滚动拉到底，追更博主、备份合集效率翻倍。", icon_stack),
        ("自动连续下载", "开启后新捕获的视频自动进入队列、依次下载；",
         "挂机托管，下班回来整个清单已经下完。", icon_down),
        ("按需单个下载", "不是什么都想要？列表按需勾选，只下你点的那条；",
         "不占磁盘、不浪费时间，灵活可控。", icon_target),
        ("自然捕获 & 图片开关", "默认只跟随当前播放的视频+随后几条缓存；",
         "图片帖可按需收录或隐藏，列表干净可控。", icon_target),
    ]
    for t, l1, l2, ic in feats:
        d.rounded_rectangle((60, y, W - 60, y + 158), radius=18, fill=(255, 250, 251),
                            outline=(255, 214, 224), width=3)
        ic(d, 120, y + 48, 38, RED)
        ltext(d, 168, y + 34, t, FB(29), INK)
        ltext(d, 168, y + 76, l1, F(23), INK)
        ltext(d, 168, y + 112, l2, F(23), GRAY)
        y += 178

    y += 10
    ltext(d, 70, y + 16, "三步上手", FB(32), INK)
    y += 58
    steps = ["① 点「安装/检测」装好 CA 证书，再点「启动嗅探」",
             "② 打开微信视频号浏览视频，列表自动捕获（页面请重进一次）",
             "③ 点「下载选中」或开启自动下载；想全下点「批量拉取」"]
    for s in steps:
        d.rounded_rectangle((70, y, W - 70, y + 54), radius=12, fill=(247, 249, 252))
        ltext(d, 100, y + 27, s, F(24), INK)
        y += 66

    y += 6
    ltext(d, 70, y + 16, "更多亮点", FB(28), INK)
    y += 50
    extra = ["· 列表表头排序 · Ctrl/Shift 多选批量管理 · 按钮悬停 2 秒有说明",
             "· 一键停止所有下载 / 取消所有排队 / 复制日志排查问题"]
    for s in extra:
        ltext(d, 100, y + 16, s, F(23), GRAY)
        y += 44

    y += 16
    d.rounded_rectangle((70, y, W - 70, y + 130), radius=14, fill=(255, 245, 247))
    ltext(d, 100, y + 26, "温馨提示", FB(26), RED)
    ltext(d, 100, y + 66, "· 页面请在启动嗅探后重新进入，确保捕获钩子生效",
          F(22), INK)
    ltext(d, 100, y + 98, "· 仅供个人学习与备份自己有权访问的内容",
          F(22), GRAY)
    ctext(d, W / 2, H - 44, "本工具为绿色便携版，解压即用，永久更新",
          F(22), GRAY)
    return img


def main():
    ui = render_ui(scale=1.0)
    ui.save(OUT / "ui_effect.png")
    make_xhs_cover(ui).save(OUT / "xhs_cover.png")
    make_idlefish_main(ui).save(OUT / "idlefish_main.png")
    make_detail_long().save(OUT / "detail_long.png")
    print("saved to", OUT)


if __name__ == "__main__":
    main()
