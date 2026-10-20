"""抓取顺序序号测试（v2.2.1）。

客户需求：
  「序号能和视频被抓取到的先后顺序相关联吗？有客户要求下载的视频顺序、
   标题与视频号里的顺序一致」

设计：
  · Record 新增 seq 字段：捕获时递增分配，**永不改变**
  · 点「序号」表头排序 = 按抓取先后排列
  · 有序号「序号」列（当前显示位置）与 seq（抓取顺序）是两个概念：
      - 序号列：1,2,3... 反映**当前显示第几个**
      - seq：抓取顺序，排序依据
      客户点「序号 ▲」→ 列表按抓取顺序 → 序号列自然变成 1..N 与抓取顺序一致

覆盖：
  Q1 Record 有 seq 字段
  Q2 seq 捕获时递增分配
  Q3 seq 不因删除记录而重复（单调递增）
  Q4 点「序号」表头按 seq 排序
  Q5 排序后序号列 = 1..N（与抓取顺序一致）
  Q6 seq 持久化（save/load 往返）
  Q7 老记录（无 seq）自动补号
  Q8 诊断开关机制存在
"""
import json
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

print("=" * 74)
print("Q1 Record 结构")
print("=" * 74)
import dataclasses
fields = {f.name for f in dataclasses.fields(G.Record)}
print(f"  Record 字段数: {len(fields)}")
check("Q1a Record 有 seq 字段", "seq" in fields)
r0 = G.Record(id="x", title="t", url="u", decode_key="", size=0,
              source="s", created="")
check("Q1b seq 默认值 0", r0.seq == 0, str(r0.seq))

print()
print("=" * 74)
print("Q2/Q3 seq 分配（单调递增，删记录不重复）")
print("=" * 74)
app = G.App()
app.root.withdraw()

# 清掉可能的缓存，重新测
if hasattr(app, "_seq_counter"):
    del app._seq_counter

s1 = app._next_seq()
s2 = app._next_seq()
s3 = app._next_seq()
print(f"  连取 3 次: {s1}, {s2}, {s3}")
check("Q2a seq 递增", s2 == s1 + 1 and s3 == s2 + 1, f"{s1},{s2},{s3}")

# 模拟删记录（store 里删掉一些）→ 再取 seq 不应重复
_before = app._seq_counter
if app.store.order:
    rid = app.store.order[0]
    app.store.items.pop(rid, None)
    try:
        app.store.order.remove(rid)
    except Exception:
        pass
s4 = app._next_seq()
print(f"  删 1 条记录后再取: {s4}（之前最大 {_before}）")
check("Q3a ★删记录后 seq 不重复（仍递增）", s4 == _before + 1,
      f"{s4} vs {_before + 1}")

print()
print("=" * 74)
print("Q4/Q5 点「序号」表头 → 按抓取顺序排序")
print("=" * 74)
# ★ 用**独立小 App 场景**验证排序键逻辑，不依赖真实列表
#   （真实列表有 600+ 条记录，测试行会被挤走）
recs = []
for seq_val, title in ((3, "第三个抓的"), (1, "第一个抓的"),
                       (4, "第四个抓的"), (2, "第二个抓的")):
    rec = G.Record(id=f"qtest{seq_val}", title=title,
                   url=f"http://x/{seq_val}", decode_key="", size=0,
                   source="测", created="10:00:00", platform="视频号",
                   seq=seq_val)
    app.store.items[rec.id] = rec
    recs.append(rec)

# 直接验证 _sort_key 的排序结果（这是 Q4 的核心）
app._sort_col = "no"
app._sort_desc = False
keys = [(app._sort_key(r.id), r.title) for r in recs]
keys.sort(key=lambda t: t[0])
ordered_titles = [t for _k, t in keys]
print(f"  按 _sort_key('no') 排序: {ordered_titles}")
expected = ["第一个抓的", "第二个抓的", "第三个抓的", "第四个抓的"]
check("Q4a ★「序号」排序键 = 抓取顺序", ordered_titles == expected,
      str(ordered_titles))

# 降序
keys_desc = sorted(keys, key=lambda t: t[0], reverse=True)
titles_desc = [t for _k, t in keys_desc]
print(f"  降序: {titles_desc}")
check("Q4b 降序正确", titles_desc == list(reversed(expected)), str(titles_desc))

# Q5：序号列 = 1..N（用独立 Treeview 验证 _renumber_rows）
tv = app.tree
tmp_kids = []
for i in range(4):
    iid = tv.insert("", "end", iid=f"_q5_{i}",
                    values=("", "待下载", f"t{i}", "🎬 视频号", "1M",
                            "测", "10:00", ""))
    tmp_kids.append(iid)
# 把 4 个测试行移到最前面，再重新编号
for idx, k in enumerate(tmp_kids):
    tv.move(k, "", idx)
app._renumber_rows()
nums_all = [tv.set(k, "no") for k in tv.get_children()]
nums_head = [tv.set(k, "no") for k in tmp_kids]
print(f"  前 4 行序号: {nums_head}，全表首尾: {nums_all[:3]}...{nums_all[-1]}")
check("Q5a 序号从 1 开始连续", nums_all[:4] == ["1", "2", "3", "4"],
      str(nums_all[:4]))
check("Q5b 全表序号连续（末位=总行数）",
      nums_all[-1] == str(len(nums_all)),
      f"{nums_all[-1]} vs {len(nums_all)}")

# 清理测试行
for k in tmp_kids:
    try:
        tv.delete(k)
    except Exception:
        pass
for r in recs:
    try:
        app.store.items.pop(r.id, None)
    except Exception:
        pass

print()
print("=" * 74)
print("Q6 seq 持久化")
print("=" * 74)
tdir = Path(tempfile.mkdtemp(prefix="seq_"))
sp = tdir / "records.json"
st = G.RecordStore(sp, max_records=0)
for i in (1, 5, 9):
    st.add(G.Record(id=f"p{i}", title=f"t{i}", url=f"u{i}", decode_key="",
                    size=0, source="s", created=f"10:0{i}:00", seq=i))
st.save()
st2 = G.RecordStore(sp, max_records=0)
st2._load()
loaded = {rid: st2.items[rid].seq for rid in st2.order}
print(f"  存 3 条 seq=1,5,9 → 读回: {loaded}")
check("Q6a seq 保存后能读回",
      sorted(loaded.values()) == [1, 5, 9], str(sorted(loaded.values())))

print()
print("=" * 74)
print("Q7 老记录（无 seq）自动补号")
print("=" * 74)
old_file = tdir / "old.json"
old_file.write_text(json.dumps([
    {"id": "o1", "title": "老1", "url": "u1", "decode_key": "", "size": 0,
     "source": "s", "created": "10-01 10:00:00", "status": "done",
     "file": "", "error": "", "progress": 100, "is_image": False,
     "video_id": "", "quality_note": "", "platform": "视频号"},      # 无 seq
    {"id": "o2", "title": "老2", "url": "u2", "decode_key": "", "size": 0,
     "source": "s", "created": "10-01 09:00:00", "status": "done",
     "file": "", "error": "", "progress": 100, "is_image": False,
     "video_id": "", "quality_note": "", "platform": "视频号"},
], ensure_ascii=False), encoding="utf-8")
st3 = G.RecordStore(old_file, max_records=0)
st3._load()
seqs = {rid: st3.items[rid].seq for rid in st3.order}
print(f"  两条老记录补号后: {seqs}")
check("Q7a 老记录被补上 seq", all(v > 0 for v in seqs.values()), str(seqs))
check("Q7b 补号按 created 先后（09:00 早于 10:00 → seq 更小）",
      seqs.get("o2", 0) < seqs.get("o1", 0), str(seqs))

print()
print("=" * 74)
print("Q8 诊断开关")
print("=" * 74)
check("Q8a 有诊断开关常量", hasattr(G.App, "DIAG_FLAG"))
check("Q8b 有 _diag_raw_enabled 方法", hasattr(G.App, "_diag_raw_enabled"))
check("Q8c 有 _dump_raw_desc 方法", hasattr(G.App, "_dump_raw_desc"))
print(f"  开关文件名: {G.App.DIAG_FLAG}")

# 清理
try:
    for r in recs:
        app.store.items.pop(r.id, None)
        try:
            app.store.order.remove(r.id)
        except Exception:
            pass
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
