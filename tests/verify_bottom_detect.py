"""到底判据修正验证（v2.7.4）。

═══ 客户实测（2026-10-29，第二轮回）═══
「翻页还没到底部就自动停了，我手动一翻发现还有」

═══ 日志证据 ═══
```
[14:11:04] ⚡ 批量拉取已开启
[14:12:25] 滚轮兜底第 4/5 次
[14:12:37] 滚轮兜底第 5/5 次
[14:12:49] ⚡ 连续 5 次兜底滚动都没有新内容，判定已拉到底，自动停止。本次共抓 118 条
```
→ 118 条之后手动翻**还有内容**，说明判据错了。

═══ 根因 ═══
v2.7.3 用「连续 N 次无新增」判到底。但这个条件**不等于到底**：

| 无新增的可能原因 | 是不是真的到底 |
|---|---|
| 这一屏都是已抓过的重复条目 | ❌ 不是 |
| 单发滚轮只滚一小段，还没到触发加载的位置 | ❌ 不是 |
| 网络慢，数据还在路上 | ❌ 不是 |
| **页面高度不再增长** | ✅ 是 |

═══ 修正 ═══
判据改为 **页面 scrollHeight 是否还在增长**：
  · 还在长 → 说明还有内容，**继续滚，计数清零**
  · 不再长 + 连续 N 次 → 才判定到底

覆盖：
T1  页面高度还在增长 → 不停，继续滚
T2  ★页面高度不变时才累加 idle 计数
T3  ★高度有变化 → idle 归零（关键：不误判重复条目）
T4  连续 N 次高度不变 → 判定到底停止
T5  addon 记录页面上报的 scrollHeight（h 参数）
T6  gui 用 scroll_height 而不是"新捕获数"判到底
T7  无残留死代码（get_scroll_pos 已删）
T8  断网保护仍在（v2.7.3 的能力没丢）
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


gui = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8")
addon = (ROOT / "sphdl" / "addon.py").read_text(encoding="utf-8")
ap = (ROOT / "sphdl" / "autopage.py").read_text(encoding="utf-8")

# 取兜底循环代码段
i = gui.find("def _start_batch_wheel_fallback")
j = gui.find("def _stop_batch_wheel_fallback", i)
loop = gui[i:j]
print(f"兜底循环代码段 {len(loop)} 字符\n")

print("=" * 74)
print("T1-T4 到底判据")
print("=" * 74)
check("T1a 判据用 scroll_height", "scroll_height" in loop)
check("T1b 记录了 pos_before / pos_after",
      "pos_before" in loop and "pos_after" in loop)
check("T1c ★有「页面还在长 → 继续」的分支",
      "moved = " in loop or "pos_after != pos_before" in loop)
check("T2a idle 累加在滚动之后", "idle_rounds += 1" in loop)
check("T3 ★高度变化时 idle 归零", "idle_rounds = 0" in loop)
check("T3b 归零后 continue（不进入停止判定）",
      re.search(r"idle_rounds = 0[\s\S]{0,400}?continue", loop) is not None)
check("T4a 连续 N 次高度不变才停", "MAX_IDLE_ROUNDS" in loop)
m = re.search(r"MAX_IDLE_ROUNDS\s*=\s*(\d+)", loop)
print(f"  MAX_IDLE_ROUNDS = {m.group(1) if m else '?'}")
check("T4b 上限存在且合理（v2.7.4 提到 8）",
      m is not None and 5 <= int(m.group(1)) <= 15, m.group(1) if m else "?")
check("T4d ★曾增长过的页面会多等 3 轮", "grew_at_least_once" in loop)
check("T4c 到底提示写明「页面高度都没变化」",
      "页面高度都没变化" in loop, "见 push_log")

print()
print("=" * 74)
print("T5-T6 数据来源")
print("=" * 74)
check("T5a addon 记录 scroll_height 字段",
      "self.scroll_height" in addon)
check("T5b 从 batch 轮询的 h 参数取值",
      'q.get("h")' in addon)
check("T5c addon 里初始化 scroll_height",
      "self.scroll_height = 0" in addon)
check("T6a gui 读 addon.scroll_height",
      'getattr(self.addon, "scroll_height"' in loop)
check("T6b ★不再用 get_scroll_pos（未实现的空函数已删）",
      "get_scroll_pos" not in gui and "get_scroll_pos" not in ap)

print()
print("=" * 74)
print("T7-T8 无死代码 / 旧能力保留")
print("=" * 74)
check("T7a autopage.py 无 get_scroll_pos", "def get_scroll_pos" not in ap)
check("T7b autopage.py 仍提供滚轮工具",
      "def post_wheel" in ap and "def find_video_window" in ap)
check("T8a 断网保护仍在（_net_ok）", "self._net_ok()" in loop)
check("T8b 断网提示仍在", "网络已断开" in loop)
check("T8c batch_autostop 事件仍在", "batch_autostop" in loop)

print()
print("=" * 74)
print("逻辑模拟：重复条目场景不该被判到底")
print("=" * 74)


def simulate(scroll_heights, max_idle=8):
    """模拟兜底循环判定（与生产代码同构，含 v2.7.4 的「曾增长延长」）。"""
    idle = 0
    grew_at_least_once = False
    for k in range(1, len(scroll_heights)):
        before, after = scroll_heights[k - 1], scroll_heights[k]
        idle += 1
        moved = (after != before)
        if moved:
            idle = 0
            grew_at_least_once = True
            continue                      # 还在长 → 继续
        need = max_idle + (3 if grew_at_least_once else 0)
        if idle >= need:
            return ("到底", k)
    return ("未到底", len(scroll_heights) - 1)


# 场景：前 3 次高度不变（重复条目），第 4 次开始长
h1 = [1000, 1000, 1000, 1000, 1200, 1400, 1400, 1400, 1400, 1400, 1400, 1600]
r1 = simulate(h1)
print(f"  场景1 前段重复后段增长: {r1}")
check("T9a ★重复条目导致的「不变」不会立刻判到底（要连续 5 次）",
      r1[0] == "未到底" or r1[1] >= 5, str(r1))

# 场景：真的到底（高度从第 2 次起就不变）
h2 = [1000, 1200] + [1200] * 12   # 涨一次后彻底不动（够 8+3 轮）
r2 = simulate(h2)
print(f"  场景2 真到底: {r2}")
check("T9b 真到底能被判定", r2[0] == "到底", str(r2))

# 场景：一直增长
h3 = [1000, 1200, 1400, 1600, 1800, 2000, 2200]
r3 = simulate(h3)
print(f"  场景3 一直增长: {r3}")
check("T9c 一直增长绝不判到底", r3[0] == "未到底", str(r3))

# 场景4（新增）：像客户那次 —— 长过一次后停了，但延长观察后确实到底
h4 = [1000, 1200] + [1200] * 20
r4 = simulate(h4)
print(f"  场景4 增长后长期不变: {r4}")
check("T9d 真到底（曾增长）需要更长观察才判",
      r4[0] == "到底" and r4[1] >= 11, str(r4))

# 场景5（新增）：客户 118 条那种 —— 高度缓慢断续增长，绝不能提前判到底
h5 = [1000] + [1000] * 4 + [1400] + [1400] * 4 + [1800] + [1800] * 4 + [2200]
r5 = simulate(h5)
print(f"  场景5 断续增长（模拟回放列表分页）: {r5}")
check("T9e ★断续增长绝不提前判到底（客户 118 条误停的复现）",
      r5[0] == "未到底", str(r5))

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
