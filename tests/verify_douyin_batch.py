"""抖音批量下载（方案 A）验证（v2.4.0）。

═══ 背景 ═══
客户要「像视频号一样的抖音批量下载工具」。先做 A（粘贴链接），
跑通后做 B（输入主页批量抓）。

═══ 已实测确认的技术事实（见 sphdl/douyin.py 顶部注释）═══
1. yt-dlp 走不通（403 + 系统 cookies 读不出）
2. Playwright 开页面读它自己的 aweme/detail 响应 ✅ 可行
3. play_addr 是**音视频合一 MP4**（1080x1920、30.54MB、h264+aac）
   → **不需要 ffmpeg**，包体不涨
4. 下载只需 Referer，**不需要 cookies**
5. 用 `channel="msedge"` 驱动系统 Edge → **不需要内置 Chromium**
6. 风控是**间歇性**限流 → 用重试 + 随机间隔解决

═══ 覆盖 ═══
  D1  链接解析：标准链接 / 短链 / 分享文案 / 多链接 / 去重 / 中文标点
  D2  字段解析：从 detail JSON 提取标题/作者/时长/大小/1080p 地址
  D3  降级：play_addr 缺失时从 bit_rate 取最高档
  D4  错误处理：图文/直播/无数据 → 明确 error 而不是崩
  D5  文件名安全（Windows 非法字符、超长）
  D6  下载跳过：文件已存在且大小一致 → 不重复下
  D7  下载失败：地址失效（403）→ 返回错误而不是留半截文件
  D8  UI：窗口能建、按钮齐全、勾选切换、全选/反选
  D9  UI：解析结果回填（成功/失败两种样式）
  D10 UI：下载进度 / 完成 / 全部结束的状态流转
  D11 主窗口：按钮存在、点开能建窗口、重复点不重复建
  D12 目录：下载到 <下载目录>/抖音/
"""
import json
import os
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


print("=" * 74)
print("D1 链接解析")
print("=" * 74)
from sphdl.douyin import extract_urls

cases = [
    ("https://www.douyin.com/video/7483474074954386740",
     ["https://www.douyin.com/video/7483474074954386740"]),
    ("7.68 复制打开抖音，看看【某某】的作品 https://v.douyin.com/iRNBho6u/ 很不错",
     ["https://v.douyin.com/iRNBho6u/"]),
    ("a https://www.douyin.com/video/111 b https://v.douyin.com/abc/",
     ["https://www.douyin.com/video/111", "https://v.douyin.com/abc/"]),
    ("https://www.douyin.com/video/999。后面有中文",
     ["https://www.douyin.com/video/999"]),
    ("重复 https://v.douyin.com/xx/ 和 https://v.douyin.com/xx/",
     ["https://v.douyin.com/xx/"]),
    ("https://www.iesdouyin.com/share/video/123456",
     ["https://www.iesdouyin.com/share/video/123456"]),
    ("没有链接", []),
    ("", []),
]
for txt, want in cases:
    got = extract_urls(txt)
    ok = got == want
    print(f"  {'✅' if ok else '❌'} {txt[:44]!r}")
    if not ok:
        print(f"      期望 {want}")
        print(f"      实际 {got}")
    check(f"D1 {txt[:26] or '(空)'}", ok)

print()
print("=" * 74)
print("D2/D3/D4 detail JSON 解析")
print("=" * 74)
from sphdl.douyin import _parse_detail, DouyinVideo

# 完整数据（模拟真实结构，数值取自 2026-10-26 实测）
real = {"aweme_detail": {
    "aweme_id": "7483474074954386740",
    "desc": "因为好玩，他开发出收入千万的AI工具 #刘小排",
    "duration": 140200,
    "author": {"nickname": "刘小排"},
    "video": {
        "play_addr": {"url_list": ["https://v26-web.douyinvod.com/aaa"],
                      "data_size": 32025519, "width": 1080,
                      "height": 1920},
        "cover": {"url_list": ["https://cover/x.jpg"]},
        "bit_rate": [
            {"gear_name": "normal_1080_0", "bit_rate": 1827379,
             "play_addr": {"url_list": ["https://1080"], "data_size": 32025519,
                           "width": 1080, "height": 1920}},
        ],
    },
}}
v = _parse_detail(real)
print(f"  title={v.title[:30]!r} author={v.author!r}")
print(f"  {v.width}x{v.height} {v.duration_text} {v.size_text}")
check("D2a 标题", "刘小排" in v.title, v.title[:30])
check("D2b 作者", v.author == "刘小排", v.author)
check("D2c 时长 140 秒", v.duration_ms == 140200, str(v.duration_ms))
check("D2d 分辨率 1080x1920", (v.width, v.height) == (1080, 1920),
      f"{v.width}x{v.height}")
check("D2e 大小 30.5MB 量级", 29_000_000 < v.size < 33_000_000,
      str(v.size))
check("D2f 地址", v.play_url.startswith("https://"), v.play_url[:30])
check("D2g ok 为真", v.ok is True)

# play_addr 缺失 → 从 bit_rate 取
nopa = json.loads(json.dumps(real))
nopa["aweme_detail"]["video"]["play_addr"]["url_list"] = []
nopa["aweme_detail"]["video"]["bit_rate"] = [
    {"play_addr": {"url_list": ["https://low"], "data_size": 100}},
    {"play_addr": {"url_list": ["https://high"], "data_size": 9999}},
]
v2 = _parse_detail(nopa)
print(f"  D3 降级取 bit_rate 最高档: {v2.play_url}")
check("D3 play_addr 缺失时取最高码率", v2.play_url == "https://high",
      v2.play_url)

# 图文/直播/空
for label, data in (
        ("空响应", {"aweme_detail": {}}),
        ("无 detail 字段", {}),
        ("有 detail 但无 video", {"aweme_detail": {"desc": "x"}}),
):
    vv = _parse_detail(data)
    print(f"  D4 {label}: ok={vv.ok} err={vv.error[:30]!r}")
    check(f"D4 {label} 明确报错不崩", (not vv.ok) and bool(vv.error))

print()
print("=" * 74)
print("D5 文件名安全")
print("=" * 74)
from sphdl.douyin import safe_name

bad = 'a/b\\c:d*e?f"g<h>i|j\n\t标题'
sn = safe_name(bad)
print(f"  {bad!r}")
print(f"  → {sn!r}")
illegal = set('\\/:*?"<>|\r\n\t')
check("D5a 无 Windows 非法字符", not (illegal & set(sn)), sn)
check("D5b 非空", bool(sn.strip()))
long = "长" * 300
check("D5c 超长被截断", len(safe_name(long)) <= 80, str(len(safe_name(long))))
check("D5d 空输入有兜底", safe_name("") == "douyin_video", safe_name(""))

print()
print("=" * 74)
print("D6/D7 下载行为（用本地 HTTP 服务模拟）")
print("=" * 74)
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAYLOAD = b"X" * 400_000          # 400KB 假视频（>100KB 阈值）


class H(BaseHTTPRequestHandler):
    mode = "ok"

    def do_GET(self):
        if H.mode == "403":
            self.send_response(403)
            self.end_headers()
            return
        if H.mode == "tiny":
            body = b"x" * 1000
        else:
            body = PAYLOAD
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"

from sphdl.douyin import download

tdir = Path(tempfile.mkdtemp(prefix="dydl_"))

# D6 正常下载
H.mode = "ok"
vv = DouyinVideo(aweme_id="111", title="测试视频/有斜杠", play_url=f"{base}/v",
                 size=len(PAYLOAD))
ok, info = download(vv, tdir, timeout=30)
print(f"  正常下载: ok={ok}  {Path(info).name if ok else info}")
check("D6a 下载成功", ok, str(info)[:50])
check("D6b 文件名已脱敏", ok and "/" not in Path(info).name,
      Path(info).name if ok else "")

# 重复下载应跳过（不报错、不重复写）
if ok:
    p1 = Path(info)
    mtime1 = p1.stat().st_mtime
    time.sleep(1.1)
    ok2, info2 = download(vv, tdir, timeout=30)
    same = ok2 and info2 == info and p1.stat().st_mtime == mtime1
    print(f"  重复下载: ok={ok2} 跳过未重写={same}")
    check("D6c 已存在且大小一致 → 跳过", same)

# D6d 进度回调
progs = []
H.mode = "ok"
vv2 = DouyinVideo(aweme_id="222", title="进度测试", play_url=f"{base}/p",
                  size=len(PAYLOAD))
download(vv2, tdir, on_progress=lambda g, t, d: progs.append((g, t)),
         timeout=30)
print(f"  进度回调次数: {len(progs)}")
check("D6d 进度回调被调用", len(progs) >= 1, str(len(progs)))
check("D6e 进度总量正确", progs and progs[-1][0] == len(PAYLOAD),
      str(progs[-1] if progs else None))

# D7 403 → 失败且不留 .part
H.mode = "403"
vv3 = DouyinVideo(aweme_id="333", title="403测试", play_url=f"{base}/x",
                  size=len(PAYLOAD))
ok3, info3 = download(vv3, tdir, timeout=30)
print(f"  403 场景: ok={ok3}  {str(info3)[:60]}")
check("D7a 下载失败返回 False", ok3 is False)
check("D7b 不留 .part 残文件",
      not list(tdir.glob("*.part")), str(list(tdir.glob("*.part"))))

# D7c 文件太小 → 判失败
H.mode = "tiny"
vv4 = DouyinVideo(aweme_id="444", title="小文件", play_url=f"{base}/t", size=0)
ok4, info4 = download(vv4, tdir, timeout=30)
print(f"  小文件场景: ok={ok4}  {str(info4)[:50]}")
check("D7c 文件太小判失败", ok4 is False)
# D7d 没地址
vv5 = DouyinVideo(aweme_id="555", title="无地址", error="解析失败")
ok5, info5 = download(vv5, tdir, timeout=10)
check("D7d 无地址直接返回错误", ok5 is False and "解析失败" in info5, info5)
srv.shutdown()

print()
print("=" * 74)
print("D8-D12 GUI")
print("=" * 74)
import tkinter as tk
from tkinter import ttk
import sphdl.gui as G

cfgdir = Path(tempfile.mkdtemp(prefix="dygui_"))
G.CONFIG_FILE = cfgdir / "config.json"
G.RECORDS_FILE = cfgdir / "records.json"
G.CONFIG_FILE.write_text("{}", encoding="utf-8")
G.RECORDS_FILE.write_text("[]", encoding="utf-8")

app = G.App()
app.root.deiconify()
app.root.geometry("1100x700+40+40")
for _ in range(6):
    app.root.update()
    time.sleep(0.08)

# D11 主窗口按钮
has_btn = hasattr(app, "btn_dy")
print(f"  主窗口有「抖音批量下载」按钮: {has_btn}")
check("D11a 主窗口按钮存在", has_btn)

app._on_douyin()
for _ in range(10):
    app.root.update()
    time.sleep(0.08)

w = getattr(app, "_dy_win", None)
check("D11b 点开能创建窗口", w is not None)
if w:
    print(f"  窗口标题: {w.win.title()!r}")
    check("D11c 窗口标题正确", "抖音" in w.win.title(), w.win.title())

    # D8 控件齐全
    def walk(x, acc):
        for c in x.winfo_children():
            acc.append(c)
            walk(c, acc)
        return acc

    allc = walk(w.win, [])
    texts = []
    for c in allc:
        try:
            t = c.cget("text")
            if t:
                texts.append(str(t))
        except Exception:
            pass
    joined = " | ".join(texts)
    print(f"  控件总数: {len(allc)}")
    for need in ("② 解析链接", "③ 下载选中", "全选", "停止", "打开下载目录"):
        ok = need in joined
        print(f"    {'✅' if ok else '❌'} {need}")
        check(f"D8 有「{need}」", ok)
    check("D8b 有结果表格", any(isinstance(c, ttk.Treeview) for c in allc))
    check("D8c 有进度条", any(isinstance(c, ttk.Progressbar) for c in allc))

    # D12 目录
    d = w._dir()
    print(f"  下载目录: {d}")
    check("D12 下载到 <下载目录>/抖音/", d.name == "抖音", str(d))

    # D9 回填（成功 + 失败）
    w.videos = [("https://u1", None), ("https://u2", None)]
    for i, u in enumerate(("https://u1", "https://u2")):
        iid = w.tree.insert("", "end",
                            values=("☑", f"（待解析）{u}", "", "", "", "排队中"))
        w._rows[iid] = i
    good = DouyinVideo(aweme_id="1", title="成功视频", author="阿作者",
                       duration_ms=65000, play_url="https://p",
                       size=25_000_000, width=1080, height=1920)
    w.apply_item(0, good)
    w.apply_item(1, DouyinVideo(error="解析失败（风控）"))
    app.root.update()

    iids = w.tree.get_children()
    v0 = w.tree.item(iids[0], "values")
    v1 = w.tree.item(iids[1], "values")
    print(f"  成功行: {v0}")
    print(f"  失败行: {v1}")
    check("D9a 成功行显示标题", "成功视频" in str(v0), str(v0[1]))
    check("D9b 成功行显示时长", "1:05" in str(v0), str(v0[3]))
    check("D9c 成功行显示大小", "MB" in str(v0), str(v0[4]))
    check("D9d 失败行标失败并取消勾选",
          str(v1[0]) == "☐" and "失败" in str(v1[1]), str(v1[:2]))

    # D10 进度流转
    w.apply_dl_start(0, 1, 2)
    app.root.update()
    w.apply_dl_prog(0, 12_500_000, 25_000_000, 1, 2)
    app.root.update()
    mid = w.tree.item(iids[0], "values")[5]
    pb = float(w.pbar["value"])
    print(f"  下载中状态: {mid!r}  进度条 {pb:.0f}%")
    check("D10a 状态显示下载中", "下载中" in str(mid), str(mid))
    check("D10b 进度条 > 0", pb > 0, f"{pb:.0f}")

    fake_file = cfgdir / "抖音" / "结果.mp4"
    fake_file.parent.mkdir(parents=True, exist_ok=True)
    fake_file.write_bytes(b"x" * 1000)
    w.apply_dl_done(0, True, str(fake_file), 1, 2)
    app.root.update()
    done = w.tree.item(iids[0], "values")[5]
    print(f"  完成状态: {done!r}")
    check("D10c 完成状态显示", "完成" in str(done), str(done))

    w.apply_all_done(1, 0, 1)
    app.root.update()
    stat = w.lbl_stat.cget("text")
    print(f"  汇总: {stat!r}")
    check("D10d 汇总显示成功数", "1" in stat, stat)

    # D10e 勾选切换
    w._toggle_all()
    app.root.update()
    marks = {w.tree.item(i, "values")[0] for i in w.tree.get_children()}
    print(f"  全选后勾选状态: {marks}")
    check("D10e 全选/反选可用", marks in ({"☑"}, {"☐"}), str(marks))

    # D11d 重复点开复用窗口（不新建）
    first_id = str(w.win)
    app._on_douyin()
    app.root.update()
    check("D11d 重复点开复用同一窗口",
          str(getattr(app, "_dy_win").win) == first_id)

    try:
        w.win.destroy()
    except Exception:
        pass

app.root.destroy()

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
