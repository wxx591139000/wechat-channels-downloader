"""序号列 + 列偏移 + 反选说明测试（v2.2.0）。

客户需求：
  「界面的视频清单加"序号"列，有些用户希望下载固定数量的视频，
   有了序号他可以根据序号来按需选中合适数量」

用户追加要求：
  「"反选"按钮的说明也加到多选技巧的说明里」

覆盖：
  N1 首列是「序号」
  N2 序号按显示顺序连续编号（1,2,3...）
  N3 排序后序号重新编号（关键：客户要看"当前第几个"）
  N4 ★列偏移修复（加序号列后「来源」列从 #3 变 #4，双击筛选不能失效）
  N5 提示条包含「反选」说明
  N6 序号列宽度合理（能显示 3 位数）
  N7 _row_values 与列数对齐（多了序号列不能错位）
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


import sphdl.gui as G

print("=" * 74)
print("构造 App")
print("=" * 74)
app = G.App()
app.root.withdraw()
print("  App 已构造")

print()
print("=" * 74)
print("N1 序号列位置")
print("=" * 74)
cols = list(app.tree["columns"])
print(f"  列: {cols}")
check("N1a 首列是 no（序号）", cols[0] == "no", str(cols[0]))
check("N1b 列数 = 8（原 7 + 序号）", len(cols) == 8, str(len(cols)))
head_no = app.tree.heading("no", "text")
print(f"  序号列表头文字: {head_no}")
check("N1c 表头文字是「序号」", head_no == "序号", head_no)

print()
print("=" * 74)
print("N2/N3 序号编号与排序后重编")
print("=" * 74)
# 插入 5 个测试行
kids = []
for i in range(5):
    # ★ 必须给满 8 个值（含序号占位），否则整体错位
    iid = app.tree.insert("", "end", iid=f"_no_{i}",
                          values=("", "待下载", f"标题{i}", "🎬 视频号",
                                  "1M", "测", "10:00", f"f{i}.mp4"))
    kids.append(iid)

app._renumber_rows()
nums = [int(app.tree.set(k, "no")) for k in kids]
print(f"  插入后序号: {nums}")
# ★ 列表里可能有真实记录，序号不一定从 1 开始，
#   但必须是**连续递增**的（这才是客户要的"第几个"）
check("N2a 序号连续递增", all(nums[i+1] - nums[i] == 1 for i in range(len(nums)-1)),
      str(nums))
total_rows = len(app.tree.get_children())
print(f"  列表总行数: {total_rows}，最后一行序号应为 {total_rows}")
last = app.tree.get_children()[-1]
check("N2b 最后一行序号 == 总行数（编号完整）",
      app.tree.set(last, "no") == str(total_rows),
      f"{app.tree.set(last,'no')} vs {total_rows}")

# 模拟排序：把顺序反转（tree.move）
for idx, k in enumerate(reversed(kids)):
    app.tree.move(k, "", idx)
app._renumber_rows()
nums2 = [app.tree.set(k, "no") for k in app.tree.get_children()[:5]]
print(f"  反转顺序后序号: {nums2}")
check("N3a ★排序/移动后序号重新连续编号",
      nums2 == ["1", "2", "3", "4", "5"], str(nums2))

# 验证序号确实跟着"位置"而不是跟着"行"
first_after = app.tree.get_children()[0]
check("N3b 首行序号=1（跟位置走，不跟行 id 走）",
      app.tree.set(first_after, "no") == "1",
      f"首行={first_after} 序号={app.tree.set(first_after,'no')}")

print()
print("=" * 74)
print("N4 ★列偏移修复（加序号列后双击筛选不能失效）")
print("=" * 74)
pi = app._col_index("platform")
print(f"  platform 列索引: {pi}（应为 3，因为 no=0,status=1,title=2,platform=3）")
check("N4a platform 索引 = 3", pi == 3, str(pi))

si = app._col_index("status")
check("N4b status 索引 = 1", si == 1, str(si))
check("N4c _col_index 对不存在的列返回 -1",
      app._col_index("nonexistent") == -1)

# 验证 vals 取值位置正确（用真实行）
vals = app.tree.item(kids[0], "values")
print(f"  测试行 values: {vals}")
print(f"  vals[{pi}] = {vals[pi] if len(vals) > pi else 'N/A'}")
check("N4d vals[platform索引] 取到来源值",
      len(vals) > pi and "视频号" in str(vals[pi]),
      str(vals[pi]) if len(vals) > pi else "索引越界")

print()
print("=" * 74)
print("N5 提示条含「反选」说明")
print("=" * 74)
txt = app.lbl_hint.cget("text")
print(f"  提示条内容：\n  {txt}")
check("N5a 含「反选」", "反选" in txt)
check("N5b 解释了反选含义", "没选的变选中" in txt or "变选中" in txt)
check("N5c 仍含 Ctrl 说明", "Ctrl" in txt)
check("N5d 仍含 Shift 说明", "Shift" in txt)
check("N5e 仍含全选说明", "全选" in txt)

print()
print("=" * 74)
print("N6/N7 列宽与行值对齐")
print("=" * 74)
w = app.tree.column("no", "width")
print(f"  序号列宽: {w}")
check("N6a 序号列宽 >= 45（能显示 3 位数）", int(w) >= 45, str(w))

# 用真实记录测 _row_values 长度
if app.store.items:
    rid = next(iter(app.store.items))
    rv = app._row_values(app.store.items[rid])
    print(f"  _row_values 返回 {len(rv)} 个值，列数 {len(cols)}")
    check("N7a values 个数 == 列数", len(rv) == len(cols),
          f"{len(rv)} vs {len(cols)}")
    check("N7b 第一个值是空（序号待填）", rv[0] == "", repr(rv[0]))

# 清理
for k in kids:
    try:
        app.tree.delete(k)
    except Exception:
        pass

try:
    app.root.destroy()
except Exception:
    pass

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
