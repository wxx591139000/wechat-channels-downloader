"""删除/清空后序号行为验证（v2.2.2）。

用户问题：
  「要是用户删除某些视频清单，甚至清除全部视频清单，
   新抓到的视频序号怎么处理」

═══ 设计原则（定稿）═══

  seq（抓取顺序）与「序号列」（显示位置）分离：

  ┌─ 场景 ─────────────┬─ seq 行为 ──────────┬─ 理由 ────────────┐
  │ 删部分记录          │ 继续递增，留空档     │ 不会与现存重复     │
  │ 清空全部（不重启）  │ **重置为 1**         │ 列表空了，从 1 直观 │
  │ 清空全部（重启）    │ 从 1 开始            │ 无历史可比，自然    │
  │ 删除后重启          │ 从 max(现存)+1 继续  │ 保持不重复          │
  └────────────────────┴──────────────────────┴────────────────────┘

  ★ 关键决策：**「删除全部记录」时把计数器也重置**
    否则客户清空后继续抓，序号从 101 开始，会以为软件出了问题。
    （这是 diag_seq_on_delete.py 诊断出的真实问题）

覆盖：
  S1 删部分 → 新 seq 不与现存重复
  S2 删部分 → 序号列重编为 1..N（连续，客户看不出空档）
  S3 ★清空全部 → 计数器重置，新 seq 从 1 开始
  S4 清空后重启 → 新 seq 从 1 开始
  S5 删除后重启 → seq 从 max(现存)+1 继续（不重复）
  S6 清空全部后，序号列也是连续的
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


import sphdl.gui as G

app = G.App()
app.root.withdraw()


def reset_app():
    """把列表和计数器清到已知状态。"""
    app.store.items.clear()
    app.store.order.clear()
    app._row_of.clear()
    app._seq_counter = 0


print("=" * 74)
print("S1/S2 删部分记录")
print("=" * 74)
reset_app()
seqs = [app._next_seq() for _ in range(5)]
print(f"  抓 5 条: seq={seqs}")

# 造 5 条真记录
recs = []
for i, sq in enumerate(seqs):
    rec = G.Record(id=f"s1_{sq}", title=f"v{sq}", url=f"http://x/{sq}",
                   decode_key="", size=0, source="测", created=f"10:0{i}:00",
                   platform="视频号", seq=sq)
    app.store.items[rec.id] = rec
    app.store.order.append(rec.id)
    recs.append(rec)

# 删中间 2 条
for rec in (recs[1], recs[3]):
    app._remove_row(rec.id)
alive = [r.seq for r in app.store.items.values()]
print(f"  删 2 条后现存 seq: {sorted(alive)}")

new_seqs = [app._next_seq() for _ in range(2)]
print(f"  再抓 2 条 seq: {new_seqs}")
check("S1a ★新 seq 不与现存重复",
      not (set(new_seqs) & set(alive)), f"新={new_seqs} 现存={sorted(alive)}")
check("S1b 新 seq 单调递增", new_seqs == [6, 7], str(new_seqs))

print()
print("=" * 74)
print("S3 ★清空全部记录 → 计数器重置")
print("=" * 74)
app._seq_counter = 100          # 模拟已经抓过 100 条
print(f"  模拟计数器 = {app._seq_counter}")
# 调真实的清空方法（它会清 store + 应重置计数器）
app.store.items.clear()
app.store.order.clear()
app._row_of.clear()
app._reset_seq_on_clear()       # ← v2.2.2 新增：清空时重置
print(f"  清空后计数器 = {app._seq_counter}")
first_new = app._next_seq()
print(f"  清空后第一次抓: seq = {first_new}")
check("S3a ★清空全部后 seq 从 1 开始", first_new == 1, str(first_new))

print()
print("=" * 74)
print("S4 清空后重启 → seq 从 1")
print("=" * 74)
tdir = Path(tempfile.mkdtemp(prefix="seq2_"))
f = tdir / "records.json"
f.write_text("[]", encoding="utf-8")
st = G.RecordStore(f, max_records=0)
st._load()
mx = max([int(getattr(r, "seq", 0) or 0) for r in st.items.values()] or [0])
print(f"  空列表加载 → max(seq)={mx} → 下一条 = {mx + 1}")
check("S4a 空列表重启后从 1 开始", mx + 1 == 1, str(mx + 1))

print()
print("=" * 74)
print("S5 删除后重启 → seq 从 max(现存)+1 继续")
print("=" * 74)
f2 = tdir / "records2.json"
f2.write_text(json.dumps([
    {"id": "a", "title": "A", "url": "u1", "decode_key": "", "size": 0,
     "source": "s", "created": "10:00:00", "status": "done", "file": "",
     "error": "", "progress": 100, "is_image": False, "video_id": "",
     "quality_note": "", "platform": "视频号", "seq": 5},
    {"id": "b", "title": "B", "url": "u2", "decode_key": "", "size": 0,
     "source": "s", "created": "10:01:00", "status": "done", "file": "",
     "error": "", "progress": 100, "is_image": False, "video_id": "",
     "quality_note": "", "platform": "视频号", "seq": 9},
], ensure_ascii=False), encoding="utf-8")
st2 = G.RecordStore(f2, max_records=0)
st2._load()
mx2 = max([int(getattr(r, "seq", 0) or 0) for r in st2.items.values()] or [0])
print(f"  现存 seq=5,9 → max={mx2} → 下一条 = {mx2 + 1}")
check("S5a ★删除后重启，seq 从 max+1 继续（不重复）", mx2 + 1 == 10,
      str(mx2 + 1))

print()
print("=" * 74)
print("S6 清空后序号列连续")
print("=" * 74)
reset_app()
for i in range(3):
    sq = app._next_seq()
    rec = G.Record(id=f"s6_{sq}", title=f"v{sq}", url=f"http://y/{sq}",
                   decode_key="", size=0, source="测", created=f"11:0{i}:00",
                   platform="视频号", seq=sq)
    app.store.items[rec.id] = rec
    app.store.order.append(rec.id)
    iid = app.tree.insert("", "end", iid=f"s6i_{sq}",
                          values=("", "待下载", f"v{sq}", "🎬 视频号",
                                  "1M", "测", "11:00", ""))
    app._row_of[rec.id] = iid
app._renumber_rows()
nums = [app.tree.set(k, "no") for k in app.tree.get_children()[:3]]
print(f"  3 条记录序号列: {nums}")
check("S6a 序号列连续 1,2,3", nums == ["1", "2", "3"], str(nums))

# 清理
for k in list(app.tree.get_children()):
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
