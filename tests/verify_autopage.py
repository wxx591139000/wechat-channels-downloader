"""自动翻页抓取验证（v2.7.0）。

═══ 客户需求（2026-10-29）═══
「林萃心屿」直播回放几百个，一页 8 条，v2.6.6 已能整页全抓，
但仍要人工滑 31 次 → 要「自动翻页」。

═══ 设计取舍 ═══
不注入 JS 调分页接口（参数含签名+游标，接口一变就废），
而是**模拟真实滚轮**让页面自己加载下一页 —— 接口怎么变都不影响。

═══ 覆盖 ═══
P1  滚轮事件真的发出（SendInput 结构正确）
P2  正常翻页：有新增时一直翻，累计条数正确
P3  ★到底自动停：连续 idle_rounds 轮无新增 → 停止，原因正确
P4  ★达到翻页上限 → 停止
P5  ★找不到窗口 → 立即停并给可照做的提示（不空转）
P6  ★用户 stop() 能中断（不用等 max_pages）
P7  on_done 回调带 pages/added/reason，且不含内部字段（_last_count）
P8  光标位置还原（不把用户鼠标留在窗口中心）
P9  UI：✕ 是隐藏不是销毁；窗口非 transient
P10 UI：ap_log/ap_prog/autopage_done 三个事件都接上了
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


from sphdl import autopage as AP

print("=" * 74)
print("P1 滚轮事件结构")
print("=" * 74)
check("P1a WHEEL_DELTA = 120", AP.WHEEL_DELTA == 120, str(AP.WHEEL_DELTA))
check("P1b MOUSEEVENTF_WHEEL = 0x800",
      AP.MOUSEEVENTF_WHEEL == 0x0800, hex(AP.MOUSEEVENTF_WHEEL))
size = AP.ctypes.sizeof(AP.INPUT)
print(f"  INPUT 结构大小: {size} 字节（64 位应为 40）")
check("P1c INPUT 结构大小合理", size in (28, 40), str(size))
# 实际发一次滚轮（不依赖窗口，验证不抛异常）
try:
    AP.send_wheel(-AP.WHEEL_DELTA)
    ok = True
except Exception as e:
    ok = False
    print(f"  异常: {e}")
check("P1d send_wheel 可调用不报错", ok)

print()
print("=" * 74)
print("P2 正常翻页（有新增一直翻）")
print("=" * 74)
state = {"n": 0, "scrolls": 0}


def scroll_ok():
    state["scrolls"] += 1
    state["n"] += 8       # 每页新增 8 条


p = AP.AutoPager(max_pages=5, interval=0.05, idle_rounds=3,
                 scroll_fn=scroll_ok, count_fn=lambda: state["n"])
p.start()
p.join(timeout=20)
print(f"  翻页 {p.result['pages']}  新增 {p.result['added']}  原因 {p.result['reason']}")
check("P2a 翻满 5 页", p.result["pages"] == 5, str(p.result["pages"]))
check("P2b 累计新增 40", p.result["added"] == 40, str(p.result["added"]))
check("P2c 停止原因=翻页上限", "上限" in p.result["reason"], p.result["reason"])

print()
print("=" * 74)
print("P3 ★到底自动停（连续无新增）")
print("=" * 74)
state3 = {"n": 16, "scrolls": 0}


def scroll3():
    # 前 2 次新增，之后到底
    state3["scrolls"] += 1
    if state3["scrolls"] <= 2:
        state3["n"] += 8


p3 = AP.AutoPager(max_pages=50, interval=0.05, idle_rounds=3,
                  scroll_fn=scroll3, count_fn=lambda: state3["n"])
p3.start()
p3.join(timeout=25)
print(f"  翻页 {p3.result['pages']}  新增 {p3.result['added']}  原因 {p3.result['reason']}")
check("P3a ★提前停止（没跑满 50 页）", p3.result["pages"] < 50, str(p3.result["pages"]))
check("P3b 停止原因=到底", "到底" in p3.result["reason"], p3.result["reason"])
check("P3c 翻页数 = 2 + idle_rounds", p3.result["pages"] == 5,
      str(p3.result["pages"]))

print()
print("=" * 74)
print("P4 达到翻页上限")
print("=" * 74)
state4 = {"n": 0}


def scroll4():
    state4["n"] += 1


p4 = AP.AutoPager(max_pages=3, interval=0.05, idle_rounds=99,
                  scroll_fn=scroll4, count_fn=lambda: state4["n"])
p4.start()
p4.join(timeout=20)
check("P4 3 页后停", p4.result["pages"] == 3, str(p4.result["pages"]))

print()
print("=" * 74)
print("P5 ★找不到窗口 → 立即停 + 可照做的提示")
print("=" * 74)
logs5 = []


def scroll5():
    raise RuntimeError("no window")     # 模拟 _do_scroll 返回 False 用下面方式


# 用 find_fn 返回 None 触发
p5 = AP.AutoPager(max_pages=10, interval=0.05,
                  scroll_fn=None, count_fn=lambda: 0,
                  find_fn=lambda: (None, None),
                  on_log=lambda m, lv="info": logs5.append((m, lv)))
p5.start()
p5.join(timeout=20)
print(f"  翻页 {p5.result['pages']}  原因 {p5.result['reason']}")
for m, lv in logs5:
    print(f"    [{lv}] {m[:70]}")
check("P5a 立即停止（0 页）", p5.result["pages"] == 0, str(p5.result["pages"]))
check("P5b 原因=找不到窗口", "找不到" in p5.result["reason"], p5.result["reason"])
check("P5c 提示告诉用户怎么办",
      any("打开" in m and "视频号" in m for m, _ in logs5), str(len(logs5)))

print()
print("=" * 74)
print("P6 ★用户 stop() 能中断")
print("=" * 74)
state6 = {"n": 0}


def scroll6():
    state6["n"] += 1


p6 = AP.AutoPager(max_pages=200, interval=0.4, idle_rounds=99,
                  scroll_fn=scroll6, count_fn=lambda: state6["n"])
p6.start()
time.sleep(0.6)
p6.stop()
p6.join(timeout=15)
print(f"  翻页 {p6.result['pages']}  原因 {p6.result['reason']}")
check("P6a 中断后线程结束", not p6.is_alive())
check("P6b 远小于 200 页", p6.result["pages"] < 10, str(p6.result["pages"]))
check("P6c 原因=用户停止", "用户停止" in p6.result["reason"], p6.result["reason"])

print()
print("=" * 74)
print("P7 on_done 回调")
print("=" * 74)
done = {}


def _done(res):
    done.update(res)


p7 = AP.AutoPager(max_pages=2, interval=0.05, idle_rounds=99,
                  scroll_fn=lambda: None,
                  count_fn=lambda: 8 * 2,
                  on_done=_done)
p7.start()
p7.join(timeout=15)
print(f"  on_done 收到: {done}")
check("P7a 收到 pages/added/reason",
      all(k in done for k in ("pages", "added", "reason")), str(list(done)))
check("P7b ★不含内部字段 _last_count",
      not any(k.startswith("_") for k in done), str(list(done)))

print()
print("=" * 74)
print("P8 光标还原")
print("=" * 74)
src = (ROOT / "sphdl" / "autopage.py").read_text(encoding="utf-8")
check("P8a 记录原光标位置", "old = get_cursor_pos()" in src)
check("P8b finally 里还原", "set_cursor_pos(*old)" in src)

print()
print("=" * 74)
print("P9-P10 UI 行为")
print("=" * 74)
ui = (ROOT / "sphdl" / "autopage_ui.py").read_text(encoding="utf-8")
check("P9a ★✕ 是 withdraw 不是 destroy",
      "WM_DELETE_WINDOW" in ui and "self.win.withdraw()" in ui
      and "destroy()" not in ui.split("def _hide")[1][:200])
check("P9b 不用 transient（否则没有最小化/最大化）",
      "transient(" not in ui)
check("P9c 窗口可调整大小", "resizable(True, True)" in ui)

gui = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8")
check("P10a 主界面有自动翻页按钮", "🔁 自动翻页" in gui)
check("P10b 事件 ap_log 已接", '"ap_log"' in gui or "('ap_log'" in gui or "ap_log" in gui)
check("P10c 事件 ap_prog 已接", "ap_prog" in gui)
check("P10d 事件 autopage_done 已接", "autopage_done" in gui)
check("P10e 入口方法 _on_autopage", "def _on_autopage" in gui)

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
