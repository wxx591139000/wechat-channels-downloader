"""抖音主页抓取：登录状态下的可行性验证。

═══ 为什么需要单跑这个 ═══
实测未登录时抖音主页永远显示「数据加载中」，
**一个 aweme 接口都不发**（216 个响应里 0 个是作品列表）。
且出现 `page/turn/offline` + `verify_center` = 风控拦截。

所以方案 B 的前置条件是**必须登录**。本脚本用于登录后验证。

用法：
  1. 先跑 `python tests/verify_douyin_profile_e2e.py`（会提示未登录）
  2. 在工具里点「登录抖音」，完成扫码
  3. 再跑本脚本
"""
import sys
import time
from pathlib import Path

ROOT = Path(r"E:/myHermesWorkSpace/wechat-channels-downloader/sphdl")
sys.path.insert(0, str(ROOT))

from sphdl import douyin as DY  # noqa: E402

print("=" * 70)
print("抖音登录状态检查")
print("=" * 70)
prof = DY.profile_dir()
print(f"profile 目录: {prof}")
print(f"存在: {prof.exists()}")
print(f"已登录: {DY.is_logged_in()}")
print()

if not DY.is_logged_in():
    print("❌ 当前未登录 —— 方案 B 必须先登录")
    print()
    print("请在工具里：打开抖音窗口 → 点「登录抖音」→ 扫码")
    print("（登录后用 headless 复用 profile 才能拿到作品列表）")
    sys.exit(1)

print("✅ 已登录，开始验证主页抓取")
print()

URL = sys.argv[1] if len(sys.argv) > 1 else (
    "https://www.douyin.com/user/"
    "MS4wLjABAAAAv7iSuuXDJGDvJkmH_vz1qkDZYo1apxgzaxdBSeIuPiM")

from sphdl import douyin_profile as DP  # noqa: E402


def on_log(lv, msg):
    print(f"  [{lv}] {msg}")


t0 = time.time()
res = DP.fetch_profile(URL, max_items=20, on_log=on_log)
dt = time.time() - t0

print()
print("=" * 70)
print(f"结果（{dt:.1f} 秒）")
print("=" * 70)
print(f"  昵称: {res.nickname!r}")
print(f"  作品: {len(res.items)} 条")
print(f"  has_more: {res.has_more}")
print(f"  说明: {res.note}")
print(f"  错误: {res.error}")

if res.items:
    print()
    print("前 5 条：")
    for v in res.items[:5]:
        print(f"  · {v.title[:40]:42s} {v.duration_text:>7s} {v.size_text:>8s}")
    print()
    print("✅ 方案 B 可用！")
    sys.exit(0)

print()
print("❌ 登录后仍抓不到，需要排查")
sys.exit(1)
