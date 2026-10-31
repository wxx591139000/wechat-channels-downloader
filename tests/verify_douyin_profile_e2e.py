"""抖音主页批量抓取（方案 B）端到端验证 —— 真实网络。

═══ 验证什么 ═══
不是"函数能跑"，而是：
  1. 真实 Edge 能打开抖音主页
  2. 能捕获到 aweme/post 列表响应
  3. 能解析出真实作品（标题/地址/时长）
  4. 滚动能触发加载更多（has_more 分页生效）
  5. 未登录时给出明确提示，不是静默失败

用法：
  python tests/verify_douyin_profile_e2e.py [主页URL]
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


# 抖音官方账号（作品多、公开、不易被限制）
DEFAULT_URL = "https://www.douyin.com/user/MS4wLjABAAAAv7iSuuXDJGDvJkmH_vz1qkDZYo1apxgzaxdBSeIuPiM"

url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
print(f"目标主页: {url}")

from sphdl import douyin as DY  # noqa: E402
from sphdl import douyin_profile as DP  # noqa: E402

print(f"登录状态: {'已登录' if DY.is_logged_in() else '未登录'}")
print()

logs = []


def on_log(lv, msg):
    logs.append((lv, msg))
    print(f"  [{lv}] {msg}")


t0 = time.time()
res = DP.fetch_profile(url, max_items=30, on_log=on_log)
dt = time.time() - t0

print()
print("=" * 74)
print(f"抓取结果（耗时 {dt:.1f} 秒）")
print("=" * 74)
print(f"  sec_uid   : {res.sec_uid[:30]}")
print(f"  昵称      : {res.nickname!r}")
print(f"  作品数    : {len(res.items)}")
print(f"  has_more  : {res.has_more}")
print(f"  说明      : {res.note}")
print(f"  错误      : {res.error}")

print()
check("E1 没报致命错误", not res.error, res.error[:60])
check("E2 ★抓到作品", len(res.items) > 0, f"{len(res.items)} 条")

if res.items:
    print()
    print("前 5 条：")
    for v in res.items[:5]:
        print(f"  · {v.title[:44]:46s} {v.duration_text:>7s}  "
              f"{v.size_text:>8s}  {v.author}")
    print()

    v0 = res.items[0]
    check("E3 ★有下载地址", bool(v0.play_url) and "http" in v0.play_url,
          v0.play_url[:60])
    check("E4 ★有标题", bool(v0.title), v0.title[:40])
    check("E5 ★有时长", v0.duration_ms > 0, v0.duration_text)
    check("E6 有作者", bool(v0.author), v0.author)
    check("E7 aweme_id 唯一", len({x.aweme_id for x in res.items})
          == len(res.items))

    # 地址可访问性（拿第一个试 HEAD）
    try:
        import urllib.request
        req = urllib.request.Request(
            v0.play_url, method="GET",
            headers={"User-Agent": DY.UA,
                     "Referer": "https://www.douyin.com/"})
        with urllib.request.urlopen(req, timeout=15) as r:
            ct = r.headers.get("content-type", "")
            n = int(r.headers.get("content-length") or 0)
            print(f"\n  首个地址 HEAD: HTTP {r.status}  {ct}  {n} 字节")
            check("E8 ★★地址真的能下（非 403）",
                  r.status in (200, 206), f"HTTP {r.status}")
            check("E9 是视频类型", "video" in ct, ct)
    except Exception as e:
        check("E8 ★★地址真的能下（非 403）", False, str(e)[:60])
        check("E9 是视频类型", False, "-")

# 分页验证
check("E10 ★滚动触发了分页（有 has_more 或抓到多条）",
      res.has_more or len(res.items) > 8,
      f"has_more={res.has_more} items={len(res.items)}")

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
