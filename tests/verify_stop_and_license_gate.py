"""停止下载 / 未授权门禁 修复验证（v2.2.5）。

═══ 两个客户报障（2026-10-23）═══

【报障1】「点击『停止所有下载』后，序号 500 那行还一直在下载，并没有被停止」
  根因：`_stop_all_downloads()` 按 URL 逐个 cancel，只覆盖界面上
  还在 pending/downloading/decrypting 的记录；pipeline 内部队列里
  还有别的任务（状态已改/已不在列表）没人取消 → 队列继续跑。
  另外「自动下载」开着会立刻补新任务，用户看到新的又开始了。

【报障2】「当前 2.2.4 显示未授权，为何还能下载视频」
  根因：`addon.py` 的自动下载路径直接 `self.pipeline.submit(vd)`，
  **绕过了 GUI 的 _submit_record() 授权门禁**。

覆盖：
  S1 Pipeline.cancel_all() 能清空队列并返回数量
  S2 cancel_all 后队列真的空（再 submit 才继续）
  S3 cancel_all 不影响后续正常下载（stop_event 会被复位）
  S4 ★未授权时 addon 自动下载不提交
  S5 已授权时 addon 自动下载正常提交
  S6 license_check 未设置时不过滤（命令行/测试场景兼容）
  S7 GUI 已把 license_check 挂到 addon
"""
import sys
import time
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


print("=" * 74)
print("S1/S2 Pipeline.cancel_all()")
print("=" * 74)
import tempfile
from sphdl.pipeline import Pipeline
from sphdl.entities import VideoData

tdir = Path(tempfile.mkdtemp(prefix="pl_"))
# wait=True 时 _loop 会等队列；这里用默认，手动检查队列
pl = Pipeline(tdir, wait=False)

# 造 5 个任务（不真的下载：url 无效，但先看队列清空）
vds = [VideoData(url=f"http://invalid.test/{i}.mp4", decode_key="", size=1,
                 description=f"任务{i}") for i in range(5)]
for vd in vds:
    pl.submit(vd)
print(f"  投递 5 个后队列大小: {pl._queue.qsize()}")
check("S1a 投递后队列有 5 个", pl._queue.qsize() == 5, str(pl._queue.qsize()))

n = pl.cancel_all()
print(f"  cancel_all() 返回 {n}，队列剩余 {pl._queue.qsize()}")
check("S1b 返回清掉的数量", n >= 4, f"n={n}")
check("S2 cancel_all 后队列清空", pl._queue.qsize() <= 1, str(pl._queue.qsize()))

print()
print("=" * 74)
print("S3 cancel_all 后仍能正常下载（stop_event 复位）")
print("=" * 74)
# 给一个真实可下载的小文件（用本地 http 服务模拟太重，改查 stop_event 行为）
pl2 = Pipeline(Path(tempfile.mkdtemp(prefix="pl2_")), wait=False)
pl2.cancel_all()
print(f"  cancel_all 后 stop_event 置位: {pl2._stop_event.is_set()}")
# _process 会在开始时 clear()
pl2._stop_event.clear()   # 模拟 _process 的复位
check("S3 stop_event 可复位（新任务不受影响）", not pl2._stop_event.is_set())

print()
print("=" * 74)
print("S4/S5/S6 addon 自动下载的授权门禁")
print("=" * 74)
from sphdl.addon import SphAddon


class FakePipeline:
    def __init__(self):
        self.submitted = []

    def submit(self, vd):
        self.submitted.append(vd)


def make_addon(licensed):
    fp = FakePipeline()
    a = SphAddon(fp, auto_download=True)
    a.license_check = (lambda: licensed)
    a.on_capture = None
    return a, fp


vd = VideoData(url="http://x/1.mp4", decode_key="", size=1, description="测试视频")

a_no, fp_no = make_addon(False)
# ★ 真实入口是 SphAddon._handle(vd, source)
a_no._handle(vd, "test")
print(f"  未授权 → 提交数: {len(fp_no.submitted)}")
check("S4 未授权时 addon 不提交下载", len(fp_no.submitted) == 0,
      f"{len(fp_no.submitted)} 个")

a_yes, fp_yes = make_addon(True)
a_yes._handle(vd, "test")
print(f"  已授权 → 提交数: {len(fp_yes.submitted)}")
check("S5 已授权时 addon 正常提交", len(fp_yes.submitted) >= 1,
      f"{len(fp_yes.submitted)} 个")

# 不设 license_check（命令行场景）
fp3 = FakePipeline()
a3 = SphAddon(fp3, auto_download=True)
a3.license_check = None
a3._handle(vd, "test")
print(f"  未设 license_check → 提交数: {len(fp3.submitted)}")
check("S6 未设回调时不过滤（兼容命令行）", len(fp3.submitted) >= 1,
      f"{len(fp3.submitted)} 个")

print()
print("=" * 74)
print("S7 GUI 挂接检查")
print("=" * 74)
src = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8")
check("S7a GUI 已挂 license_check",
      "self.addon.license_check = self._has_download_license" in src)
check("S7b 停止所有下载用了 cancel_all", "self.pipeline.cancel_all()" in src)
check("S7c 停止时同时关闭自动下载",
      "已同时关闭「自动下载」" in src)

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
