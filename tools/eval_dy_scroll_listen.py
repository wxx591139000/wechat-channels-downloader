"""方案 A 变体：客户手动滚动，工具监听页面自发请求累加。

═══ 思路 ═══
既然：
  · 工具自己 fetch 会被限流（第 2 页空）
  · 工具自己滚动无效（拿到的还是第一页）
  · 但手动脚本连续 fetch 5 次是成功的

那退一步：**客户在可见浏览器里手动滚动**，页面自己会发
aweme/post 请求（带正确 cursor + 签名），工具只负责**监听响应累加**。

这是方案 A 的真正形态：客户滚 → 页面加载 → 工具白拿。
不需要客户做任何"抓取"操作。

═══ 本脚本验证 ═══
1. 开有头浏览器
2. 监听所有 aweme/post 响应
3. 用 JS 模拟"客户滚动"（逐步小步滚，模拟人手）
4. 看能累加到多少条、cursor 是否推进

如果成功 → 方案 A 可行：客户只需「一边滚一边等着」。
"""
import sys
import time
from pathlib import Path

ROOT = Path(r"E:/myHermesWorkSpace/wechat-channels-downloader/sphdl")
sys.path.insert(0, str(ROOT))

from sphdl import douyin as DY  # noqa: E402
from sphdl.douyin import profile_dir, UA  # noqa: E402

SEC = "MS4wLjABAAAAAgTdaUkW9HoblheZJ-W44LQ-5YkSC2pbNbssIgKa8tg"

from playwright.sync_api import sync_playwright  # noqa: E402

batches = []


def on_resp(r):
    if "aweme/post" in r.url:
        try:
            d = r.json()
            lst = d.get("aweme_list") or []
            batches.append({
                "cursor": d.get("max_cursor"),
                "n": len(lst),
                "has_more": d.get("has_more"),
                "first": (lst[0].get("desc") or "")[:34] if lst else "",
                "ids": [str(a.get("aweme_id")) for a in lst],
            })
        except Exception:
            pass


with sync_playwright() as p:
    ctx = None
    for name, kw in DY._browser_candidates()[:2]:
        try:
            ctx = p.chromium.launch_persistent_context(
                str(profile_dir()), headless=False,
                args=["--no-first-run"], user_agent=UA, locale="zh-CN",
                viewport={"width": 1400, "height": 900}, **kw)
            print(f"浏览器: {name}（有头）")
            break
        except Exception:
            continue
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    pg.on("response", on_resp)

    pg.goto(f"https://www.douyin.com/user/{SEC}",
            wait_until="commit", timeout=45000)
    print("等页面加载 8 秒…")
    time.sleep(8)
    print(f"初次: 已捕获 {len(batches)} 批")

    print()
    print("=" * 74)
    print("模拟客户逐步滚动（每次滚 1/3 屏，间隔 2 秒，共 20 次）")
    print("=" * 74)

    seen = set()
    for b in batches:
        seen.update(b["ids"])
    print(f"初始累计 {len(seen)} 条")

    for i in range(1, 26):
        info = pg.evaluate("""() => {
            let best=null,bestH=0;
            for (const el of document.querySelectorAll('*')) {
                const st=getComputedStyle(el);
                if(!/auto|scroll/.test(st.overflowY)) continue;
                const h=el.scrollHeight-el.clientHeight;
                if(h>bestH){bestH=h;best=el;}
            }
            if (best) {
                const before = best.scrollTop;
                // 一次滚到底
                best.scrollTop = best.scrollHeight;
                return {top: before, max: best.scrollHeight - best.clientHeight,
                        now: best.scrollTop, h: best.scrollHeight};
            }
            window.scrollTo(0, document.documentElement.scrollHeight);
            return {top: 0, max: 0, now: window.scrollY, h: 0};
        }""")
        time.sleep(2.5)
        before = len(seen)
        for b in batches:
            seen.update(b["ids"])
        print(f"  滚 {i:2d}: top {info['top']}→{info['now']}/{info['max']}  "
              f"高{info['h']}  批{len(batches)}  累计{len(seen)}"
              f"（+{len(seen)-before}）")
        if batches and batches[-1]["has_more"] == 0:
            print(f"  → has_more=0，已到底")
            break

    print()
    print("=" * 74)
    print("结果")
    print("=" * 74)
    print(f"  拦截到 {len(batches)} 批 aweme/post 响应")
    for b in batches:
        print(f"    cursor={b['cursor']} n={b['n']:2d} "
              f"has_more={b['has_more']}  {b['first'][:30]}")
    print(f"  去重后作品: {len(seen)} 条")

    ctx.close()
