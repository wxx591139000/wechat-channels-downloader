"""评论串视频修复验证（v2.2.16）。

═══ 我自己验证真实数据时发现的 bug（2026-10-25）═══

拿客户机器上已抓到的真实 records.json 做体检，发现：

```
跨视频重复的 cid 数: 72
  14781306192264562803 → ['夜已深…', '日本东京都…', '第一次世界大战全过程']
```

**同一批评论被挂到了 3 个不同视频上。**

根因：评论挂在「最后捕获的视频」（`_cur_video_key`），
而用户实际会在多个视频间来回切，评论 payload 到达时
"最后捕获的视频" 很可能不是评论真正所属的那个。

修法（三重）：
  1. `VideoData.nonce_id` / `Record.nonce_id`：保存视频的 objectNonceId
  2. `addon._collect_video_ids()`：从评论 payload 里挖出视频标识
  3. `addon._cid_owner`：**全局兜底** —— 一个 cid 只能属于一个视频，
     已归属的绝不重复挂到别处（不依赖能否认出 ID）

覆盖：
  O1 payload 带 objectId → 评论挂到对应视频（不是最后捕获那个）
  O2 payload 带 objectNonceId → 同样能匹配
  O3 ★看不出 ID 时，同一 cid 不会挂到第二个视频（全局兜底）
  O4 同视频重复投递仍按原逻辑去重
  O5 nonce_id 能持久化（_load 白名单）
  O6 无 ID 且无当前视频 → 不挂（不丢到随机记录上）
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


print("=" * 74)
print("O1/O2 payload 自带视频 ID 时，评论归属正确")
print("=" * 74)
from sphdl.addon import SphAddon

# 模拟两份记录：vidA / vidB
RECORDS = {"vidA": "nonceA", "vidB": "nonceB"}
got = []


def resolve_video_by_ids(ids):
    """复现 gui._resolve_video_by_ids。"""
    for vid, non in RECORDS.items():
        if vid in ids:
            return vid
        if non in ids:
            return vid
    return None


a = SphAddon(None)
a.on_comments = lambda k, cs, t: got.append((k, [c.get("content") for c in cs]))
a.resolve_video_by_ids = resolve_video_by_ids
a.comments_enabled = True
# 故意把"当前视频"设成 vidA（模拟用户正在看 A）
a.set_current_video("vidA")

# 但评论 payload 明确说这是 vidB 的评论
payload_b = json.dumps({
    "data": {
        "objectId": "vidB",
        "commentList": [
            {"nickname": "甲", "content": "B的评论1", "likeCount": 1},
            {"nickname": "乙", "content": "B的评论2", "likeCount": 2},
        ],
    }
}, ensure_ascii=False).encode()

a._handle_comment_payload(payload_b)
print(f"  当前视频=vidA，payload 带 objectId=vidB")
print(f"  回调: {got}")
check("O1 挂到了 vidB（不是当前视频 vidA）",
      len(got) == 1 and got[0][0] == "vidB",
      str(got[0][0]) if got else "无回调")

# 用 nonce 匹配
payload_nonce = json.dumps({
    "objectNonceId": "nonceA",
    "commentList": [{"nickname": "丙", "content": "A的评论", "likeCount": 0}],
}, ensure_ascii=False).encode()
a._handle_comment_payload(payload_nonce)
print(f"  payload 带 objectNonceId=nonceA → {got[-1] if got else None}")
check("O2 nonce 也能匹配到 vidA",
      len(got) == 2 and got[1][0] == "vidA",
      str(got[-1][0]) if len(got) >= 2 else "无")

print()
print("=" * 74)
print("O3 ★看不出 ID 时的全局兜底（最重要）")
print("=" * 74)
b = SphAddon(None)
got2 = []
b.on_comments = lambda k, cs, t: got2.append((k, [c.get("content") for c in cs]))
b.resolve_video_by_ids = lambda ids: None      # 模拟认不出 ID
b.comments_enabled = True

same_comment = {"nickname": "同名用户", "content": "同一条评论", "likeCount": 5}
payload_noid = json.dumps(
    {"commentList": [same_comment]}, ensure_ascii=False).encode()

# 先在看 A 的时候收到
b.set_current_video("vidA")
b._handle_comment_payload(payload_noid)
# 然后切到 B，又收到同一条（真实事故就是这样串的）
b.set_current_video("vidB")
b._handle_comment_payload(payload_noid)

print(f"  回调记录: {got2}")
check("O3 同一 cid 只挂到第一个视频",
      len(got2) == 1 and got2[0][0] == "vidA", str(got2))
check("O3b 第二个视频没收到重复评论",
      all(k != "vidB" for k, _ in got2), str([k for k, _ in got2]))

print()
print("=" * 74)
print("O4 同视频重复投递仍去重")
print("=" * 74)
n_before = len(got2)
b._handle_comment_payload(payload_noid)      # 还是 vidB
b.set_current_video("vidA")
b._handle_comment_payload(payload_noid)      # 回到 vidA 再投一次
print(f"  再投两次后回调数: {len(got2)}（应仍为 {n_before}）")
check("O4 重复投递不重复追加", len(got2) == n_before, str(len(got2)))

print()
print("=" * 74)
print("O5 nonce_id 持久化")
print("=" * 74)
import sphdl.gui as G

tdir = Path(tempfile.mkdtemp(prefix="nonce_"))
G.CONFIG_FILE = tdir / "config.json"
G.RECORDS_FILE = tdir / "records.json"
G.CONFIG_FILE.write_text("{}", encoding="utf-8")
G.RECORDS_FILE.write_text(json.dumps([
    {"id": "n1", "title": "视频1", "url": "u1", "decode_key": "", "size": 1,
     "source": "t", "created": "2026-10-25 10:00:00", "status": "waiting",
     "video_id": "vidX", "nonce_id": "nonceX", "seq": 1},
], ensure_ascii=False), encoding="utf-8")

app = G.App()
app.root.withdraw()
app.root.update()
rec = app.store.items.get("n1")
print(f"  加载后 nonce_id = {getattr(rec, 'nonce_id', 'MISSING')!r}")
check("O5 nonce_id 从 records.json 读回",
      getattr(rec, "nonce_id", "") == "nonceX",
      str(getattr(rec, "nonce_id", None)))

# GUI 的匹配方法
key = app._resolve_video_by_ids({"nonceX"})
print(f"  _resolve_video_by_ids({{'nonceX'}}) = {key!r}")
check("O5b GUI 能用 nonce 反查到视频键", key == "vidX", str(key))

key2 = app._resolve_video_by_ids({"garbage", "vidX"})
check("O5c 也能用 video_id 反查", key2 == "vidX", str(key2))

key3 = app._resolve_video_by_ids({"unknown"})
check("O5d 认不出时返回 None", key3 is None, str(key3))

print()
print("=" * 74)
print("O6 无 ID 且无当前视频 → 不挂")
print("=" * 74)
c = SphAddon(None)
got3 = []
c.on_comments = lambda k, cs, t: got3.append((k, cs))
c.resolve_video_by_ids = lambda ids: None
c.comments_enabled = True
c._handle_comment_payload(payload_noid)      # 没有 _cur_video_key
print(f"  回调数: {len(got3)}（应为 0）")
check("O6 没有归属对象时不乱挂", len(got3) == 0, str(len(got3)))

app.root.destroy()

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
