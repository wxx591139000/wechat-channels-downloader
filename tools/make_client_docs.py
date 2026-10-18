#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成三份客户自助排查 Word 文档。

设计原则（给非技术用户）：
· 每一步都有明确的「怎么做」和「应该看到什么」
· 不用术语，或术语后面立刻用大白话解释
· 每个问题分「先试这个」（80% 情况）和「还不行再试」
· 结尾统一给「怎么找我们」和「发什么给我们」
· 不用 markdown 语法，用 Word 的标题/编号/表格
"""
import sys
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor, Cm

OUT = Path(r"E:/myHermesWorkSpace/wechat-channels-downloader/docs/客户排查指南")
OUT.mkdir(parents=True, exist_ok=True)

# 中文字体设置
FONT_CN = "微软雅黑"


def setup(doc):
    """全局字体 + 页边距。"""
    st = doc.styles["Normal"]
    st.font.name = FONT_CN
    st.font.size = Pt(10.5)
    st._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_CN)
    for s in doc.sections:
        s.top_margin = Cm(2.0)
        s.bottom_margin = Cm(2.0)
        s.left_margin = Cm(2.2)
        s.right_margin = Cm(2.2)


def title(doc, text):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(text)
    r.font.size = Pt(20)
    r.font.bold = True
    r.font.name = FONT_CN
    r._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_CN)
    p.paragraph_format.space_after = Pt(4)


def subtitle(doc, text):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(text)
    r.font.size = Pt(10)
    r.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
    r.font.name = FONT_CN
    r._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_CN)
    p.paragraph_format.space_after = Pt(14)


def h1(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(16)
    p.paragraph_format.space_after = Pt(6)
    r = p.add_run(text)
    r.font.size = Pt(14)
    r.font.bold = True
    r.font.color.rgb = RGBColor(0x1E, 0x3A, 0x5F)
    r.font.name = FONT_CN
    r._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_CN)


def h2(doc, text, color=(0x8A, 0x4B, 0x00)):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(11)
    p.paragraph_format.space_after = Pt(3)
    r = p.add_run(text)
    r.font.size = Pt(11.5)
    r.font.bold = True
    r.font.color.rgb = RGBColor(*color)
    r.font.name = FONT_CN
    r._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_CN)


def body(doc, text, indent=0.0, bold=False, color=None, size=10.5):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(4)
    if indent:
        p.paragraph_format.left_indent = Cm(indent)
    r = p.add_run(text)
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.name = FONT_CN
    r._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_CN)
    if color:
        r.font.color.rgb = RGBColor(*color)
    return p


def steps(doc, items, start=1):
    """编号步骤（手写编号，避免 Word 自动编号在长文档里错乱）。"""
    for i, it in enumerate(items, start):
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Cm(0.8)
        p.paragraph_format.space_after = Pt(3)
        r1 = p.add_run(f"{i}. ")
        r1.font.bold = True
        r1.font.size = Pt(10.5)
        r1.font.name = FONT_CN
        r1._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_CN)
        # 支持 (主体文字, 补充说明) 两段
        if isinstance(it, tuple):
            r2 = p.add_run(it[0])
            r2.font.size = Pt(10.5)
            r2.font.name = FONT_CN
            r2._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_CN)
            note = it[1] if len(it) > 1 and it[1] else ""
            if not note:
                continue
            r3 = p.add_run("  " + note)
            r3.font.size = Pt(9.5)
            r3.font.color.rgb = RGBColor(0x77, 0x77, 0x77)
            r3.font.name = FONT_CN
            r3._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_CN)
        else:
            r2 = p.add_run(it)
            r2.font.size = Pt(10.5)
            r2.font.name = FONT_CN
            r2._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_CN)


def box(doc, text, kind="info"):
    """提示框（用单元格底色模拟）。"""
    colors = {
        "info": ((0xE8, 0xF0, 0xF8), (0x1E, 0x3A, 0x5F)),
        "warn": ((0xFF, 0xF3, 0xE0), (0x8A, 0x4B, 0x00)),
        "ok":   ((0xE8, 0xF5, 0xE9), (0x15, 0x80, 0x3D)),
    }
    bg, fg = colors.get(kind, colors["info"])
    t = doc.add_table(rows=1, cols=1)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    cell = t.cell(0, 0)
    cell.width = Cm(16.5)
    shd = cell._tc.get_or_add_tcPr().makeelement(
        qn("w:shd"), {qn("w:val"): "clear", qn("w:color"): "auto",
                      qn("w:fill"): "%02X%02X%02X" % bg})
    cell._tc.get_or_add_tcPr().append(shd)
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(4)
    r = p.add_run(text)
    r.font.size = Pt(10)
    r.font.color.rgb = RGBColor(*fg)
    r.font.name = FONT_CN
    r._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_CN)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def table(doc, headers, rows, widths=None):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = t.rows[0].cells
    for i, h in enumerate(headers):
        hdr[i].text = ""
        p = hdr[i].paragraphs[0]
        r = p.add_run(h)
        r.font.bold = True
        r.font.size = Pt(9.5)
        r.font.name = FONT_CN
        r._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_CN)
        shd = hdr[i]._tc.get_or_add_tcPr().makeelement(
            qn("w:shd"), {qn("w:val"): "clear", qn("w:color"): "auto",
                          qn("w:fill"): "1E3A5F"})
        hdr[i]._tc.get_or_add_tcPr().append(shd)
        r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = ""
            p = cells[i].paragraphs[0]
            r = p.add_run(str(v))
            r.font.size = Pt(9.5)
            r.font.name = FONT_CN
            r._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_CN)
    if widths:
        for row in t.rows:
            for i, w in enumerate(widths):
                row.cells[i].width = Cm(w)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def footer_contact(doc):
    h1(doc, "还是不行？请联系我们")
    body(doc, "如果按上面全部步骤做完仍然解决不了，请把下面这些信息发给我们，"
              "我们会帮你定位：")
    steps(doc, [
        ("「诊断-双击我.bat」窗口的完整截图",
         "（这个文件在解压后的文件夹里，双击就会自己检查）"),
        ("软件里「日志」区域最后 20 行的截图",
         "（在主窗口下半部分，可以鼠标选中复制）"),
        ("你的 Windows 版本", "（按 Win+R，输入 winver 回车，截图那个窗口）"),
        ("你电脑上有没有装别的代理/VPN 软件",
         "（比如 Clash、v2ray、各种加速器）"),
    ], start=1)
    box(doc, "小提示：发截图比打字描述有用得多。文字描述容易出现理解偏差，"
             "截图能让我们一眼看到问题在哪。", "info")


# ============================================================================
# 文档一：装不上 CA 证书
# ============================================================================
def doc_ca():
    doc = Document()
    setup(doc)
    title(doc, "问题一：装不上 CA 证书")
    subtitle(doc, "微信视频号下载器 · 客户自助排查指南")

    box(doc, "先搞清楚：CA 证书是什么？\n"
             "它就是一个「通行证」，让本工具能读取你电脑上的视频号流量。"
             "只影响本工具，不会影响你访问其它网站，也不会泄露你的密码。",
        "info")

    h1(doc, "第一步：确认你点按钮的顺序对不对（最常见原因）")

    body(doc, "90% 的「装不上」都是因为顺序反了。证书要等软件启动嗅探之后才会"
              "自动生成，先点「安装」是找不到证书的。", bold=True)

    steps(doc, [
        ("先点【▶ 启动嗅探】", "不要先点安装证书"),
        ("等 3~5 秒", "这期间软件会自动生成证书文件，屏幕上没有变化是正常的"),
        ("再点【安装/检测】", "这次才会弹出安装向导"),
        ("在弹出的向导里按提示操作", "遇到 Windows 的蓝色安全警告，要点【是】"),
        ("回到软件，证书状态应显示「已安装 ✓」", "绿色字"),
    ])

    box(doc, "判断有没有走对：如果你点【安装/检测】时弹出一个黑色命令行窗口，"
             "里面写着「找不到 CA 证书文件」，说明你跳过了第 1 步 —— "
             "回头先点【▶ 启动嗅探】，等 5 秒再试。", "warn")

    h1(doc, "第二步：如果顺序对了还是装不上")

    h2(doc, "情况 A：向导里提示失败，或点了【是】之后没反应")
    body(doc, "多半是被安全软件拦了。按顺序试：")
    steps(doc, [
        ("临时关闭杀毒软件", "360、火绒、腾讯电脑管家、Windows Defender 都要关"),
        ("重新运行安装向导", "软件里再点一次【安装/检测】"),
        ("装成功后把杀毒软件打开", "如果这时候软件又不能用了，把本工具加入白名单"),
    ])

    h2(doc, "情况 B：提示「需要管理员权限」")
    body(doc, "右键点击【微信视频号下载器.exe】→ 选「以管理员身份运行」，"
              "然后重新操作一遍。")

    h2(doc, "情况 C：公司/单位的电脑")
    body(doc, "如果是公司电脑，可能被 IT 部门用组策略锁定了证书导入权限，"
              "这种情况你自己改不了，需要用个人电脑，或者找 IT 申请。"
              "判断方法：别的软件装证书也失败 = 大概率是组策略。")

    h1(doc, "第三步：怎么自己确认证书到底装上没有")

    body(doc, "不用信软件显示，直接去 Windows 里查：")
    steps(doc, [
        ("按键盘 Win + R", "同时按住 Windows 键和字母 R"),
        ("输入 certmgr.msc 然后回车", "会打开「证书」管理窗口"),
        ("左边点开「受信任的根证书颁发机构」→ 再点「证书」",
         None),
        ("在右侧列表里找名字带 mitmproxy 的那一条", None),
    ])

    table(doc,
          ["你看到的情况", "说明什么", "怎么办"],
          [["找到了 mitmproxy 开头的证书", "证书已装好", "问题不在这里，去看问题二"],
           ["完全找不到", "证书确实没装上", "回到第一步，严格按顺序再来一遍"],
           ["软件里显示「已安装」但这里找不到",
            "可能装到了别的用户账户下",
            "确认你当前登录的是同一个 Windows 账户"]],
          widths=[6.0, 5.0, 5.5])

    h1(doc, "常见疑问")

    h2(doc, "装这个证书安全吗？")
    body(doc, "安全。它是一个「本地证书」，只在你自己的电脑上有效，"
              "作用范围仅限本工具读取流量。它不会让你的电脑更容易被攻击，"
              "也不会把数据传到别的地方。")

    h2(doc, "以后卸载软件，证书要删吗？")
    body(doc, "建议删掉，养成好习惯。方法：按 Win+R 输入 certmgr.msc → "
              "受信任的根证书颁发机构 → 证书 → 找到 mitmproxy 那条，右键删除。"
              "不删也不影响使用，只是会一直留着。")

    h2(doc, "为什么每次重装系统都要重新装证书？")
    body(doc, "因为证书是装在 Windows 系统里的，重装系统等于清空了，需要重新装一次。"
              "装之前记得先点【▶ 启动嗅探】让软件重新生成证书。")

    footer_contact(doc)

    p = OUT / "问题一-装不上CA证书.docx"
    doc.save(p)
    return p


# ============================================================================
# 文档二：打开嗅探后打不开视频号
# ============================================================================
def doc_open_fail():
    doc = Document()
    setup(doc)
    title(doc, "问题二：打开嗅探后，打不开视频号")
    subtitle(doc, "微信视频号下载器 · 客户自助排查指南")

    box(doc, "这个问题的本质：软件为了让视频走它那里，会临时改你电脑的"
             "「网络代理」设置。如果改错了、或者你电脑上还有别的代理软件在抢，"
             "微信就连不上网了。\n\n"
             "下面按「最可能」到「较少见」排好了，从第一步开始试。", "warn")

    h1(doc, "第一步：完全退出微信，再重新打开（最常见原因，先试这个）")

    body(doc, "微信只在启动的那一刻读一次网络设置。如果你的微信在开嗅探之前"
              "就已经打开了，它还在用旧的设置，自然连不上。", bold=True)

    body(doc, "注意：点 × 只是把窗口最小化，微信还在后台跑。必须真正退出。")

    steps(doc, [
        ("在屏幕右下角找到微信图标", "就是那个绿色小方块，可能在向上的箭头里"),
        ("在图标上点右键 → 选「退出」", "不是点 ×，也不是「隐藏」"),
        ("确认真的退出了", "按 Ctrl+Shift+Esc 打开任务管理器，看有没有 WeChat.exe"),
        ("点软件的【▶ 启动嗅探】", "等 3~5 秒"),
        ("重新打开微信", None),
        ("进「发现 → 视频号」试试", None),
    ])

    h1(doc, "第二步：关掉你电脑上其它的代理软件")

    body(doc, "如果你装了 Clash、v2ray、SSR、各类网游加速器、VPN，"
              "它们会和本工具抢同一个设置，结果谁都连不上。")

    steps(doc, [
        ("找到那个软件的图标", "通常在屏幕右下角的托盘里"),
        ("右键 → 退出 / 完全退出", "关窗口往往不够，要真正退出进程"),
        ("确认它不在任务管理器里了", "Ctrl+Shift+Esc 看进程列表"),
        ("再试一次微信视频号", None),
    ])

    box(doc, "用完之后要恢复上网：把这些代理软件重新打开就行，"
             "本工具不会永久改你的网络设置。", "info")

    h1(doc, "第三步：检查系统代理是不是被改坏了")

    body(doc, "如果你之前强制关过本软件（比如任务管理器里杀进程、或者电脑突然断电），"
              "系统代理可能还指着一个已经关掉的端口，导致所有程序都上不了网。")

    body(doc, "怎么检查：")
    steps(doc, [
        ("按 Win + R，输入 inetcpl.cpl，回车", "会打开「Internet 属性」窗口"),
        ("点顶部的「连接」标签页", None),
        ("点最下面的「局域网设置」按钮", None),
        ("看「为 LAN 使用代理服务器」这一项", None),
    ])

    table(doc,
          ["你看到的情况", "说明", "怎么办"],
          [["这一项没打勾", "系统代理是干净的", "问题不在这，继续看下面"],
           ["打勾了，地址是 127.0.0.1，端口 8899",
            "是本工具设的，但软件可能已经关了",
            "把勾去掉 → 点确定，然后重新开嗅探"],
           ["打勾了，地址是别的（比如 7890）",
            "是你其它代理软件设的",
            "把勾去掉，或退出那个代理软件"]],
          widths=[6.0, 5.0, 5.5])

    h1(doc, "第四步：换个端口")

    body(doc, "有时候 8899 这个端口被别的程序占用了。换一个试试：")
    steps(doc, [
        ("在软件里找到「端口」输入框", "就在启动嗅探按钮旁边"),
        ("把 8899 改成 8898 或 9999", None),
        ("点【▶ 启动嗅探】", None),
        ("重新打开微信试试", None),
    ])

    h1(doc, "第五步：还是不行？用系统自带的诊所工具")

    body(doc, "Windows 有个自带的网络诊断：")
    steps(doc, [
        ("按 Win + I 打开设置", None),
        ("网络和 Internet → 网络疑难解答", None),
        ("让它自动检测并修复", None),
    ])

    h1(doc, "快速判断：到底是哪一环出了问题")

    body(doc, "打开软件，看下半部分的「日志」区域，找这一行：")

    table(doc,
          ["日志里显示", "含义", "解法"],
          [["流活动 无", "流量根本没走代理", "按上面第一到第三步排查"],
           ["流活动 3秒前 / 15秒前", "流量通了，问题在别处",
            "去看问题三（抓不到视频）"],
           ["页面注入 0 次", "微信页面没被接管",
            "完全关闭视频号页面后重新进一次"]],
          widths=[5.0, 5.0, 6.5])

    footer_contact(doc)

    p = OUT / "问题二-打开嗅探后打不开视频号.docx"
    doc.save(p)
    return p


# ============================================================================
# 文档三：抓不到视频
# ============================================================================
def doc_no_capture():
    doc = Document()
    setup(doc)
    title(doc, "问题三：打开嗅探后，抓不到视频")
    subtitle(doc, "微信视频号下载器 · 客户自助排查指南")

    box(doc, "注意区分两种情况：\n"
             "· 视频号能正常看，只是工具列表里没出现视频 → 看这份文档\n"
             "· 视频号本身打不开 / 加载不出来 → 那是问题二，看那份文档",
        "info")

    h1(doc, "第一步：确认操作的先后顺序（90% 是这个原因）")

    body(doc, "这个工具的抓取原理是「监听微信的网络请求」。微信只在启动时读一次"
              "网络设置，所以顺序必须是：", bold=True)

    box(doc, "正确顺序：\n"
             "① 先打开工具 → 点【▶ 启动嗅探】\n"
             "② 等 3~5 秒（让代理起来）\n"
             "③ 完全退出微信（托盘右键 → 退出）\n"
             "④ 重新打开微信\n"
             "⑤ 进「发现 → 视频号」→ 播放视频", "warn")

    body(doc, "很多人是「先开微信 → 再开嗅探」，这样微信还在用旧的网络设置，"
              "工具的代码根本没机会介入，所以一个都抓不到。")

    h1(doc, "第二步：确认你真的在播视频")

    body(doc, "工具只在视频**真正开始播放**时才能拿到数据。"
              "以下情况抓不到是正常的：")
    steps(doc, [
        "只在视频号列表里滑动 —— 没有播放，抓不到",
        "封面页停留 —— 视频还没开始加载，抓不到",
        "视频播放了但立刻划走 —— 可能来不及，建议完整播 3~5 秒",
        "看的是图片/图文帖 —— 那是图片不是视频",
    ])

    h1(doc, "第三步：看日志里的「流活动」，判断卡在哪")

    body(doc, "打开软件下半部分的「日志」区域，找类似这样的行：")
    body(doc, "流活动 3秒前  |  页面注入 5 次  |  捕获 0 个", indent=0.8,
         color=(0x66, 0x66, 0x66), size=9.5)

    table(doc,
          ["日志内容", "含义", "怎么做"],
          [["流活动 无\n捕获 0 个", "流量没走代理", "按问题二排查（顺序/代理冲突）"],
           ["流活动 3秒前\n页面注入 0 次", "流量通了，但工具没接管页面",
            "完全关闭视频号页面 → 重新进一次"],
           ["流活动 3秒前\n页面注入 5 次\n捕获 0 个",
            "工具已接管，但没识别到视频",
            "可能播的是图片帖；换一个视频号试试"],
           ["捕获 N 个（N>0）", "已经在抓了", "去列表看看，可能只是没注意"]],
          widths=[5.0, 5.0, 6.5])

    h1(doc, "第四步：检查列表是不是满了")

    body(doc, "如果你没授权（或用的是低档位授权码），列表有数量上限。"
              "满了之后新的视频不会再进来。")

    body(doc, "怎么判断：看主界面「列表上限」那一行：")
    table(doc,
          ["显示", "含义", "怎么做"],
          [["未授权（可捕获，不能下载）",  "试用状态，最多 500 条",
            "需要下载就点「🔑 授权管理」激活"],
           ["轻量（500 条）", "已授权 500 条上限", "满了就升级档位或删掉部分记录"],
           ["至尊（2 万条）", "已授权 2 万条上限", "基本不会满"]],
          widths=[5.5, 5.0, 6.0])

    body(doc, "列表满了的话，日志里会出现：「列表已满，后续捕获的视频将不再加载」。")

    h1(doc, "第五步：确认抓的是「视频号」不是「公众号文章」")

    body(doc, "这是两套完全不同的东西：")

    table(doc,
          ["你在看什么", "能不能抓", "怎么抓"],
          [["微信 → 发现 → 视频号", "能（需要嗅探）",
            "本文档的方法"],
           ["公众号文章里的视频", "能（需要单独授权）",
            "点软件里的「📄 公众号视频」按钮，粘贴文章链接"],
           ["聊天窗口里别人发的视频", "不能", "不在支持范围"],
           ["朋友圈的视频", "不能", "不在支持范围"]],
          widths=[6.0, 4.5, 6.0])

    h1(doc, "第六步：杀毒软件干扰")

    body(doc, "有些杀软会把「监听网络」的行为当可疑操作拦掉，表现就是"
              "代理起来了但抓不到东西。")

    steps(doc, [
        "临时关闭杀毒软件（360 / 火绒 / 电脑管家 / Defender）",
        "重新启动嗅探 + 重开微信视频号",
        "如果能抓了 → 把本工具加入杀软的信任列表/白名单",
    ])

    h1(doc, "第七步：重新来一遍的标准流程")

    body(doc, "如果上面都试过还不行，完全按这个流程重启一次：")

    steps(doc, [
        ("关掉本工具", "右键托盘图标退出"),
        ("关掉微信", "托盘右键 → 退出，确认任务管理器里没有 WeChat.exe"),
        ("检查系统代理有没有残留", "Win+R → inetcpl.cpl → 连接 → 局域网设置"),
        ("重新打开本工具", None),
        ("点【▶ 启动嗅探】，等 5 秒", None),
        ("打开微信 → 视频号 → 完整播放一个视频 5 秒以上", None),
        ("看软件的日志和列表", None),
    ])

    box(doc, "如果日志里「捕获」的数字在涨，但列表里看不到，"
             "说明抓到了但被过滤了（可能是图片帖，或者列表满了）。"
             "把日志截图发我们，能立刻定位。", "info")

    footer_contact(doc)

    p = OUT / "问题三-打开嗅探后抓不到视频.docx"
    doc.save(p)
    return p


if __name__ == "__main__":
    print("生成三份客户排查文档...\n")
    for fn, name in ((doc_ca, "问题一-装不上CA证书"),
                     (doc_open_fail, "问题二-打开嗅探后打不开视频号"),
                     (doc_no_capture, "问题三-打开嗅探后抓不到视频")):
        path = fn()
        size = path.stat().st_size
        print(f"  ✅ {name}.docx  ({size/1024:.1f} KB)")
    print(f"\n输出目录：{OUT}")
