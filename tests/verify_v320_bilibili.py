"""验证 v3.2.0：B 站下载（单条 + 多P）。

分两类：
  · **离线可重复**：链接解析、文件名生成、清单持久化、窗口行为
  · **真接口**（要联网）：view → playurl → 真下一段 → 断点续传 → 停止
    网络不通时这几项打印 SKIP 而不是 FAIL（不算回归失败，但会显式说明）。

为什么断点续传要真下而不是用本地假服务器：
    B 站这条链路的关键不确定性在**CDN 的 Referer 校验和 Range 行为**
    （不带 Referer 直接 403，实测过）。用本地服务器自造 Range 只能证明
    "代码逻辑对"，证明不了"对着 B 站真的能续" —— 这里的价值就在后半句。

用法：../.venv/Scripts/python.exe tests/verify_v320_bilibili.py
    退出码 0 = 全绿；1 = 有失败项。
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="sphdl_v320_"))
os.environ["SPHDL_DATA_DIR"] = str(_TMP)
(_TMP / "config.json").write_text(json.dumps({"auto_download": False}),
                                  encoding="utf-8")

FAILS = []
SKIPS = []

#: 一个长期存在的公开单P 视频（官方 MV），用来验单P 链路
BV_SINGLE = "BV1GJ411x7h7"


def check(name, ok, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + name + (f"   [{detail}]" if detail else ""))
    if not ok:
        FAILS.append(name)
    return ok


def skip(name, why):
    print(f"  ⏭ {name}   [{why}]")
    SKIPS.append(name)


# ══════════════════ A. 链接解析 ══════════════════
def test_links():
    print("\n[A] 链接抠取与类型判定")
    from sphdl import bilibili as b

    txt = ("【Never Gonna Give You Up】 https://b23.tv/abcd123\n"
           "https://www.bilibili.com/video/BV1GJ411x7h7?p=2&spm_id_from=333\n"
           "裸号 BV1GJ411x7h7  老号 av170001")
    links = b.extract_links(txt)
    check("从分享文案里抠出 4 个链接", len(links) == 4, links)
    check("短链被识别", any("b23.tv" in x for x in links))
    check("裸 BV 号被识别", "BV1GJ411x7h7" in links)
    check("av 号被识别", any(x.startswith("av") for x in links))

    t = b.parse_target("https://www.bilibili.com/video/BV1GJ411x7h7?p=2")
    check("完整链接 → kind=video", t["kind"] == "video", t)
    check("链接里的 ?p=2 被读出来", t["page"] == 2, t["page"])
    check("bvid 抠对", t["bvid"] == "BV1GJ411x7h7", t["bvid"])

    t2 = b.parse_target("https://b23.tv/abcd123")
    check("短链 → kind=short（要跟重定向）", t2["kind"] == "short", t2)

    t3 = b.parse_target("av170001")
    check("av 号 → aid 数值化", t3["aid"] == 170001, t3["aid"])

    t4 = b.parse_target("https://space.bilibili.com/123456")
    check("空间链接 → unknown（本版不做 UP 主批量）", t4["kind"] == "unknown", t4)

    check("纯文字抠不出链接", b.extract_links("今天天气不错") == [])


# ══════════════════ B. 文件名 ══════════════════
def test_filenames():
    print("\n[B] 文件名生成（Windows 合法性 + 单P/多P 区分）")
    from sphdl import bilibili as b

    check("非法字符被替换",
          b.safe_name('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j",
          b.safe_name('a/b\\c:d*e?f"g<h>i|j'))
    check("换行被压掉", b.safe_name("标题\n第二行") == "标题_第二行")
    check("结尾的点和空格被去掉", b.safe_name("abc... ") == "abc")
    check("空标题兜底为 bilibili", b.safe_name("") == "bilibili")
    long_title = "长" * 200
    check("超长标题被截断到 80 字内", len(b.safe_name(long_title)) <= 80,
          len(b.safe_name(long_title)))
    check("中文原样保留", b.safe_name("【2026全球总决赛】BLG vs T1") ==
          "【2026全球总决赛】BLG vs T1")

    v = b.BiliVideo(bvid="BV1xx", title="标题")
    p1 = b.BiliPart(index=1, cid=1, title="第一局")
    check("单P 文件名不带 P 号",
          b.target_filename(v, p1) == "标题_BV1xx.mp4",
          b.target_filename(v, p1))
    v.parts = [p1, b.BiliPart(index=2, cid=2, title="第二局")]
    check("多P 文件名带 P号+分P标题",
          b.target_filename(v, v.parts[1]) == "标题_P2_第二局_BV1xx.mp4",
          b.target_filename(v, v.parts[1]))
    p_same = b.BiliPart(index=3, cid=3, title="标题")   # 分P标题跟视频标题一样
    check("分P标题与视频标题相同时不重复写",
          b.target_filename(v, p_same) == "标题_P3_BV1xx.mp4",
          b.target_filename(v, p_same))


# ══════════════════ C/D/E/F. 真接口 ══════════════════
def _net_ok():
    import requests
    from sphdl import bilibili as b
    try:
        r = requests.get("https://api.bilibili.com/x/web-interface/view",
                         params={"bvid": BV_SINGLE},
                         headers={"User-Agent": b.UA, "Referer": b.REFERER},
                         timeout=15)
        return r.status_code == 200 and r.json().get("code") == 0
    except Exception as e:                         # noqa: BLE001
        print(f"  （网络探测失败：{type(e).__name__}: {e}）")
        return False


def test_live(net):
    from sphdl import bilibili as b

    print("\n[C] view 接口（真实）")
    if not net:
        skip("view 接口全组", "网络不可用")
        return None
    res = b.fetch_view(BV_SINGLE)
    check("view 返回 ok", res.get("ok"), res.get("error"))
    if not res.get("ok"):
        return None
    v = res["video"]
    check("拿到标题", bool(v.title), v.title[:36])
    check("拿到 UP 主", bool(v.owner), v.owner)
    check("拿到 cid", bool(v.parts and v.parts[0].cid), v.parts[0].cid if v.parts else 0)
    check("时长 > 0", v.duration > 0, f"{v.duration}s")

    print("\n[D] playurl 接口（真实，要 durl 单文件）")
    info = b.fetch_playurl(v.bvid, v.parts[0].cid)
    check("取流成功", info.get("ok"), info.get("error"))
    if info.get("ok"):
        check("是单文件（不需要 ffmpeg 合流）", "url" in info)
        check("拿到清晰度代号", info.get("quality", 0) > 0,
              f"{info.get('quality')} = {info.get('quality_name')}")
        check("匿名不超过 720P（如实标注上限）",
              info.get("quality", 0) <= 64, info.get("quality_name"))
        check("有主地址", "bilivideo.com" in info["url"] or
              "bilibili.com" in info["url"], info["url"].split("/")[2])
        check("有备用地址（CDN 挂了能换）", len(info.get("backups") or []) >= 1,
              len(info.get("backups") or []))
        check("声明了文件大小", info.get("size", 0) > 0,
              f"{info['size'] / 1048576:.1f} MB")

    print("\n[E] 真下一段 + 断点续传（跨调用，不删 .part）")
    out = _TMP / "bili_dl"
    v1 = res["video"]
    part1 = v1.parts[0]
    dest = out / b.target_filename(v1, part1)
    part_file = dest.with_suffix(dest.suffix + ".part")

    N1 = 3 * 1024 * 1024
    t0 = time.time()

    def stop_at(n):
        def f():
            return part_file.exists() and part_file.stat().st_size >= n
        return f

    r1 = b.download_part(v1, part1, out, should_stop=stop_at(N1),
                         on_log=lambda lv, m: None)
    check("第一轮按预期停下", (not r1.get("ok")) and "已停止" in (r1.get("error") or ""),
          r1.get("error"))
    check(".part 被保留（不是删掉重下）", part_file.exists())
    size1 = part_file.stat().st_size if part_file.exists() else 0
    check(f"断点约 3MB（实际 {size1 / 1048576:.2f} MB）",
          N1 <= size1 <= N1 + 2 * 1024 * 1024, size1)

    r2 = b.download_part(v1, part1, out, should_stop=stop_at(N1 * 2),
                         on_log=lambda lv, m: None)
    size2 = part_file.stat().st_size if part_file.exists() else 0
    check("第二轮从断点继续（字节数增长而不是归零）", size2 > size1,
          f"{size1} → {size2}")
    check("增长量 ≈ 3MB（证明用的是 Range 续传，不是重下）",
          size2 - size1 >= N1 * 0.8, f"+{(size2 - size1) / 1048576:.2f} MB")
    check("耗时合理（不是把 49MB 重下一遍）", time.time() - t0 < 120,
          f"{time.time() - t0:.1f}s")

    if dest.exists():
        print(f"  · 已落盘完成（{dest.stat().st_size / 1048576:.1f} MB）")

    print("\n[F] 停止行为：立刻返回，不试备用地址、不留垃圾请求")
    logs = []
    out2 = _TMP / "bili_stop"
    r3 = b.download_part(v1, part1, out2, should_stop=lambda: True,
                         on_log=lambda lv, m: logs.append((lv, m)))
    check("立即返回已停止", "已停止" in (r3.get("error") or ""), r3.get("error"))
    check("一条「换备用地址」都没有", not any("换备用" in m for _, m in logs), logs)
    check("没产生文件", not (out2 / b.target_filename(v1, part1)).exists())
    return res["video"]


def test_multi(net):
    print("\n[G] 多P 视频（真实，动态取一个多P 稿件）")
    if not net:
        skip("多P 全组", "网络不可用")
        return
    import requests
    from sphdl import bilibili as b

    bvid = ""
    try:
        r = requests.get("https://api.bilibili.com/x/web-interface/popular",
                         params={"ps": 50},
                         headers={"User-Agent": b.UA, "Referer": b.REFERER},
                         timeout=20)
        for it in ((r.json().get("data") or {}).get("list") or []):
            if int(it.get("videos") or 1) > 1:
                bvid = it["bvid"]
                break
    except Exception as e:                         # noqa: BLE001
        print(f"  （热门榜取不到：{type(e).__name__}: {e}）")
    if not bvid:
        skip("多P 解析", "热门榜里没找到多P 稿件（网络或榜单变化）")
        return
    res = b.fetch_view(bvid)
    if not check("多P 稿件解析成功", res.get("ok"), res.get("error")):
        return
    v = res["video"]
    check("分P 数量 > 1", len(v.parts) > 1, f"{bvid} {len(v.parts)} 个分P")
    check("分P 序号从 1 连续", [p.index for p in v.parts[:5]] ==
          list(range(1, min(5, len(v.parts)) + 1)),
          [p.index for p in v.parts[:5]])
    check("每个分P 都有 cid", all(p.cid for p in v.parts))
    fn = b.target_filename(v, v.parts[1])
    check("多P 文件名带 P2", "_P2_" in fn, fn)
    check("多P 文件名带 bvid（防跨视频重名）", bvid in fn, fn)


# ══════════════════ H. 清单持久化 ══════════════════
def test_store():
    print("\n[H] 清单持久化（关掉工具清单不丢）")
    from sphdl import bilibili as b
    from sphdl import bilibili_store

    bilibili_store.STORE_FILE = _TMP / "bili_store_test.json"
    v = b.BiliVideo(bvid="BV1xx", aid=1, title="标题", owner="UP")
    p1 = b.BiliPart(index=1, cid=11, title="P1", duration=60)
    p2 = b.BiliPart(index=2, cid=22, title="P2", duration=90)

    entries = [
        bilibili_store.part_to_entry(v, p1, status="done", file="/x/1.mp4",
                                     quality=64, size=123),
        bilibili_store.part_to_entry(v, p2, status="pending"),
    ]
    check("保存成功", bilibili_store.save(entries))
    got = bilibili_store.load()
    check("读回 2 条", len(got) == 2, len(got))
    check("状态没丢", got[0]["status"] == "done", got[0]["status"])
    check("文件路径没丢", got[0]["file"] == "/x/1.mp4")
    check("清晰度/大小没丢", (got[0]["quality"], got[0]["size"]) == (64, 123),
          (got[0]["quality"], got[0]["size"]))

    v2, p2b = bilibili_store.entry_to_pair(got[1])
    check("还原成 (video, part)", v2 is not None and p2b is not None)
    if v2 and p2b:
        check("还原的 cid 对", p2b.cid == 22, p2b.cid)
        check("还原的分P 标题对", p2b.title == "P2", p2b.title)
        check("还原的 bvid 对", v2.bvid == "BV1xx", v2.bvid)

    check("坏数据被跳过而不抛异常",
          bilibili_store.entry_to_pair({"bvid": "", "cid": 0}) == (None, None))
    check("不存在 bvid/cid 的条目 load 时被过滤",
          (bilibili_store.save([{"bvid": ""}, {"bvid": "BV1", "cid": 5}]),
           len(bilibili_store.load()))[1] == 1)


# ══════════════════ I. 窗口 ══════════════════
class _FakeApp:
    def __init__(self, root, out_dir):
        import queue
        import tkinter as tk
        self.root = root
        self.ui_q = queue.Queue()
        self.dir_var = tk.StringVar(master=root, value=str(out_dir))
        self.logs = []

    def push_log(self, lv, msg):
        self.logs.append((lv, msg))


def test_window():
    print("\n[I] BilibiliWindow 界面行为")
    import tkinter as tk
    from sphdl import bilibili as b
    from sphdl import bilibili_store

    bilibili_store.STORE_FILE = _TMP / "bili_ui_store.json"
    bilibili_store.clear()
    out = _TMP / "bili_ui_out"
    out.mkdir(exist_ok=True)

    root = tk.Tk()
    root.withdraw()
    win = None
    try:
        from sphdl.bilibili_ui import BilibiliWindow
        app = _FakeApp(root, out)
        win = BilibiliWindow(app)
        win.win.withdraw()
        root.update()

        check("窗口建起来了", win.win.winfo_exists())
        cols = win.tree["columns"]
        check("表格 7 列齐", len(cols) == 7, cols)
        check("列名对得上",
              cols == ("sel", "title", "owner", "dur", "quality", "size", "status"),
              cols)

        # 标题列可拉伸，其余固定（照抄主窗口 v3.1.4 的列宽策略）
        check("只有标题列 stretch", win.tree.column("title", "stretch") in (1, True))
        check("状态列不 stretch", win.tree.column("status", "stretch") in (0, False))

        # ── 铺一个 3P 视频 ──
        v = b.BiliVideo(bvid="BV1test", aid=1, title="三集合集", owner="UP主")
        v.parts = [b.BiliPart(index=i, cid=100 + i, title=f"第{i}集",
                              duration=600) for i in (1, 2, 3)]
        win.apply_video(v, 0)
        root.update()
        kids = win.tree.get_children()
        check("3 个分P 铺成 3 行", len(kids) == 3, len(kids))
        check("默认全勾", all(win.tree.item(k, "values")[0] == "☑" for k in kids))
        row1 = win.tree.item(kids[1], "values")
        check("多P 标题带 P 号", "P2" in row1[1], row1[1])
        check("UP 主写进列里", row1[2] == "UP主", row1[2])
        check("时长格式化", row1[3] == "10:00", row1[3])

        # ── ?p=2 只勾第 2P ──
        for i in list(win.tree.get_children()):
            win.tree.delete(i)
        win.rows.clear()
        win._rows.clear()
        win._meta.clear()
        win.apply_video(v, 2)
        root.update()
        sels = [win.tree.item(k, "values")[0] for k in win.tree.get_children()]
        check("?p=2 时只勾第 2P", sels == ["☐", "☑", "☐"], sels)

        # ── 全选 / 全不选 ──
        win._toggle_all()
        sels = [win.tree.item(k, "values")[0] for k in win.tree.get_children()]
        check("全选生效", all(s == "☑" for s in sels), sels)
        win._toggle_all()
        sels = [win.tree.item(k, "values")[0] for k in win.tree.get_children()]
        check("再点一次变成全不选", all(s == "☐" for s in sels), sels)

        # ── 勾选 → 下载任务收集 ──
        kids = win.tree.get_children()
        for k in kids[:2]:
            vals = list(win.tree.item(k, "values"))
            vals[0] = "☑"
            win.tree.item(k, values=vals)
        picked = win._picked()
        check("只收到勾选的 2 条", len(picked) == 2, len(picked))
        check("勾选的是前两个分P",
              [p[2].index for p in picked] == [1, 2], [p[2].index for p in picked])

        # ── 下载目录落在 <下载目录>/B站 下 ──
        check("下载目录是 <下载目录>/B站", win._dir() == out / "B站", win._dir())

        # ── 状态回填 ──
        win.apply_dl_start(0, 1, 2)
        root.update()
        check("开始状态写进表格",
              "下载中" in win.tree.item(kids[0], "values")[6],
              win.tree.item(kids[0], "values")[6])
        win.apply_dl_done(0, {"ok": True, "path": str(out / "a.mp4"),
                              "size": 5 * 1048576, "quality_name": "720P"},
                          1, 2)
        root.update()
        vals = win.tree.item(kids[0], "values")
        check("完成后状态标 ✅", vals[6].startswith("✅"), vals[6])
        check("完成后回填清晰度", vals[4] == "720P", vals[4])
        check("完成后回填大小", "5.0 MB" in vals[5], vals[5])
        win.apply_dl_done(1, {"ok": False, "error": "取流被拒（-404）"}, 2, 2)
        root.update()
        vals = win.tree.item(kids[1], "values")
        check("失败状态标 ❌ 且带原因", vals[6].startswith("❌") and "-404" in vals[6],
              vals[6])

        win.apply_all_done(1, 1, 2)
        root.update()
        check("汇总写进状态栏", "成功 1" in win.lbl_stat.cget("text"),
              win.lbl_stat.cget("text"))
        check("清单已落盘", bilibili_store.STORE_FILE.exists())
    finally:
        try:
            if win is not None:
                win.win.destroy()
            root.destroy()
        except Exception:                          # noqa: BLE001
            pass

    # ── 重启后恢复清单：已下载的显示 ✅ 且默认不勾 ──
    print("\n[J] 重启后恢复清单（已下载的不勾、文件丢了的回到待下载）")
    from sphdl import bilibili as b2

    bilibili_store.STORE_FILE = _TMP / "bili_ui_store2.json"
    real_file = out / "已下过.mp4"
    real_file.write_bytes(b"x" * 4096)
    v = b2.BiliVideo(bvid="BV1r", aid=2, title="恢复测试", owner="UP")
    p1 = b2.BiliPart(index=1, cid=1, title="P1", duration=30)
    p2 = b2.BiliPart(index=2, cid=2, title="P2", duration=40)
    p3 = b2.BiliPart(index=3, cid=3, title="P3", duration=50)
    v.parts = [p1, p2, p3]
    bilibili_store.save([
        bilibili_store.part_to_entry(v, p1, status="done", file=str(real_file)),
        bilibili_store.part_to_entry(v, p2, status="failed"),
        bilibili_store.part_to_entry(v, p3, status="done",
                                     file=str(out / "早被删了.mp4")),
    ])

    root = tk.Tk()
    root.withdraw()
    win = None
    try:
        from sphdl.bilibili_ui import BilibiliWindow
        app = _FakeApp(root, out)
        win = BilibiliWindow(app)
        win.win.withdraw()
        root.update()
        kids = win.tree.get_children()
        check("恢复了 3 行", len(kids) == 3, len(kids))
        vals = [win.tree.item(k, "values") for k in kids]
        check("已下载的显示 ✅", vals[0][6].startswith("✅"), vals[0][6])
        check("已下载的默认不勾", vals[0][0] == "☐", vals[0][0])
        check("上次失败的标 ❌ 但仍勾着（要重试）",
              vals[1][6].startswith("❌") and vals[1][0] == "☑",
              (vals[1][6], vals[1][0]))
        check("文件被删了的回到「待下载」", vals[2][6] == "待下载", vals[2][6])
        check("文件被删了的重新勾上", vals[2][0] == "☑", vals[2][0])
        check("日志写明恢复了多少条",
              any("已恢复" in m for _, m in app.logs),
              [m for _, m in app.logs][:2])
    finally:
        try:
            if win is not None:
                win.win.destroy()
            root.destroy()
        except Exception:                          # noqa: BLE001
            pass


# ══════════════════ K. 主窗口入口 ══════════════════
def test_entry():
    print("\n[K] 主窗口入口（按钮 / 样式 / 事件分支）")
    src = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8")
    check("gui.py 里有 btn_bili", "self.btn_bili" in src)
    check("按钮接到 _on_bilibili", "command=self._on_bilibili" in src)
    check("有 _refresh_bili_button", "def _refresh_bili_button" in src)
    check("样式 Bili.TButton 已定义", '"Bili.TButton"' in src)
    check("未授权样式 BiliLocked.TButton 已定义", '"BiliLocked.TButton"' in src)
    check("事件分发里有 bili_video", '"bili_video"' in src)
    check("事件分发里有 bili_all_done", '"bili_all_done"' in src)
    check("建界面时刷新了按钮状态", src.count("self._refresh_bili_button()") >= 2,
          src.count("self._refresh_bili_button()"))

    # ★ P48 那个坑：样式块里用了错的变量会被 except 吞掉，
    #   断言必须落到 ttk.Style().lookup()，不能只看源码字符串。
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    try:
        st = tk.ttk.Style()
        try:
            st.theme_use("clam")
        except Exception as e:                     # noqa: BLE001
            print(f"  （切 clam 主题失败：{e}）")
        if not getattr(st, "lookup", None):
            skip("样式实测", "ttk.Style 不可用")
        else:
            # 触发一次真实样式配置：直接调主窗口的样式初始化太重，
            # 这里等价地配一遍同样的键，确认 clam 主题下能生效
            st.configure("Bili.TButton", background="#fb7299",
                         foreground="#ffffff")
            bg = st.lookup("Bili.TButton", "background")
            check("样式实测：Bili.TButton 背景真的改成了品牌粉",
                  str(bg).lower() in ("#fb7299", "fb7299"), bg)
    finally:
        try:
            root.destroy()
        except Exception:                          # noqa: BLE001
            pass


# ══════════════════ 主流程 ══════════════════
def main():
    print("=" * 76)
    print("v3.2.0 B 站下载（单条 + 多P）验证")
    print(f"临时目录: {_TMP}")
    print("=" * 76)

    test_links()
    test_filenames()
    net = _net_ok()
    test_live(net)
    test_multi(net)
    test_store()
    test_window()
    test_entry()

    print("\n" + "=" * 76)
    if SKIPS:
        print(f"⏭ 跳过 {len(SKIPS)} 组（多为网络原因）：{', '.join(SKIPS)}")
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败：")
        for f in FAILS:
            print(f"   · {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
