"""直播回放整页捕获验证（v2.6.6）—— 用客户真实抓包做固件。

═══ 客户需求（2026-10-29）═══
博主「林萃心屿」的「直播回放」栏目有几百个回放，
人工点进每个都无法翻到下一个，希望工具能自动挨个抓取。

═══ 已确认事实（来自客户真实抓包，不是推测）═══
· 回放列表**带完整 object_desc**，每条 media[0].url 可直接下载
· 每页 8 条回放，时长 6751~25054 秒（1.8~7 小时），file_size 2~3 GB
· 老逻辑只抓「前 2 条」→ 245 条回放要滑 122 次

═══ 为什么用真实 payload 而不是伪造对象 ═══
第一版我用 dict 手搓 object_desc → `extract_from_object_desc` 只认了 1 条、
标题还是空的。伪造数据测的是"我猜的结构"，真实数据才测得出行为。
（这和 P21/P22 两次「测试假绿」同一个教训。）

覆盖：
  L1  真实一页 8 条回放 → 8 条全入列（老逻辑只有 2）
  L2  把时长改成 30 秒（模拟短视频）→ 仍只抓 2 条
  L3  混合页（2 短 + 6 长）→ 全部入列
  L4  单页上限 LONG_PER_PAGE_CAP 生效
  L5  重复 payload 不重复入列
  L6  判据是 media[0].video_play_len，阈值 600 秒
"""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


# ══════════ 载入客户真实抓包固件 ══════════
FIX = ROOT / "tests" / "fixtures" / "replay_payload.json"
if not FIX.exists():
    print(f"❌ 缺少固件 {FIX}")
    sys.exit(1)

payload = json.loads(FIX.read_text(encoding="utf-8"))
print(f"固件: {FIX.name}  {FIX.stat().st_size} 字节")
print(f"  顶层 keys: {list(payload.keys())}")

from sphdl import addon as A
from sphdl.parser import iter_object_desc_entries, extract_from_object_desc

data = payload
objs = data.get("object") or []
print(f"  object 条目: {len(objs)}    feeds_count: {data.get('feeds_count')}")
durs = [((o.get('object_desc') or {}).get('media') or [{}])[0].get('video_play_len')
        for o in objs]
print(f"  各条时长: {durs}")

# 用真实 payload 走真实迭代器
entries = []
for od, entry in iter_object_desc_entries(data):
    entries.append((od, entry))
print(f"  迭代器解出 (desc, entry) 对: {len(entries)}")

odes = [od for od, _ in entries]
emap = {id(od): e for od, e in entries}


class Rec:
    """记录 _handle 收到的 vd，并模拟 seen 去重。"""

    def __init__(self):
        self.ANCHOR = 2
        self.LOOKAHEAD = 2
        self.PENDING_CAP = 200
        self.LONG_VIDEO_SEC = 600
        self.LONG_PER_PAGE_CAP = 60
        self.seen = set()
        self.captured = []

    def _handle(self, vd, source=""):
        key = getattr(vd, "video_id", None) or (vd.url, vd.decode_key)
        self.seen.add(key)
        self.captured.append(vd)

    def run(self, odes_, emap_):
        A.SphAddon._capture_anchor(self, odes_, "test", emap_)
        return self.captured


print()
print("=" * 74)
print("L1 真实一页 8 条回放 → 全抓")
print("=" * 74)
r1 = Rec()
got = r1.run(odes, emap)
print(f"  入列条数: {len(got)}")
for v in got[:4]:
    print(f"    - {str(getattr(v, 'title', ''))[:34]}  "
          f"{getattr(v, 'size', 0)/1048576:.0f}MB")
check("L1a ★8 条回放全部入列（老逻辑只 2 条）", len(got) == 8, f"{len(got)}/8")
check("L1b 确实不是老行为的 2 条", len(got) != 2, str(len(got)))
check("L1c 每条都有下载地址",
      all(getattr(v, "url", "") for v in got), "全有 url")
check("L1d 每条都有解码密钥",
      all(getattr(v, "decode_key", None) is not None for v in got),
      "decode_key 齐")

print()
print("=" * 74)
print("L2 同样 8 条但时长改 30 秒（模拟短视频）→ 仍只抓 2 条")
print("=" * 74)
short = copy.deepcopy(data)
for o in short.get("object") or []:
    m = (o.get("object_desc") or {}).get("media") or []
    if m:
        m[0]["video_play_len"] = 30
entries_s = list(iter_object_desc_entries(short))
odes_s = [od for od, _ in entries_s]
emap_s = {id(od): e for od, e in entries_s}
r2 = Rec()
got2 = r2.run(odes_s, emap_s)
print(f"  入列条数: {len(got2)}")
check("L2 ★短视频仍只抓 ANCHOR 条（2）", len(got2) == 2, f"{len(got2)}")

print()
print("=" * 74)
print("L3 混合页：前 2 条改短视频 + 后 6 条保持长视频")
print("=" * 74)
mix = copy.deepcopy(data)
for i, o in enumerate(mix.get("object") or []):
    if i < 2:
        m = (o.get("object_desc") or {}).get("media") or []
        if m:
            m[0]["video_play_len"] = 45
entries_m = list(iter_object_desc_entries(mix))
odes_m = [od for od, _ in entries_m]
emap_m = {id(od): e for od, e in entries_m}
r3 = Rec()
got3 = r3.run(odes_m, emap_m)
print(f"  入列条数: {len(got3)}")
check("L3 混合页 8 条全入列", len(got3) == 8, f"{len(got3)}")

print()
print("=" * 74)
print("L4 单页上限防灌爆（100 条长视频）")
print("=" * 74)
many = copy.deepcopy(data)
big = []
for i in range(100):
    o = copy.deepcopy((data.get("object") or [])[0])
    od = o.get("object_desc") or {}
    m = od.get("media") or [{}]
    if m:
        m[0]["video_play_len"] = 4000
        m[0]["url"] = m[0].get("url", "").replace("encfilekey=", f"encfilekey=K{i}")
    o["id"] = f"MANY{i}"
    o["object_nonce_id"] = f"nonce_MANY{i}"
    big.append(o)
many["object"] = big
entries_x = list(iter_object_desc_entries(many))
odes_x = [od for od, _ in entries_x]
emap_x = {id(od): e for od, e in entries_x}
r4 = Rec()
got4 = r4.run(odes_x, emap_x)
print(f"  {len(odes_x)} 条长视频 → 入列 {len(got4)}")
check("L4 上限生效", len(got4) <= 2 + r4.LONG_PER_PAGE_CAP,
      f"{len(got4)} ≤ {2 + r4.LONG_PER_PAGE_CAP}")

print()
print("=" * 74)
print("L5 重复 payload 不重复入列")
print("=" * 74)
r5 = Rec()
a = len(r5.run(odes, emap))
b = len(r5.run(odes, emap))
print(f"  第一次 {a} 条，第二次累计 {b} 条")
check("L5 ★重复 payload 零新增", a == b == 8, f"{a} → {b}")

print()
print("=" * 74)
print("L6 判据与阈值")
print("=" * 74)
src = (ROOT / "sphdl" / "addon.py").read_text(encoding="utf-8")
check("L6a 用 media[0].video_play_len 判定", "video_play_len" in src)
check("L6b 阈值 600 秒", "LONG_VIDEO_SEC = 600" in src)
check("L6c 单页上限 60", "LONG_PER_PAGE_CAP = 60" in src)
check("L6d 长视频捕获有独立日志",
      "长视频整页捕获" in src)

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
