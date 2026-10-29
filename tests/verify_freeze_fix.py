"""界面卡死修复验证（v2.7.5）。

═══ 客户实测（2026-10-29）═══
「2.7.4 这会卡死了」

═══ 日志证据 ═══
```
最近 3000 行日志里 DIM: 2294 (76%)   ← 每个 HTTP 请求一行
[18:43:47] ✅ 批量拉取完成：已滚动到列表末尾（本次新增 185 条）  ← JS 已判完
[18:43:49] 页面高度未变（2/11）…（一直刷到 18:44:37 才停）      ← 兜底白滚
```
两个问题：
① **DIM 日志洪水**：GUI 订阅了所有日志，每个网络请求一行 → Text 控件
   每条 insert + 行数统计 + see() 都要走 Tk 事件循环 → 批量拉取时卡死
② **重复劳动**：JS 18:43:47 已判定到底，GUI 兜底不知道，又白滚 11 次
   （每次 6 秒 + 8 次 push_log）

覆盖：
K1  ★dim 级别只落盘、不推给 GUI（根治洪水）
K2  ★GUI 模式默认关闭 debug（main.py 不再强制 True）
K3  有显式开启方式（SPHDL_DEBUG 或 开启调试日志.txt）
K4  ★addon 在 JS 判完成时打 batch_done_at 时间戳
K5  ★兜底看到 batch_done_at 立即收手（不再白滚）
K6  过程日志节流（只在 1/2/4/8/16 轮打）
K7  info/ok/warn/error 仍会推给 GUI（不能把正常日志也吞了）
K8  落盘不受影响（dim 仍写进日志文件，排查时能看）
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


lg = (ROOT / "sphdl" / "logger.py").read_text(encoding="utf-8")
gui = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8")
addon = (ROOT / "sphdl" / "addon.py").read_text(encoding="utf-8")
main = (ROOT / "main.py").read_text(encoding="utf-8")

print("=" * 74)
print("K1 dim 日志不再推给 GUI")
print("=" * 74)
i = lg.find("def _emit")
j = lg.find("def info", i)
emit = lg[i:j]
print(f"  _emit 代码段 {len(emit)} 字符")
check("K1a ★_emit 里有 dim 提前 return",
      'if level == "dim":' in emit and "return" in emit)
# 位置必须在 subscribers 循环之前
pos_dim = emit.find('if level == "dim":')
pos_sub = emit.find("for fn in list(self._subscribers)")
print(f"  dim 判断位置 {pos_dim} / 订阅循环位置 {pos_sub}")
check("K1b ★dim return 在订阅循环之前（否则照样推给 GUI）",
      pos_dim > 0 and pos_sub > pos_dim, f"{pos_dim} < {pos_sub}")
check("K8a dim 仍写文件（_write_file 在 return 之前）",
      emit.find("_write_file") < pos_dim,
      f"write={emit.find('_write_file')} dim={pos_dim}")
check("K7 info 仍走 _emit", 'def info(self, msg):\n        self._emit("info", msg)' in lg)
check("K7b ok/warn/error 仍走 _emit",
      'self._emit("ok"' in lg and 'self._emit("warn"' in lg
      and 'self._emit("error"' in lg)

print()
print("=" * 74)
print("K2-K3 GUI 模式调试日志")
print("=" * 74)
check("K2a ★main.py 不再无条件 debug_enabled = True",
      "logger.debug_enabled = True  # GUI 模式：全流量日志" not in main)
check("K2b 改为按开关判断", "SPHDL_DEBUG" in main)
check("K3a 支持环境变量 SPHDL_DEBUG", 'environ.get("SPHDL_DEBUG"' in main)
check("K3b 支持文件开关 开启调试日志.txt", "开启调试日志.txt" in main)

print()
print("=" * 74)
print("K4-K5 JS 完成后兜底收手")
print("=" * 74)
check("K4a addon 有 batch_done_at 字段",
      "self.batch_done_at = 0.0" in addon)
check("K4b ★batch_done 时打时间戳",
      "self.batch_done_at = time.time()" in addon)
k = gui.find("def _start_batch_wheel_fallback")
k2 = gui.find("def _stop_batch_wheel_fallback", k)
loop = gui[k:k2]
check("K5a ★兜底检查 batch_done_at",
      'getattr(self.addon, "batch_done_at", 0)' in loop)
check("K5b ★检查在滚动之前（不能先滚再判）",
      loop.find("batch_done_at") < loop.find("post_wheel(hwnd"),
      f"check={loop.find('batch_done_at')} scroll={loop.find('post_wheel(hwnd')}")
check("K5c 收手时给明确提示", "页面已报告滚动到底" in loop)

print()
print("=" * 74)
print("K6 过程日志节流")
print("=" * 74)
check("K6a 只在特定轮次打日志",
      "idle_rounds in (1, 2, 4, 8, 16)" in loop)

print()
print("=" * 74)
print("K9 实跑：dim 不推给订阅者、但落盘")
print("=" * 74)
import subprocess
code = r'''
import sys, tempfile
sys.path.insert(0, r"E:/myHermesWorkSpace/wechat-channels-downloader/sphdl")
from pathlib import Path
from sphdl.logger import Logger, logger

got = []
logger.subscribe(lambda lv, m: got.append((lv, m)))
logger.debug_enabled = True

tmp = Path(tempfile.mkdtemp())
Logger._log_path = tmp / "t.log" if hasattr(Logger, "_log_path") else None

logger.info("INFO-测试")
logger.debug("DIM-测试")
logger.warn("WARN-测试")
logger.ok("OK-测试")

lv = [x[0] for x in got]
print("收到级别:", lv)
print("DIM_PUSHED:", "dim" in lv)
print("INFO_PUSHED:", "info" in lv)
'''
r = subprocess.run(["E:/myHermesWorkSpace/wechat-channels-downloader/.venv/Scripts/python.exe",
                    "-c", code], cwd=str(ROOT), capture_output=True,
                   text=True, timeout=90)
out = r.stdout or r.stderr[:400]
print("  " + out.strip().replace("\n", "\n  "))
check("K9a ★dim 没有被推给订阅者", "DIM_PUSHED: False" in out, out.strip()[:80])
check("K9b info 仍被推送", "INFO_PUSHED: True" in out)

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
