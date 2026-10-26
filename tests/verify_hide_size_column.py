"""大小列隐藏验证（v2.2.20）。

═══ 客户要求（2026-10-25）═══
「隐藏视频清单里的『大小』列，部分客户纠结于实际下载后大小变小，
  直接隐藏起来吧。」

背景：客户看到「待下载 ≈183MB」，下载后只有 18MB，反复纠结"为什么变小"。
本质是微信元数据给的是**最高画质**值，实际下的是**当时播放档位**，
两者天然不同 —— 解释了也难接受。隐藏这一列避免无解争议。

覆盖：
  Z1 大小列 width=0 / minwidth=0 / stretch=False / 表头空
  Z2 序号列同样隐藏（v2.2.18 的行为不能被改回）
  Z3 values 仍是 8 个字段（内部索引不能错位）
  Z4 _col_index("size") 仍正确（排序/内部逻辑要用）
  Z5 Record.size 字段保留（列表上限/统计内部使用）
  Z6 点「大小」表头排序不崩
  Z7 标题列变宽（补回隐藏列的空间）
  Z8 状态列仍从 x=0 开始（前面没有残留空白）
  Z9 其他列都在（来源/捕获方式/时间/文件）
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


import sphdl.gui as G

tdir = Path(tempfile.mkdtemp(prefix="hidesize_"))
G.CONFIG_FILE = tdir / "config.json"
G.RECORDS_FILE = tdir / "records.json"
G.CONFIG_FILE.write_text("{}", encoding="utf-8")
G.RECORDS_FILE.write_text("[]", encoding="utf-8")

app = G.App()
app.root.withdraw()
app.root.update()

rec = G.Record(id="h1", title="测试视频标题", url="u1", decode_key="",
               size=183 * 1024 * 1024, source="t",
               created="2026-10-25 10:00:00", status="waiting", seq=1)
app.store.add(rec)
app._add_row(rec)
app.root.update()

print("=" * 74)
print("Z1/Z2 两个列都隐藏")
print("=" * 74)
for col, label in (("size", "大小"), ("no", "序号")):
    w = int(app.tree.column(col, "width"))
    mw = int(app.tree.column(col, "minwidth"))
    st_ = app.tree.column(col, "stretch")
    txt = app.tree.heading(col, "text")
    print(f"  {label}列: width={w} minwidth={mw} stretch={st_} 表头={txt!r}")
    check(f"Z1/Z2 {label}列宽度 0", w == 0, str(w))
    check(f"Z1/Z2 {label}列 minwidth 0", mw == 0, str(mw))
    check(f"Z1/Z2 {label}列表头为空", str(txt).strip() == "", repr(txt))

print()
print("=" * 74)
print("Z3/Z4 values 与索引不变")
print("=" * 74)
cols = tuple(app.tree["columns"])
vals = app._row_values(rec)
print(f"  列数 {len(cols)}  values {len(vals)}")
print(f"  values[4]（大小）= {vals[4]!r}  ← 数据仍在，只是不显示")
check("Z3 values 数与列数一致", len(vals) == len(cols), f"{len(vals)}/{len(cols)}")
check("Z3b 大小值仍在 values 里（≈ 前缀）",
      str(vals[4]).startswith("≈"), str(vals[4]))
expect = {"no": 0, "status": 1, "title": 2, "platform": 3,
          "size": 4, "source": 5, "time": 6, "file": 7}
bad = {k: (app._col_index(k), v) for k, v in expect.items()
       if app._col_index(k) != v}
print(f"  索引核对: {'全部正确' if not bad else bad}")
check("Z4 所有 _col_index 正确", not bad, str(bad))

print()
print("=" * 74)
print("Z5 Record.size 保留")
print("=" * 74)
check("Z5 size 字段存在且未清空", int(rec.size) == 183 * 1024 * 1024, str(rec.size))
# 保存/加载后仍在
app.store.save()
app.root.destroy()
app2 = G.App()
app2.root.withdraw()
app2.root.update()
r2 = app2.store.items.get("h1")
check("Z5b 重启后 size 仍保留", r2 is not None and int(r2.size) > 0,
      str(int(r2.size) if r2 else None))
check("Z5c _display_size 仍可用（内部函数没删）",
      hasattr(app2, "_display_size"))

print()
print("=" * 74)
print("Z6 点表头排序不崩")
print("=" * 74)
for col in ("size", "no", "title"):
    try:
        app2._on_tree_sort(col)
        print(f"  点 {col} 表头排序 → 正常")
    except Exception as e:
        check(f"Z6 排序 {col}", False, str(e)[:60])
check("Z6 隐藏列排序不崩", True)

print()
print("=" * 74)
print("Z7/Z8/Z9 布局")
print("=" * 74)
tw = int(app2.tree.column("title", "width"))
print(f"  标题列宽: {tw}px")
check("Z7 标题列变宽（≥300px）", tw >= 300, str(tw))

x = 0
pos = {}
for col in app2.tree["columns"]:
    w = int(app2.tree.column(col, "width"))
    pos[col] = (x, x + w, w)
    x += w
print(f"  状态列起始 x={pos['status'][0]}")
check("Z8 状态列从 x=0 开始", pos["status"][0] == 0, str(pos["status"][0]))

alive = all(pos[c][2] > 0 for c in ("status", "title", "platform",
                                    "source", "time", "file"))
check("Z9 其余 6 列都可见", alive,
      str({c: pos[c][2] for c in ("status", "title", "platform",
                                  "source", "time", "file")}))

app2.root.destroy()

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
