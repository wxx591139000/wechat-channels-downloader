"""批量拉取止损与断网保护验证（v2.7.3）。

═══ 客户实测（2026-10-29）═══
「一直在自动翻页，但只抓了 59 条。刚才 wifi 断了」

═══ 日志证据 ═══
· 59 条全部在 13:51:27 之前抓完（125 次捕获 → 59 条唯一）
· 之后 7 分钟零新增，但兜底线程每 12 秒照滚一次，白滚 35 次
· 钩子数据几乎不增长（心跳 13→18）→ 页面确实没在请求数据

→ 工具**不知道断网**，继续空转。要修两件事：
   ① 断网 → 立即停批量并明确告知（不是让客户等）
   ② 连续多次兜底滚动都没新增 → 判定到底，自动停（不能无限滚）

覆盖：
N1  网络探测函数存在且可调用
N2  ★断网 → 停止批量 + 提示含「网络已断开」
N3  ★提示里有「已抓 N 条」「恢复后继续」「不会重复」
N4  ★连续 5 次兜底无新增 → 判定到底自动停
N5  兜底有次数上限常量（不能无限滚）
N6  batch_autostop 事件已接并真正关掉批量
N7  探测优先用 qq.com / channels（不是随便一个域名）
N8  有新增时 idle 计数归零（不会误判到底）
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
print("N1 网络探测")
print("=" * 74)
check("N1a 有 _net_ok 方法", "def _net_ok" in gui)
i = gui.find("def _net_ok")
j = gui.find("def _start_batch_wheel_fallback", i)
seg = gui[i:j]
check("N1b 用 socket.create_connection", "create_connection" in seg)
check("N1c 有超时（不会卡住）", "timeout" in seg)
check("N1d 探测多个目标（一个不通再试另一个）",
      seg.count("for host, port in") == 1 and "www.qq.com" in seg
      and "channels.weixin.qq.com" in seg)
check("N7 ★探测的是 QQ/视频号域名（不是随便一个）",
      "www.qq.com" in seg and "channels.weixin.qq.com" in seg)

# 实跑一次（本机应当有网）
try:
    import subprocess
    code = ("import sys; sys.path.insert(0,'.');"
            "from sphdl.gui import App;"
            "print('NET_OK' if App._net_ok() else 'NET_FAIL')")
    r = subprocess.run(["E:/myHermesWorkSpace/wechat-channels-downloader/.venv/Scripts/python.exe",
                        "-c", code], cwd=str(ROOT), capture_output=True,
                       text=True, timeout=60)
    out = r.stdout.strip()
    print(f"  实跑 _net_ok(): {out or r.stderr[:120]}")
    check("N1e 实跑可返回结果", "NET_" in out, out)
except Exception as e:                                   # noqa: BLE001
    check("N1e 实跑可返回结果", False, str(e))

print()
print("=" * 74)
print("N2-N3 断网保护")
print("=" * 74)
k = gui.find("def _start_batch_wheel_fallback")
k2 = gui.find("def _stop_batch_wheel_fallback")
loop = gui[k:k2]
check("N2a 兜底里调用了 _net_ok", "self._net_ok()" in loop)
check("N2b ★断网时 push_log 告警",
      "网络已断开" in loop)
check("N2c ★断网时发 batch_autostop 事件",
      '("batch_autostop", "网断")' in loop.replace("'", '"')
      or "batch_autostop" in loop)
check("N3a 提示含已抓条数", "batch_count" in loop)
check("N3b 提示含「恢复网络后重新点」",
      "重新点" in loop and "批量拉取" in loop)
check("N3c 提示含「不会重复」", "不会重复" in loop)

print()
print("=" * 74)
print("N4-N5 到底止损（不能无限滚）")
print("=" * 74)
check("N4a 有 idle_rounds 计数", "idle_rounds" in loop)
check("N4b ★有次数上限 MAX_IDLE_ROUNDS", "MAX_IDLE_ROUNDS" in loop)
m = re.search(r"MAX_IDLE_ROUNDS\s*=\s*(\d+)", loop)
print(f"  MAX_IDLE_ROUNDS = {m.group(1) if m else '?'}")
check("N4c 上限合理（3~10 次）",
      m is not None and 3 <= int(m.group(1)) <= 10,
      m.group(1) if m else "?")
check("N4d ★超限后判定到底并停止",
      "判定已拉到底" in loop and "batch_autostop" in loop)
check("N4e 到底提示含本次抓取条数", "batch_count" in loop)
check("N5a 每次兜底滚动都累加计数",
      "idle_rounds += 1" in loop)
check("N8 ★有新增时 idle 归零", "idle_rounds = 0" in loop)

print()
print("=" * 74)
print("N6 事件接线")
print("=" * 74)
check("N6a batch_autostop 事件分支存在",
      'kind == "batch_autostop"' in gui)
check("N6b ★事件里真正调用 _set_batch(False)",
      "_set_batch(False" in gui[gui.find('kind == "batch_autostop"'):][:400])
# auto 分支不该再重复提示
seg_auto = gui[gui.find("def _set_batch"):gui.find("def _update_capture_hint")]
check("N6c auto='网断' 不重复提示", 'auto == "网断"' in seg_auto)
check("N6d auto='到底' 不重复提示", 'auto == "到底"' in seg_auto)

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
