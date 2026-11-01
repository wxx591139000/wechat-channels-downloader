"""探测抖音短链跳转到哪里（主页 or 作品）。

客户输入：长按复制此条消息，打开抖音搜索，查看TA的更多作品。
         https://v.douyin.com/N00VRZ-_isA/

「查看TA的更多作品」= 主页分享，但短链本身没有 sec_uid，
必须跟随重定向才知道落到 /user/ 还是 /video/。

本脚本验证：
  1. 短链最终跳到什么 URL
  2. 能否从中提取 sec_uid（主页）或 aweme_id（作品）
  3. 现有解析流程会怎么处理它（会不会报「30 秒内没等到视频数据」）
"""
import sys
import time
from pathlib import Path

ROOT = Path(r"E:/myHermesWorkSpace/wechat-channels-downloader/sphdl")
sys.path.insert(0, str(ROOT))

from sphdl import douyin as DY  # noqa: E402
from sphdl.douyin import profile_dir, UA  # noqa: E402

SHORT = sys.argv[1] if len(sys.argv) > 1 else "https://v.douyin.com/N00VRZ-_isA/"

print("=" * 74)
print(f"短链: {SHORT}")
print("=" * 74)

# ① 裸 HTTP 跟随重定向（不带浏览器，最快）
print("\n① 裸 HTTP 跟随重定向")
try:
    import urllib.request
    req = urllib.request.Request(SHORT, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        print(f"  最终 URL: {r.url}")
        print(f"  状态: {r.status}")
        final_http = r.url
except Exception as e:
    print(f"  ❌ {type(e).__name__}: {str(e)[:100]}")
    final_http = None

# ② 浏览器跟随（抖音可能用 JS 跳转）
print("\n② 浏览器跟随重定向")
from playwright.sync_api import sync_playwright  # noqa: E402

final_browser = None
with sync_playwright() as p:
    ctx = None
    for name, kw in DY._browser_candidates()[:2]:
        try:
            ctx = p.chromium.launch_persistent_context(
                str(profile_dir()), headless=True,
                args=["--no-first-run"], user_agent=UA, locale="zh-CN",
                viewport={"width": 1400, "height": 900}, **kw)
            print(f"  浏览器: {name}")
            break
        except Exception:
            continue
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        pg.goto(SHORT, wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)
        final_browser = pg.url
        print(f"  落地 URL: {final_browser}")
        print(f"  标题: {pg.title()[:70]}")
        # 页面结构判断
        info = pg.evaluate("""() => {
            return {
                h1: !!document.querySelector('h1'),
                h1txt: (document.querySelector('h1')||{}).textContent || null,
                video: !!document.querySelector('video'),
                userLinks: [...document.querySelectorAll('a[href*="/user/"]')]
                    .filter(e => e.offsetParent && !e.href.includes('/self'))
                    .length,
                bodyLen: document.body.innerText.length,
                bodyHead: document.body.innerText.slice(0, 150)
            };
        }""")
        for k, v in info.items():
            print(f"    {k}: {v}")
    except Exception as e:
        print(f"  ❌ {type(e).__name__}: {str(e)[:100]}")
    ctx.close()

# ③ 分析
print("\n③ 分析")
from sphdl.douyin import _aweme_id_from_url  # noqa: E402
from sphdl.douyin_profile import normalize  # noqa: E402

for label, u in (("HTTP", final_http), ("浏览器", final_browser)):
    if not u:
        continue
    aid = _aweme_id_from_url(u)
    n = normalize(u)
    print(f"  [{label}] {u}")
    print(f"      aweme_id: {aid or '（无）'}")
    print(f"      normalize.kind: {n['kind']}")
    print(f"      sec_uid: {(n.get('sec_uid') or '（无）')[:40]}")

print()
print("=" * 74)
print("结论")
print("=" * 74)
if final_browser:
    if "/user/" in final_browser:
        print("  → 这是**主页**短链，应走方案 B（主页批量抓取）")
        print("     但现在被当成作品解析 → 页面没有 detail 接口")
        print("     → 报「30 秒内没等到视频数据」")
    elif _aweme_id_from_url(final_browser):
        print("  → 这是**作品**短链，现有流程应该能处理")
    else:
        print(f"  → 未知落地类型: {final_browser}")
