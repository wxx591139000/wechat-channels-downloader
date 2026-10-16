#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量拉取驱动状态机测试（v2.0.18 修复）。

背景：2026-10-16 用户报告"抓到 3500+ 后清单不动"，日志显示
  批量 371s前·新增3858条 | &f=0 ×122 | batch 响应全为 0
根因：视频号窗口失焦 → 页面拿不到滚动授权 → 驱动停摆；
     且原 JS 的 `done` 一旦置位，只有收到 0（未授权）才复位，
     有焦点时永远 return，无法自动续跑。

本测试在 Python 中**等价重建**注入 JS 的状态机（逐行对照 addon.py 的
_BRIDGE_JS_TEMPLATE），验证修复后的行为契约：
  B1 有焦点 + 未到底 → 持续滚动
  B2 失焦（响应 0）→ 不滚动，但**不清 done**（关键修复）
  B3 到底后收到 grew 信号 → 自动续跑（关键修复）
  B4 到底后超过 20s 宽限 → 自动再试一轮（绝不永久停摆，关键修复）
  B5 失焦不应把 stable 累积成"到底"（防误判）
  B6 到底判定放宽至 stable>=20（50s）
  B7 失焦期间 rounds/stable 被复位，但 done 保持

跑法：python tests/verify_batch_driver.py
"""
import sys
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "sphdl" / "addon.py"

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


# ---------- 1. 从源码提取真实 JS，校验修复点存在 ----------
src = ADDON.read_text(encoding="utf-8", errors="replace")

print("=" * 74)
print("J1 源码级校验：修复点必须真实存在于注入 JS 中")
print("=" * 74)
check("J1a 存在 resetProgress()（失焦只清进度）",
      "function resetProgress() { rounds = 0; stable = 0; }" in src)
check("J1b 失焦分支调用 resetProgress 而非 resetState",
      re.search(r"if \(!on\) \{\s*//[^\n]*\n(?:[^\n]*\n)*?\s*resetProgress\(\);", src) is not None)
check("J1c 存在 grew 解析（续跑信号）",
      'var grew = !!(t && t.indexOf("r") > 0);' in src)
check("J1d done 分支有自动续跑（grew || waited > 20000）",
      re.search(r"if \(grew \|\| waited > 20000\)", src) is not None)
check("J1e 到底判定放宽为 stable >= 20",
      "stable >= 20" in src and "stable >= 12" not in src)
check("J1f done 记录时间戳 doneAt",
      "doneAt = Date.now();" in src)
check("J1g 上报 h=scrollHeight 便于诊断",
      '+ "&h=" + maxH' in src)

# 代理侧：grew 信号实现
print("\n" + "=" * 74)
print("J2 代理侧：续跑信号 grew 实现校验")
print("=" * 74)
check("J2a 代理下发 payload 带 r 后缀",
      'payload = ("1" if chosen else "0") + ("r" if grew else "")' in src)
check("J2b _batch_seen_count 已初始化",
      "self._batch_seen_count = 0" in src)
check("J2c grew 判定基于 capture_count 增长",
      "self.capture_count > self._batch_seen_count" in src)
check("J2d 仅对被选中页面下发 grew",
      re.search(r"if chosen:\s*\n\s*grew = self\.capture_count", src) is not None)

# GUI 侧：批量启动重置基线
print("\n" + "=" * 74)
print("J3 GUI 侧：批量启动时重置续跑基线")
print("=" * 74)
gui = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8", errors="replace")
check("J3a 批量开关处重置 _batch_seen_count",
      "self.addon._batch_seen_count = self.addon.capture_count" in gui)
check("J3b 状态栏有失焦可操作提示",
      "点一下视频号窗口" in gui)


# ---------- 2. Python 等价状态机 ----------
class Driver:
    """逐行对照注入 JS 的状态机（v2.0.18）。"""

    def __init__(self):
        self.rounds = 0
        self.stable = 0
        self.maxH = 0
        self.done = False
        self.doneAt = 0
        self.t = 0            # 模拟时钟(ms)
        self.scrolls = 0      # 实际滚动次数
        self.batch_done_posts = 0

    def resetProgress(self):
        self.rounds = 0
        self.stable = 0

    def tick(self, resp, scroll_h, at_bottom=True):
        """resp: '0' / '1' / '1r'；scroll_h: 本次滚动后高度。"""
        on = bool(resp) and resp[0] == "1"
        grew = "r" in resp[1:]
        if not on:
            self.resetProgress()
            return
        if self.done:
            waited = self.t - self.doneAt
            if grew or waited > 20000:
                self.done = False
                self.doneAt = 0
                self.stable = 0
                self.rounds = 0
            else:
                return
        self.rounds += 1
        self.scrolls += 1
        if scroll_h > self.maxH + 10:
            self.maxH = scroll_h
            self.stable = 0
        elif scroll_h > 0 and at_bottom:
            self.stable += 1
        else:
            self.stable = 0
        if self.rounds > 240 or self.stable >= 20:
            self.done = True
            self.doneAt = self.t
            self.batch_done_posts += 1


print("\n" + "=" * 74)
print("B1 有焦点 + 未到底 → 持续滚动")
print("=" * 74)
d = Driver()
for i in range(10):
    d.t += 2500
    d.tick("1", 1000 + i * 500)      # 高度持续增长
check("B1a 滚动 10 次", d.scrolls == 10, f"scrolls={d.scrolls}")
check("B1b 未判到底", not d.done)

print("\n" + "=" * 74)
print("B2 ★ 失焦（响应 0）→ 不滚动，但不清 done")
print("=" * 74)
d = Driver()
d.t = 2500
d.tick("1", 1000)
d.stable = 15
d.done = True
d.doneAt = d.t
before_done, before_stable = d.done, d.stable
for _ in range(6):
    d.t += 2500
    d.tick("0", 0)                    # 失焦
check("B2a 失焦期间不滚动", d.scrolls == 1, f"scrolls={d.scrolls}")
check("B2b ★ done 保持（不清）", d.done is True, f"done={d.done}")
check("B2c 进度被复位", d.stable == 0 and d.rounds == 0,
      f"stable={d.stable} rounds={d.rounds}")

print("\n" + "=" * 74)
print("B3 ★ 到底后收到 grew → 自动续跑")
print("=" * 74)
d = Driver()
d.done = True
d.doneAt = 0
d.t = 2500                            # 仅过了 2.5s，远超宽限之外
d.tick("1r", 5000)                    # 有新捕获
check("B3a ★ 自动续跑（done 复位）", d.done is False, f"done={d.done}")
check("B3b ★ 恢复了滚动", d.scrolls == 1, f"scrolls={d.scrolls}")

print("\n" + "=" * 74)
print("B4 ★ 到底后仅过 2.5s 且无 grew → 不空转为滚动（保守）")
print("=" * 74)
d = Driver()
d.done = True
d.doneAt = 0
d.t = 2500
d.tick("1", 5000)
check("B4a 未续跑（宽限内且无新数据）", d.done is True and d.scrolls == 0,
      f"done={d.done} scrolls={d.scrolls}")

print("\n" + "=" * 74)
print("B5 ★ 到底后超过 20s 宽限 → 自动再试一轮（绝不永久停摆）")
print("=" * 74)
d = Driver()
d.done = True
d.doneAt = 0
d.t = 25001                           # 超过 20s
d.tick("1", 5000)
check("B5a ★ 宽限后自动重试", d.done is False and d.scrolls == 1,
      f"done={d.done} scrolls={d.scrolls}")

print("\n" + "=" * 74)
print("B6 失焦不会误判『到底』")
print("=" * 74)
d = Driver()
for i in range(40):
    d.t += 2500
    d.tick("0", 0)                    # 全程失焦
check("B6a 全程失焦 → 永不判到底", d.done is False, f"done={d.done}")
check("B6b 全程失焦 → 零滚动", d.scrolls == 0, f"scrolls={d.scrolls}")

print("=" * 74)
print("B7 到底判定阈值 = 50s（stable>=20 @2.5s）")
print("=" * 74)
d = Driver()
# 第 1 次 tick 因 maxH=0 → 1000 > 10 走「高度增长」分支，不计 stable。
# 故需要 1 + 20 次 tick 才让 stable 达到 20。
d.t += 2500
d.tick("1", 1000, at_bottom=True)          # maxH 首次记录，stable 仍 0
for i in range(19):
    d.t += 2500
    d.tick("1", 1000, at_bottom=True)
check("B7a stable=19 时仍未到底", not d.done, f"stable={d.stable}")
d.t += 2500
d.tick("1", 1000, at_bottom=True)
check("B7b stable=20 时判到底", d.done is True, f"stable={d.stable}")
check("B7c 到底耗时约 50s（20×2.5s）", d.t - 2500 == 50000,
      f"elapsed={d.t - 2500}ms")

print()
ok = sum(1 for _, p, _ in RESULTS if p)
print(f"RESULT: {'ALL PASS' if ok == len(RESULTS) else 'FAIL'} ({ok}/{len(RESULTS)})")
for n, p, _ in RESULTS:
    if not p:
        print("  未通过: " + n)
sys.exit(0 if ok == len(RESULTS) else 1)
