"""公众号下载 + 筛选回退 修复验证（v2.2.3）。

两个客户报障（2026-10-23）：

  【报障1】公众号解析出的视频点下载 → 状态「失败」
     根因：mpvideo.qpic.cn 校验 Referer，下载器只设了 UA 没设 Referer
     实测：无 Referer → 403；带 mp.weixin.qq.com → 200

  【报障2】点「在主清单中查看」后，找不到回视频号清单了
     根因：_goto_main("公众号") 把清单筛成只看公众号，
           取消入口只有「双击来源列」，客户发现不了
           （旧提示还说"点状态栏显示全部"，但那按钮从不存在）

覆盖：
  R1 _needs_mp_referer 域名判定
  R2 下载器对 mpvideo URL 自动带 Referer
  R3 下载器对视频号 URL 不带 Referer
  R4 ★真实链接下载成功（百度 CDN 200 + 文件有效）
  R5 筛选横幅存在且有「显示全部」按钮
  R6 筛选后横幅可见 + 文案说明「只是隐藏没删除」
  R7 点「显示全部」→ 横幅隐藏 + 记录全回来
  R8 「只看视频号」按钮能正确筛选
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


print("=" * 76)
print("R1 Referer 域名判定")
print("=" * 76)
from sphdl.downloader import _needs_mp_referer

cases = [
    ("http://mpvideo.qpic.cn/0b2e.xxx.f10104.mp4?dis_k=a", True),
    ("https://mpvideo.qpic.cn/abc.mp4", True),
    ("https://finder.video.qq.com/251/20302/stodownload?x=1", False),
    ("https://sns-video-bd.xhscdn.com/abc", False),
    ("", False),
]
for u, want in cases:
    got = _needs_mp_referer(u)
    print(f"  {u[:50]:<52} → {got}")
    check(f"R1 {want}", got == want, str(got))

print()
print("=" * 76)
print("R2/R3 下载器 headers")
print("=" * 76)
from sphdl.downloader import MpdDownloader

d1 = MpdDownloader("http://mpvideo.qpic.cn/x.mp4", "C:/tmp/a.mp4")
print("  mpvideo headers:", {k: v[:40] for k, v in d1.headers.items()})
check("R2a mpvideo 带 Referer", "Referer" in d1.headers)
check("R2b Referer 值正确",
      d1.headers.get("Referer") == "https://mp.weixin.qq.com/",
      str(d1.headers.get("Referer")))
check("R2c 仍带 UA", "User-Agent" in d1.headers)

d2 = MpdDownloader("https://finder.video.qq.com/251/x", "C:/tmp/b.mp4")
print("  视频号 headers:", {k: v[:40] for k, v in d2.headers.items()})
check("R3 视频号不带 Referer", "Referer" not in d2.headers)

print()
print("=" * 76)
print("R4 ★真实链接下载（公众号视频）")
print("=" * 76)
import tempfile
try:
    from sphdl.mp_article import fetch_mp_videos
    url = ("https://mp.weixin.qq.com/s/"
           "0wttwnZg1ut4bpsEmPFmoQ?scene=1&click_id=1403243094")
    print("  正在解析文章…")
    vids = fetch_mp_videos(url, timeout=40)
    print(f"  解析出 {len(vids)} 个视频")
    if vids:
        v = vids[0]
        print(f"  默认清晰度 URL: {v.url[:80]}…")
        out = Path(tempfile.gettempdir()) / "mp_test_dl.mp4"
        if out.exists():
            out.unlink()
        dl = MpdDownloader(v.url, str(out))
        ok = dl.start()
        size = out.stat().st_size if out.exists() else 0
        print(f"  下载结果: ok={ok}  大小={size} 字节")
        check("R4a 下载成功（不再是 403）", bool(ok), f"ok={ok}")
        check("R4b 文件有内容（>100KB）", size > 100_000, f"{size} 字节")
        if out.exists():
            head = out.read_bytes()[:12]
            is_mp4 = head[4:8] == b"ftyp"
            print(f"  文件头: {head[:12]!r}  MP4={is_mp4}")
            check("R4c 是有效 MP4（ftyp 头）", is_mp4, str(head[:8]))
            out.unlink()
    else:
        check("R4a 下载成功（不再是 403）", False, "没解析出视频")
except Exception as e:
    check("R4a 下载成功（不再是 403）", False, f"{type(e).__name__}: {str(e)[:60]}")

print()
print("=" * 76)
print("R5-R8 筛选横幅（回退入口）")
print("=" * 76)
import tkinter as tk
import sphdl.gui as G

app = G.App()
app.root.withdraw()
app.root.update()

has_bar = hasattr(app, "filter_bar")
print("  filter_bar 存在:", has_bar)
check("R5a 筛选横幅已创建", has_bar)

if has_bar:
    btn_all = getattr(app, "btn_filter_all", None)
    btn_vid = getattr(app, "btn_filter_video", None)
    print("  显示全部按钮:", "有" if btn_all else "无")
    print("  只看视频号按钮:", "有" if btn_vid else "无")
    check("R5b 有「显示全部」按钮", btn_all is not None)
    check("R5c 有「只看视频号」按钮", btn_vid is not None)
    # ★ 窗口被 withdraw 时所有子控件 ismapped 都是 0，不能当判据；
    #   用 winfo_manager()：被 pack 过 → "pack"，pack_forget 后 → ""
    def bar_shown(w):
        try:
            return w.winfo_manager() == "pack"
        except Exception:
            return False
    check("R5d 初始状态横幅隐藏", not bar_shown(app.filter_bar),
          "manager=" + str(app.filter_bar.winfo_manager()))

    # 造两条不同来源的记录
    import uuid
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for plat in ("视频号", "公众号"):
        rec = G.Record(
            id="t" + uuid.uuid4().hex[:8], title=f"测试-{plat}", url="http://x",
            decode_key="", size=1, source="test", created=now,
            status="waiting", platform=plat, seq=app._next_seq())
        if app.store.add(rec):
            app._add_row(rec)
    app.root.update()
    print(f"  造了 2 条记录，当前 total={len(app.store.order)}")

    # 筛选公众号
    app._filter_by_platform("公众号")
    app.root.update()
    print(f"  筛选公众号后横幅 manager: {app.filter_bar.winfo_manager()!r}")
    check("R6a 筛选后横幅显示", bar_shown(app.filter_bar),
          "manager=" + str(app.filter_bar.winfo_manager()))
    txt = app.lbl_filter.cget("text")
    print(f"  横幅文案: {txt}")
    check("R6b 文案说明只是隐藏", "隐藏" in txt, txt)
    check("R6c 文案说明没删除", "没有删除" in txt, txt)

    kids = len(app.tree.get_children())
    print(f"  筛选后可见行数: {kids}")
    check("R6d 筛选后只显示公众号行", kids >= 1, str(kids))

    # 点「显示全部」
    if btn_all:
        btn_all.invoke()
        app.root.update()
        print(f"  「显示全部」后横幅 manager: {app.filter_bar.winfo_manager()!r}")
        check("R7a 点显示全部后横幅隐藏", not bar_shown(app.filter_bar),
              "manager=" + str(app.filter_bar.winfo_manager()))
        after = len(app.tree.get_children())
        print(f"  全部行数: {after}（筛选时 {kids}）")
        check("R7b 记录全回来了", after > kids, f"{kids} → {after}")

    # 只看视频号
    if btn_vid:
        btn_vid.invoke()
        app.root.update()
        print(f"  「只看视频号」后横幅 manager: {app.filter_bar.winfo_manager()!r}")
        check("R8a 只看视频号后横幅显示", bar_shown(app.filter_bar),
              "manager=" + str(app.filter_bar.winfo_manager()))
        check("R8b 筛选值正确",
              getattr(app, "_platform_filter", "") == "视频号",
              str(getattr(app, "_platform_filter", "")))
        app._filter_by_platform("")

# 清理测试记录
for rid in [r.id for r in list(app.store.items.values())
            if r.title.startswith("测试-")]:
    try:
        app._remove_row(rid)
    except Exception:
        pass

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
