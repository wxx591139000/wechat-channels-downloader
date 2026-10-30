"""「抓不到视频？」弹窗只弹一次（v2.8.1）。

═══ 客户反馈（2026-10-30，剪贴板截图）═══
「这个弹窗不要反复弹，弹一个就行」
截图是「抓不到视频？」诊断窗（检测到视频号在活动，但没抓到视频）。

═══ 根因 ═══
`_start_hook_watchdog` 里用的是**180 秒冷却**：
```python
if time.time() - getattr(self, "_wd_last_warn", 0) < 180:
    return          # 3 分钟后又能弹
```
所以客户只要一直抓不到，就每 3 分钟被弹一次。

═══ 修正 ═══
① 改成「每个嗅探会话只弹一次」（`_wd_popup_shown`）。
   只有真的抓到视频了才重置 → 下次重新开嗅探、又出问题时能再提醒一次。
② 再保险：`_warn_capture_failed` 内部限制「同一诊断窗只存在一个」，
   存在就前置而不是再开一个（防止其它路径重复调用）。

覆盖：
P1  ★弹窗后的 3 分钟内不再弹（原逻辑会弹）
P2  ★模拟 30 分钟持续抓不到 → 只弹 1 次
P3  ★抓到视频后重置，下次出问题能再弹 1 次
P4  ★同一诊断窗已存在时不新建，改为前置
P5  25 秒启动宽限仍在（刚启动不打扰）
P6  引擎停止时不弹
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

print("=" * 74)
print("P1-P3 只弹一次（逻辑模拟）")
print("=" * 74)


class Sim:
    """复刻 _tick 里的弹窗判定。"""

    def __init__(self):
        self._wd_started = 0.0
        self._wd_popup_shown = False
        self.popups = 0

    def tick(self, now, engine_running=True, cap=0, fail=0, hits=0, age=5):
        if not engine_running:
            return
        if now - self._wd_started < 25:
            return
        # ★ 先判抓到、再判弹过（与生产代码同序）
        if cap > 0:
            self._wd_popup_shown = False
            return
        if self._wd_popup_shown:
            return
        if fail > 0 or (hits >= 3 and 0 <= age < 120):
            self._wd_popup_shown = True
            self.popups += 1


s1 = Sim()
# 从 t=0 开始，每 5 秒 tick 一次，持续 30 分钟，一直抓不到
for t in range(0, 1800, 5):
    s1.tick(t, cap=0, fail=0, hits=5, age=5)
print(f"  30 分钟持续抓不到 → 弹窗次数: {s1.popups}")
check("P1 ★弹窗后不会 3 分钟又弹", s1.popups == 1, str(s1.popups))
check("P2 ★30 分钟内只弹 1 次（原逻辑会弹约 10 次）",
      s1.popups == 1, f"原逻辑≈{1800//180} 次")

# 抓到视频后重置 → 再次出问题能弹一次
s2 = Sim()
s2.tick(100, cap=0, hits=5)      # 出问题 → 弹 1 次
n1 = s2.popups
s2.tick(200, cap=3, hits=5)      # 抓到了 → 重置 _wd_popup_shown
print(f"  抓到后 _wd_popup_shown = {s2._wd_popup_shown}（应 False）")
s2.tick(400, cap=0, hits=5)      # 又抓不到 → 再弹
n2 = s2.popups
print(f"  第一次弹 {n1} 次 → 抓到后重置 → 再出问题后累计 {n2} 次")
check("P3 ★抓到视频后可再提醒一次", n1 == 1 and n2 == 2, f"{n1} → {n2}")

# 25 秒宽限
s3 = Sim()
for t in range(0, 25, 5):
    s3.tick(t, cap=0, hits=5)
check("P5a 25 秒内不弹", s3.popups == 0, str(s3.popups))
s3.tick(30, cap=0, hits=5)
check("P5b 超过 25 秒才弹", s3.popups == 1, str(s3.popups))

# 引擎停止
s4 = Sim()
s4.tick(100, engine_running=False, cap=0, hits=5)
check("P6 引擎停止不弹", s4.popups == 0, str(s4.popups))

print()
print("=" * 74)
print("源码检查")
print("=" * 74)
import re as _re
def _func_src(src, name):
    i = src.find("def " + name)
    if i < 0:
        return ""
    m = _re.search(r"\n    def ", src[i + 10:])
    j = i + 10 + m.start() if m else len(src)
    return src[i:j]

seg = _func_src(gui, "_start_capture_watchdog")
print(f"  _start_capture_watchdog 片段 {len(seg)} 字符")

check("P1b 源码有 _wd_popup_shown 守卫", "_wd_popup_shown" in seg)
check("P1c ★不再用 180 秒冷却", "< 180" not in seg,
      "已移除")
check("P2b ★守卫在 cap 判定之后（先判抓到再判弹过）",
      seg.find("if cap > 0:") < seg.find("if self._wd_popup_shown:"),
      f"cap={seg.find('if cap > 0:')} guard={seg.find('if self._wd_popup_shown:')}")
check("P3b 抓到视频时重置", "self._wd_popup_shown = False" in seg)
check("P5c 25 秒宽限仍在", "< 25" in seg)

# 诊断窗去重
seg2 = _func_src(gui, "_warn_capture_failed")
print(f"  _warn_capture_failed 片段 {len(seg2)} 字符")
check("P4a ★记录窗口引用 _capture_fail_win", "_capture_fail_win" in seg2)
check("P4b ★存在则前置不新建",
      "deiconify()" in seg2 and "lift()" in seg2 and "return" in seg2)
check("P4c 去重检查在 Toplevel 创建之前",
      seg2.find("_capture_fail_win") < seg2.find("tk.Toplevel(self.root)"))

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
