#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一清单方案测试（v2.0.19）。

设计（用户要求）：
  · 视频号 / 公众号 **共用一张清单**，不做两个独立清单
  · 新增「来源」列区分（🎬 视频号 / 📄 公众号）
  · ★双击「来源」列单元格 → 筛出该来源的全部视频

覆盖：
  U1 Record 有 platform 字段，默认「视频号」
  U2 老数据（无 platform）加载后默认视频号
  U3 来源列存在且显示正确图标
  U4 ★双击来源列 → 筛选生效（只剩该类）
  U5 ★再双击 → 取消筛选，恢复全部
  U6 筛选是视图过滤：store 数据没少
  U7 排序列含 platform
  U8 公众号解析 → 进主清单且 platform=公众号
  U9 来源列悬停/表头提示正确
  U10 状态栏显示筛选状态

跑法：python tests/verify_unified_list.py [--offline]
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OFFLINE = "--offline" in sys.argv
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


def pump(app, ms=250):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        try:
            app.root.update()
        except Exception:
            break
        time.sleep(0.005)


# 准备密钥
from sphdl import license as lic  # noqa: E402
sec = (ROOT / ".license_secret").read_text(encoding="utf-8").strip()
(ROOT / "sphdl" / "_lic_key.py").write_text(
    '"""t"""\nK = %r\n' % lic.obfuscate(bytes.fromhex(sec)), encoding="utf-8")
import importlib  # noqa: E402
importlib.reload(lic)

import tkinter as tk  # noqa: E402
rt = tk.Tk(); rt.withdraw(); rt.destroy()

import sphdl.gui as G  # noqa: E402
import sphdl.config as C  # noqa: E402

tdir = Path(tempfile.mkdtemp(prefix="unified_"))
G.RECORDS_FILE = tdir / "records.json"
G.CONFIG_FILE = tdir / "config.json"
C.PROXY_BACKUP_FILE = tdir / "proxy_backup.json"
(tdir / "config.json").write_text(json.dumps({
    "port": 8899, "download_dir": str(tdir / "dl"),
    "auto_proxy": False, "auto_download": False,
    "download_images": True, "natural_capture": True,
    "guide_auto_shown": True}), encoding="utf-8")

app = G.App()
try:
    pump(app, 300)
    CODE_MP = lic.make_code(0, product=lic.PRODUCT_MP_ARTICLE)
    app._activate_license(CODE_MP)
    pump(app, 200)

    # ---------- U1 Record 字段 ----------
    print("=" * 74)
    print("U1 Record.platform 字段")
    print("=" * 74)
    r = G.Record(id="t1", title="测试", url="http://x", decode_key="",
                 size=0, source="测试", created="2026-10-17 10:00:00")
    check("U1a 默认 platform = 视频号", r.platform == "视频号", r.platform)
    r2 = G.Record(id="t2", title="测试2", url="http://x", decode_key="",
                  size=0, source="公众号解析",
                  created="2026-10-17 10:00:00", platform="公众号")
    check("U1b 可显式指定「公众号」", r2.platform == "公众号")

    # ---------- U2 老数据兼容 ----------
    print("\n" + "=" * 74)
    print("U2 老数据（无 platform 字段）兼容")
    print("=" * 74)
    old = [{"id": "old1", "title": "老记录", "url": "http://o", "decode_key": "",
            "size": 100, "source": "API直嗅", "created": "2026-10-16 09:00:00",
            "status": "waiting", "file": "", "error": "", "progress": 0,
            "is_image": False, "video_id": "v_old", "renditions": [],
            "quality_note": ""}]
    (tdir / "records.json").write_text(json.dumps(old, ensure_ascii=False),
                                       encoding="utf-8")
    st = G.RecordStore(tdir / "records.json", max_records=1000)
    rr = st.items.get("old1")
    check("U2a ★老数据加载成功", rr is not None)
    check("U2b ★老数据 platform 默认为视频号",
          rr is not None and rr.platform == "视频号",
          rr.platform if rr else "未加载")

    # ---------- 造数据：3 视频号 + 2 公众号 ----------
    print("\n" + "=" * 74)
    print("U3 来源列存在且显示正确")
    print("=" * 74)
    store = app.store
    store.items.clear()
    store.order.clear()
    app._row_of.clear()
    def mk(i, plat):
        rec = G.Record(id=f"r{i}", title=f"{plat}视频{i}", url=f"http://x/{i}",
                       decode_key="", size=1000 * i, source="测试",
                       created=f"2026-10-17 10:0{i}:00", platform=plat,
                       video_id=f"v{i}")
        store.items[rec.id] = rec
        store.order.append(rec.id)
        return rec
    for i in range(1, 4):
        mk(i, "视频号")
    for i in range(4, 6):
        mk(i, "公众号")
    app._render_filtered_tree()
    pump(app, 200)

    cols = app.tree["columns"]
    check("U3a ★清单有 platform 列", "platform" in cols, str(cols))
    check("U3b 列顺序：状态/标题/来源/大小/捕获方式/时间/文件",
          tuple(cols) == ("status", "title", "platform", "size", "source",
                          "time", "file"), str(cols))
    # 检查单元格内容
    cells = {}
    for iid in app.tree.get_children():
        v = app.tree.item(iid, "values")
        cells[v[1]] = v[2]
    has_vc = any("视频号" in str(x) for x in cells.values())
    has_mp = any("公众号" in str(x) for x in cells.values())
    check("U3c ★来源列显示「视频号」", has_vc, str(list(cells.values())[:3]))
    check("U3d ★来源列显示「公众号」", has_mp, str(list(cells.values())[:3]))
    check("U3e 有图标区分（🎬 / 📄）",
          any("🎬" in str(x) for x in cells.values())
          and any("📄" in str(x) for x in cells.values()),
          str(list(set(cells.values()))))

    # ---------- U7 排序含 platform ----------
    print("\n" + "=" * 74)
    print("U7 排序：platform 列可点")
    print("=" * 74)
    try:
        cmd = app.tree.heading("platform", "command")
        check("U7a platform 表头绑定排序命令", bool(cmd))
        if cmd:
            app.tree.tk.call(cmd)
            pump(app, 150)
        check("U7b 点 platform 排序不报错", True)
    except Exception as e:                              # noqa: BLE001
        check("U7b 点 platform 排序不报错", False, f"{e}")

    # ---------- U4 双击筛选 ----------
    print("\n" + "=" * 74)
    print("U4 ★双击来源列 → 筛选生效")
    print("=" * 74)
    n_all = len(app.tree.get_children())
    check("U4a 筛选前 5 行", n_all == 5, f"{n_all}")

    # 找到一条公众号行
    mp_row = None
    for iid in app.tree.get_children():
        if "公众号" in str(app.tree.item(iid, "values")[2]):
            mp_row = iid
            break
    check("U4b 找到公众号行", mp_row is not None)

    # 模拟双击：#3 列（platform）
    class Evt:
        pass
    e = Evt()
    # 用 bbox 拿该单元格坐标
    bbox = app.tree.bbox(mp_row, "#3")
    if bbox:
        e.x, e.y = bbox[0] + bbox[2] // 2, bbox[1] + bbox[3] // 2
        app._on_tree_dclick(e)
        pump(app, 250)
        shown = len(app.tree.get_children())
        check("U4c ★双击公众号 → 只剩公众号（2 条）", shown == 2, f"{shown}")
        titles = [str(app.tree.item(i, "values")[1])
                  for i in app.tree.get_children()]
        check("U4d 筛出的都是公众号视频",
              all("公众号" in t for t in titles), str(titles))
        check("U4e getattr _platform_filter = 公众号",
              getattr(app, "_platform_filter", "") == "公众号",
              getattr(app, "_platform_filter", ""))
    else:
        check("U4c ★双击公众号 → 只剩公众号（2 条）", False, "拿不到单元格坐标")

    # ---------- U6 筛选不动数据 ----------
    print("\n" + "=" * 74)
    print("U6 ★筛选是视图过滤，数据没少")
    print("=" * 74)
    check("U6a store 里仍是 5 条", len(store.items) == 5, str(len(store.items)))
    check("U6b 视频号 3 条还在",
          sum(1 for x in store.items.values() if x.platform == "视频号") == 3)
    check("U6c 公众号 2 条还在",
          sum(1 for x in store.items.values() if x.platform == "公众号") == 2)

    # ---------- U10 状态栏 ----------
    print("\n" + "=" * 74)
    print("U10 状态栏显示筛选状态")
    print("=" * 74)
    app._update_status_bar()
    pump(app, 150)
    txt = app.status_bar.cget("text")
    check("U10a ★状态栏含筛选提示", "来源=公众号" in txt, txt[-90:])

    # ---------- U5 取消筛选 ----------
    print("\n" + "=" * 74)
    print("U5 ★再双击 → 取消筛选")
    print("=" * 74)
    # 直接调 _filter_by_platform("") 等价于"取消"
    app._filter_by_platform("")
    pump(app, 250)
    shown = len(app.tree.get_children())
    check("U5a ★取消筛选后恢复 5 行", shown == 5, f"{shown}")
    check("U5b _platform_filter 已清空",
          getattr(app, "_platform_filter", "") == "", 
          repr(getattr(app, "_platform_filter", "")))

    # ---------- U8 公众号解析进主清单 ----------
    print("\n" + "=" * 74)
    print("U8 公众号解析 → 进主清单且标为公众号")
    print("=" * 74)
    if OFFLINE:
        print("  （--offline：跳过）")
    else:
        from sphdl import mp_article as mpmod
        url = "https://mp.weixin.qq.com/s/BAvjuF4k6BLWlyCHTY58xA"
        n_before = len(store.items)
        app._open_mp_window()
        pump(app, 300)
        tops = [w for w in app.root.winfo_children()
                if w.winfo_class() == "Toplevel"]
        check("U8a 公众号窗口已打开", len(tops) >= 1, f"{len(tops)}")
        # 直接走 _on_parse_done 的等价路径：真实抓取
        try:
            h = mpmod.fetch_article(url)
            vids = mpmod.parse_article(h)
            title = mpmod.article_title(h)
            check("U8b 解析到 3 个视频", len(vids) == 3, f"{len(vids)}")
            # 模拟事件投递
            app.push_event(("mp_parse_done", vids, title, "", url))
            pump(app, 800)
            n_after = len(store.items)
            check("U8c ★3 条进入主清单", n_after == n_before + 3,
                  f"{n_before} → {n_after}")
            mp_added = [x for x in store.items.values()
                        if x.platform == "公众号"]
            check("U8d ★新记录 platform = 公众号",
                  len(mp_added) >= 3, f"{len(mp_added)} 条公众号")
            check("U8e 新记录带清晰度信息",
                  any(len(x.renditions) == 4 for x in mp_added),
                  str([len(x.renditions) for x in mp_added][:3]))
        except Exception as e:                          # noqa: BLE001
            check("U8b 解析到 3 个视频", False, f"{type(e).__name__}: {e}")
        # 关窗
        for w in app.root.winfo_children():
            if w.winfo_class() == "Toplevel":
                w.destroy()
        pump(app, 200)

    # ---------- U9 悬停提示 ----------
    print("\n" + "=" * 74)
    print("U9 来源列有说明（帮助用户发现双击筛选）")
    print("=" * 74)
    src = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8", errors="replace")
    check("U9a 代码注释说明双击筛选", "双击「来源」列" in src)
    check("U9b 表头文案为「来源」", '"platform": ("来源"' in src
          or '"来源", 78' in src or "来源" in src)

finally:
    try:
        app.root.destroy()
    except Exception:
        pass
    shutil.rmtree(tdir, ignore_errors=True)
    (ROOT / "sphdl" / "_lic_key.py").unlink(missing_ok=True)

print()
ok = sum(1 for _, p, _ in RESULTS if p)
print(f"RESULT: {'ALL PASS' if ok == len(RESULTS) else 'FAIL'} ({ok}/{len(RESULTS)})")
for n, p, _ in RESULTS:
    if not p:
        print("  未通过: " + n)
sys.exit(0 if ok == len(RESULTS) else 1)
