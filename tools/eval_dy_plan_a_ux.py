"""方案 A 客户体感评估：滚动 + 重抓 每次只拿 18 条，客户要滚多少次？

═══ 要回答的问题 ═══
如果只支持「每次抓第一页 18 条」，客户想抓完一个 84 条作品的主页，
需要怎么做？体验如何？

═══ 实测设计 ═══
模拟客户的真实操作：
  1. 打开主页 → 抓第一页（18 条）
  2. 在页面上滚动（模拟客户手动滚）→ 再抓一次（应该拿到后续的 18 条？）
  3. 重复，看能否把 84 条全拿到
  4. 记录：需要滚几次、每次抓到几条、总耗时

关键验证：**滚动后重抓，第一页会不会变成"后续的 18 条"**
（如果会 → 客户滚几次就能抓完；如果不会 → 方案 A 不成立）
"""
import sys
import time
from pathlib import Path

ROOT = Path(r"E:/myHermesWorkSpace/wechat-channels-downloader/sphdl")
sys.path.insert(0, str(ROOT))

from sphdl import douyin as DY  # noqa: E402
from sphdl.douyin import profile_dir, UA  # noqa: E402
from sphdl.douyin_profile_fetch import _FETCH_JS  # noqa: E402

SEC = "MS4wLjABAAAAAgTdaUkW9HoblheZJ-W44LQ-5YkSC2pbNbssIgKa8tg"

from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as p:
    ctx = None
    for name, kw in DY._browser_candidates()[:2]:
        try:
            ctx = p.chromium.launch_persistent_context(
                str(profile_dir()), headless=False,   # ★ 有头，更接近客户环境
                args=["--no-first-run"], user_agent=UA, locale="zh-CN",
                viewport={"width": 1400, "height": 900}, **kw)
            print(f"浏览器: {name}（有头模式）")
            break
        except Exception as e:
            print(f"{name} 失败: {str(e)[:60]}")
    if ctx is None:
        sys.exit(1)

    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    pg.goto(f"https://www.douyin.com/user/{SEC}",
            wait_until="commit", timeout=45000)
    print("等待页面加载 8 秒…")
    time.sleep(8)

    seen = set()
    total_calls = 0
    scrolls = 0
    t0 = time.time()

    print()
    print("=" * 74)
    print("模拟客户操作：抓一次 → 滚一次 → 再抓，看能否逐步拿全")
    print("=" * 74)

    for rnd in range(1, 15):
        # 抓当前「第一页」
        r = pg.evaluate(_FETCH_JS, [SEC, 0, 18])
        items = r.get("items") or []
        new = [it for it in items if it.get("aweme_id") not in seen]
        for it in new:
            seen.add(it.get("aweme_id"))
        total_calls += 1
        print(f"  第 {rnd} 次抓取: 返回 {len(items)} 条, 新增 {len(new)} 条, "
              f"累计 {len(seen)}")
        if new:
            print(f"      首条: {(new[0].get('desc') or '')[:44]}")

        if len(new) == 0:
            print(f"      → 没有新内容了")
            break

        # 滚动到底（模拟客户手动滚）
        h = pg.evaluate("""() => {
            let best = null, bestH = 0;
            for (const el of document.querySelectorAll('*')) {
                const st = getComputedStyle(el);
                if (!/auto|scroll/.test(st.overflowY)) continue;
                const hh = el.scrollHeight - el.clientHeight;
                if (hh > bestH) { bestH = hh; best = el; }
            }
            if (best && bestH > 100) {
                best.scrollTop = best.scrollHeight;
                return bestH;
            }
            window.scrollTo(0, document.documentElement.scrollHeight);
            return document.documentElement.scrollHeight;
        }""")
        scrolls += 1
        print(f"      滚动（可滚高度 {h}），等 5 秒…")
        time.sleep(5)

    dt = time.time() - t0
    print()
    print("=" * 74)
    print("结果")
    print("=" * 74)
    print(f"  最终拿到: {len(seen)} 条")
    print(f"  抓取次数: {total_calls} 次")
    print(f"  滚动次数: {scrolls} 次")
    print(f"  总耗时:   {dt:.0f} 秒")

    ctx.close()
