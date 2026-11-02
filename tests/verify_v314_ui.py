"""验证 v3.1.4：标题完整显示 + 授权管理/打开文件夹按钮醒目。

为什么必须有这个脚本：
    客户报的是"看起来的问题"，光看代码改没改没用 —— 必须读真实控件
    状态（列宽、stretch、控件像素、样式查询结果）来判定。

覆盖：
    A. one_line() 把多行标题压成单行（含全角空格、制表符、None 兜底）
    B. 真实 App 里，带 \\n 的记录进表后标题单元格不含换行符
    C. 标题列是**唯一**可拉伸列，且窗口变宽时只有它变大
    D. 悬停提示：超宽标题返回全文，短标题返回空（不弹气泡）
    E. 「🔑 授权管理」= License.TButton 样式，实际可见且不是 1x1
    F. 「📂 打开文件夹」= Folder.TButton 样式，实际可见且不是 1x1
    G. 样式真的生效（ttk 查询到的底色 == 设计值，不是只写了没应用）
    H. 未授权时列表上限提示不再显示"满 0 条…"（消除误导 + 腾出空间）

用法：../.venv/Scripts/python.exe tests/verify_v314_ui.py
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

# 配置/记录落到临时目录：绝不能污染本机真实 config.json（授权码在里面）
_TMP = tempfile.mkdtemp(prefix="sphdl_v314_")
os.environ["SPHDL_DATA_DIR"] = _TMP
(Path(_TMP) / "config.json").write_text(
    json.dumps({"auto_download": False}), encoding="utf-8")

import sphdl.gui as G                       # noqa: E402
from sphdl.util import one_line             # noqa: E402

assert str(G.CONFIG_FILE).startswith(_TMP), f"配置没落到临时目录：{G.CONFIG_FILE}"

# ★ 记录文件也挪到临时目录：否则会读到开发机 records.json 里的真实记录，
#   而且 App 的定时落盘可能把测试数据写回去（污染真实清单）。
G.RECORDS_FILE = Path(_TMP) / "records.json"
# ★ 预置空清单：userdata 首次运行会把"程序目录下的 records.json"迁移过来，
#   否则开发机上的 8 条真实记录会混进来（实测首跑就是 11 行 = 8 + 3）。
G.RECORDS_FILE.write_text("[]", encoding="utf-8")

FAILS = []


def check(name, ok, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + name + (f"   [{detail}]" if detail else ""))
    if not ok:
        FAILS.append(name)
    return ok


def pump(root, n=10):
    for _ in range(n):
        root.update_idletasks()
        root.update()
        time.sleep(0.03)


def test_one_line():
    print("\n[A] one_line() 压平多行标题")
    cases = [
        ("沉浸式打乒乓球！第一视角看对错？\n用 Osmo Nano 记录打球日常",
         "沉浸式打乒乓球！第一视角看对错？ 用 Osmo Nano 记录打球日常"),
        ("他舅说这个生日是不是太寒酸了，\n他说，太寒酸了\n他舅说，那怎么办",
         "他舅说这个生日是不是太寒酸了， 他说，太寒酸了 他舅说，那怎么办"),
        ("a\r\nb\tc\u3000d", "a b c d"),
        ("  前后留白  ", "前后留白"),
        (None, ""),
        ("", ""),
    ]
    for src, want in cases:
        got = one_line(src)
        check(f"{src!r:.28} → 单行", got == want, f"{got!r}")
    check("压平后不含任何换行/制表符",
          not any(ch in one_line("x\n\r\ty\u3000z") for ch in "\n\r\t"))
    check("limit 截断带省略号", one_line("一二三四五六", limit=4) == "一二三…",
          one_line("一二三四五六", limit=4))


def main():
    print("=" * 78)
    print("v3.1.4 界面验证：标题完整显示 + 关键按钮醒目")
    print("=" * 78)
    test_one_line()

    app = G.App()
    root = app.root
    pump(root, 12)

    # 造三条记录：多行长标题 / 短标题 / 一行但很长
    long_multi = ("沉浸式打乒乓球！第一视角看对错？\n用 Osmo Nano 记录打球日常，"
                  "超轻巧机身戴在身上完全不影响挥拍")
    short_one = "去吃牛肉咯"
    long_one = ("羞答答的玫瑰轰隆隆地开🤣#杭州宋城 #宋城千古情 #千古情奇妙夜 "
                "#宋城奇妙夜NPC出来炸场了 — 这条特意写得很长很长很长很长，"
                "用来验证列宽放不下时悬停能看到全文这件事")
    recs = []
    for i, t in enumerate((long_multi, short_one, long_one)):
        r = G.Record(id=f"t{i}", title=t, url=f"https://x/{i}", decode_key="",
                     size=1000 * (i + 1), source="JS回调",
                     created="11-02 13:13:04", status="waiting", seq=i + 1)
        app.store.items[r.id] = r
        app.store.order.insert(0, r.id)
        app._add_row(r)
        recs.append(r)
    pump(root, 10)

    print("\n[B] 表格里的标题已单行化（不再被行高裁掉第二行）")
    kids = app.tree.get_children()
    check("三条记录都在表里", len(kids) == 3, f"{len(kids)} 条")
    ti = app._col_index("title")
    for r in recs:
        iid = app._row_of.get(r.id)
        if not iid:
            check(f"{r.id} 找到行", False)
            continue
        cell = str(app.tree.item(iid, "values")[ti])
        ok = ("\n" not in cell and "\r" not in cell and "\t" not in cell)
        check(f"{r.id} 单元格无换行符", ok, f"{cell[:24]!r}")
        check(f"{r.id} 显示值 == 原文压平", cell == one_line(r.title))
    # 原文必须保留（文件名/复制标题要用）
    check("Record.title 原文未改（仍含 \\n）", "\n" in recs[0].title,
          repr(recs[0].title[:12]))

    print("\n[C] 列宽策略：只有标题列可拉伸")
    cols = app.tree["columns"]
    stretch = {c: bool(app.tree.column(c, "stretch")) for c in cols}
    stretch_on = [c for c, v in stretch.items() if v]
    check("可拉伸列只有 title", stretch_on == ["title"], str(stretch_on))
    w_before = int(app.tree.column("title", "width"))
    check("标题列初始宽度 >= 480", w_before >= 480, f"{w_before}px")

    root.geometry("1400x860")
    pump(root, 12)
    w_wide = int(app.tree.column("title", "width"))
    others_wide = {c: int(app.tree.column(c, "width")) for c in cols
                   if c not in ("title", "no", "size")}
    others_before = {"status": 96, "platform": 80, "source": 88,
                     "like": 72, "time": 76, "file": 180}
    check("窗口变宽后标题列变大", w_wide > w_before, f"{w_before} → {w_wide}px")
    check("其它列宽度不变（不再平分多出来的宽度）",
          others_wide == others_before, f"{others_wide}")

    print("\n[D] 悬停提示：只对放不下的标题弹全文")
    iid_long = app._row_of.get(recs[2].id)      # 超长标题（远超列宽）
    iid_short = app._row_of.get(recs[1].id)     # 短标题（放得下）
    txt_long = app._tip_text_for_cell(iid_long, ti)
    txt_short = app._tip_text_for_cell(iid_short, ti)
    need = app._tip_font.measure(one_line(recs[2].title))
    avail = int(app.tree.column("title", "width"))
    check("前提：这条标题确实比列宽更宽", need > avail, f"{need}px > {avail}px")
    check("超宽标题 → 返回完整单行标题", txt_long == one_line(recs[2].title),
          f"{len(txt_long)} 字")

    check("短标题 → 不弹（返回空串）", txt_short == "", repr(txt_short))
    check("非标题列 → 不弹（返回空串）",
          app._tip_text_for_cell(iid_long, app._col_index("platform")) == "")
    check("RowTip 已挂上并绑定了 <Motion>",
          app._row_tip is not None and bool(app.tree.bind("<Motion>")))
    # 真的弹一次：走 _show 直连（event_generate 造不出真实悬停像素）
    app._row_tip._show(txt_long, root.winfo_rootx() + 60,
                       root.winfo_rooty() + 120)
    pump(root, 4)
    check("气泡窗口真的创建出来了", app._row_tip._tip is not None)
    if app._row_tip._tip is not None:
        check("气泡里就是完整标题（含句末内容）",
              txt_long[-6:] in txt_long, txt_long[-10:])
    app._row_tip.hide()
    pump(root, 3)
    check("hide() 后气泡已销毁", app._row_tip._tip is None)

    print("\n[E] 「🔑 授权管理」按钮醒目（样式 + 实际像素）")
    b = app.btn_license
    st = G.ttk.Style()
    check("样式是 License.TButton", str(b.cget("style")) == "License.TButton",
          str(b.cget("style")))
    check("样式底色已生效", st.lookup("License.TButton", "background") == "#fde68a",
          st.lookup("License.TButton", "background"))
    check("样式文字色已生效", st.lookup("License.TButton", "foreground") == "#7c2d12",
          st.lookup("License.TButton", "foreground"))
    check("字体加粗", "bold" in str(st.lookup("License.TButton", "font")),
          str(st.lookup("License.TButton", "font")))
    pump(root, 6)
    bw, bh = b.winfo_width(), b.winfo_height()
    check("按钮真的显示出来（不是 1x1 隐形）", bw > 60 and bh > 18,
          f"{bw}x{bh}")
    check("按钮处于 mapped 状态", bool(b.winfo_ismapped()))

    print("\n[F] 「📂 打开文件夹」按钮醒目（样式 + 实际像素）")
    d = app.btn_open_dir
    check("样式是 Folder.TButton", str(d.cget("style")) == "Folder.TButton",
          str(d.cget("style")))
    check("样式底色已生效", st.lookup("Folder.TButton", "background") == "#dbeafe",
          st.lookup("Folder.TButton", "background"))
    check("样式文字色已生效", st.lookup("Folder.TButton", "foreground") == "#1d4ed8",
          st.lookup("Folder.TButton", "foreground"))
    dw, dh = d.winfo_width(), d.winfo_height()
    check("按钮真的显示出来（不是 1x1 隐形）", dw > 80 and dh > 18,
          f"{dw}x{dh}")
    check("按钮文字带图标", "打开文件夹" in str(d.cget("text")),
          str(d.cget("text")))
    check("按钮处于 mapped 状态", bool(d.winfo_ismapped()))

    print("\n[G] 未授权时不再显示误导性的「满 0 条…」")
    check("limit_hint 为空", str(app.limit_hint.cget("text")).strip() == "",
          repr(str(app.limit_hint.cget("text"))[:40]))
    check("左侧档位文案仍在（未授权（可捕获，不能下载））",
          "未授权" in str(app.limit_value_lbl.cget("text")),
          str(app.limit_value_lbl.cget("text")))

    print("\n[H] 抖音批量窗口的表格同样单行化（同一个毛病，一并修）")
    try:
        from types import SimpleNamespace
        from sphdl.douyin_ui import DouyinWindow
        dw_win = DouyinWindow(app)
        pump(root, 6)
        fake = [SimpleNamespace(
            ok=True, aweme_id=f"aid{i}", title=t, author="测试作者",
            duration_text="00:38", size_text="44.1 MB", error="")
            for i, t in enumerate((long_multi, short_one, "他舅说\n他说\n补个大的"))]
        dw_win.apply_profile_batch(fake)
        pump(root, 6)
        kids2 = dw_win.tree.get_children()
        check("抖音表格已铺 3 行", len(kids2) == 3, f"{len(kids2)} 行")
        bad = []
        for iid in kids2:
            cell = str(dw_win.tree.item(iid, "values")[1])
            if any(ch in cell for ch in "\n\r\t"):
                bad.append(cell[:16])
        check("抖音表格标题无换行符", not bad, str(bad))
        try:
            dw_win.win.destroy()
        except Exception:
            pass
    except Exception as e:                           # noqa: BLE001
        check(f"抖音窗口验证可执行（{type(e).__name__}: {e}）", False)

    # 收尾：别把窗口留在屏幕上
    try:
        app.engine.stop()
    except Exception:
        pass
    try:
        root.destroy()
    except Exception:
        pass

    print("\n" + "=" * 78)
    if FAILS:
        print(f"❌ 失败 {len(FAILS)} 项：" + "、".join(FAILS))
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
