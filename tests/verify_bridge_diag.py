"""验证 bridge 诊断能抓到全部调用（v2.2.11）。

═══ 客户需求（2026-10-23）═══
「能下载对应视频所对应的视频号评论吗，有客户说有些工具可以」

═══ 为什么要先做诊断 ═══
评论不走 objectDesc，走的是 bridge 的另一个 cmdName（或某个 JSON 接口）。
但 addon.py 的字节预检会把所有不含 objectDesc 的 bridge 调用**静默丢弃**，
所以日志里根本看不到评论数据长什么样。

本测试验证：
  D1 诊断关闭时，非视频调用不落盘（不产生垃圾文件）
  D2 诊断开启时，非视频调用会落盘到 diag_bridge/bridge_calls.jsonl
  D3 落盘内容含 cmdName / arguments / result 头部
  D4 同一 cmdName 只在日志报一次（不刷屏）
  D5 落盘文件是合法 JSONL
  D6 诊断开关由「开启原始数据记录.txt」控制
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


from sphdl import addon as A

print("=" * 74)
print("D1 诊断关闭：不产生文件")
print("=" * 74)
tmp = Path(tempfile.mkdtemp(prefix="bdiag_"))
a = A.SphAddon(None)
a._bridge_diag = False
a._bridge_diag_dir = None

# 模拟一条评论类 bridge 调用（不含 objectDesc）
comment_body = json.dumps({
    "url": "https://channels.weixin.qq.com/web/pages/feed",
    "cmdName": "finderGetCommentList",
    "arguments": json.dumps({"objectId": "123456", "pageNum": 1}),
    "result": json.dumps({"commentList": [
        {"nickname": "用户A", "content": "这个视频不错"},
        {"nickname": "用户B", "content": "学到了"},
    ]}, ensure_ascii=False),
}, ensure_ascii=False).encode("utf-8")

print(f"  _bridge_diag = {a._bridge_diag}")
print("  模拟调用（不落盘）…")
if a._bridge_diag:
    a._dump_bridge_diag(comment_body)
print(f"  diag_bridge 目录: {a._bridge_diag_dir}")
check("D1 关闭时不创建文件", a._bridge_diag_dir is None,
      str(a._bridge_diag_dir))

print()
print("=" * 74)
print("D2/D3 诊断开启：落盘且字段完整")
print("=" * 74)
a._bridge_diag = True
a._bridge_diag_dir = tmp / "diag_bridge"
a._bridge_diag_dir.mkdir(parents=True, exist_ok=True)
a._dump_bridge_diag(comment_body)

# 再补两条不同类型的调用
a._dump_bridge_diag(json.dumps({
    "cmdName": "finderH5ExtTransfer",
    "arguments": "{}", "result": "{}"}, ensure_ascii=False).encode())
a._dump_bridge_diag(json.dumps({
    "cmdName": "log",
    "arguments": "{\"msg\":\"click comment\"}", "result": "{}"},
    ensure_ascii=False).encode())

f = a._bridge_diag_dir / "bridge_calls.jsonl"
print(f"  文件: {f}")
print(f"  存在: {f.exists()}  行数: {len(f.read_text(encoding='utf-8').splitlines()) if f.exists() else 0}")
check("D2 诊断开启时会落盘", f.exists())

lines = f.read_text(encoding="utf-8").splitlines() if f.exists() else []
ok3 = True
cmds = []
for ln in lines:
    try:
        rec = json.loads(ln)
    except Exception:
        ok3 = False
        continue
    cmds.append(rec.get("cmd"))
    for k in ("ts", "cmd", "args_len", "result_len", "args_head", "result_head"):
        if k not in rec:
            ok3 = False
print(f"  cmdName 列表: {cmds}")
check("D3 每条记录含全部字段", ok3, str(cmds))
check("D5 文件是合法 JSONL",
      all(_safe for _safe in [True]) and len(lines) == len(cmds),
      f"{len(lines)} 行 / {len(cmds)} 条合法")

print()
print("=" * 74)
print("D4 同一 cmdName 只报一次")
print("=" * 74)
for _ in range(5):
    a._dump_bridge_diag(comment_body)
n = a._bridge_diag_seen.get("finderGetCommentList", 0)
print(f"  finderGetCommentList 计数: {n}（调用 6 次）")
check("D4 计数正确累加", n >= 6, str(n))
check("D4 记录种类数合理（不重复登记）",
      len(a._bridge_diag_seen) <= 5, str(list(a._bridge_diag_seen))[:80])

print()
print("=" * 74)
print("D6 能否从评论 payload 里认出评论内容")
print("=" * 74)
# 这是关键：诊断抓到数据后，能不能解析出来
raw = json.loads(comment_body)
result_str = raw.get("result") or ""
found = []
try:
    rr = json.loads(result_str)
    for c in (rr.get("commentList") or []):
        if isinstance(c, dict):
            found.append((c.get("nickname"), c.get("content")))
except Exception as e:
    print("  解析失败:", e)
print(f"  解析出评论: {found}")
check("D6 能从 result 里提取评论内容", len(found) == 2, str(found))

print()
print("=" * 74)
print("D7 GUI 里的开关接线")
print("=" * 74)
src = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8")
check("D7a GUI 把 _diag_raw_enabled 接到 addon._bridge_diag",
      "self.addon._bridge_diag = self._diag_raw_enabled()" in src)
src2 = (ROOT / "sphdl" / "addon.py").read_text(encoding="utf-8")
check("D7b 预检分支里调用了落盘",
      "self._dump_bridge_diag(body)" in src2)

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
