"""诊断：主页滚动为什么加载不出更多作品。

现象：首页拿到 18 条，has_more=True，但滚动 8 轮都没有新请求。
本脚本检查：
  ① 滚动前后 scrollTop 是否真的变了
  ② 页面里有没有可滚动容器
  ③ 滚到底后接口有没有再发
"""
import sys
import time
from pathlib import Path

ROOT = Path(r"E:/myHermesWorkSpace/wechat-channels-downloader/sphdl")
sys.path.insert(0, str(ROOT))

from sphdl import douyin as DY  # noqa: E402
from sphdl.douyin import profile_dir, UA  # noqa: E402

SEC = "MS4wLjABAAAAAgTdaUkW9HoblheZJ-W44LQ-5YkSC2pbNbssIgKa8tg"
URL = f"https://www.douyin.com/user/{SEC}"

from playwright.sync_api import sync_playwright  # noqa: E402

posts = []


def on_resp(r):
    if "aweme/post" in r.url:
        try:
            d = r.json()
            posts.append({
                "cursor": d.get("max_cursor"),
                "n": len(d.get("aweme_list") or []),
                "has_more": d.get("has_more"),
            })
        except Exception:
            posts.append({"err": True})


with sync_playwright() as p:
    ctx = None
    for name, kw in DY._browser_candidates()[:2]:
        try:
            ctx = p.chromium.launch_persistent_context(
                str(profile_dir()), headless=True,
                args=["--no-first-run"], user_agent=UA, locale="zh-CN",
                viewport={"width": 1400, "height": 900}, **kw)
            break
        except Exception:
            continue
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    pg.on("response", on_resp)
    pg.goto(URL, wait_until="commit", timeout=45000)
    time.sleep(8)

    print("=" * 74)
    print(f"初次加载：aweme/post 请求 {len(posts)} 次  {posts}")
    print("=" * 74)

    # 检查滚动容器
    info = pg.evaluate("""() => {
        const out = {containers: [], winH: document.documentElement.scrollHeight,
                     winClient: window.innerHeight};
        for (const el of document.querySelectorAll('*')) {
            const st = getComputedStyle(el);
            if (!/auto|scroll/.test(st.overflowY)) continue;
            const h = el.scrollHeight - el.clientHeight;
            if (h > 100) {
                out.containers.push({
                    tag: el.tagName, cls: (el.className || '').toString().slice(0,50),
                    scrollH: el.scrollHeight, clientH: el.clientHeight,
                    canScroll: h
                });
            }
        }
        out.containers.sort((a,b) => b.canScroll - a.canScroll);
        out.containers = out.containers.slice(0, 5);
        return out;
    }""")
    print(f"\n窗口可滚动高度: {info['winH'] - info['winClient']}px")
    print(f"可滚动容器 {len(info['containers'])} 个：")
    for c in info["containers"]:
        print(f"  {c['tag']:6s} scrollH={c['scrollH']:>7} clientH={c['clientH']:>6} "
              f"可滚={c['canScroll']:>6}  class={c['cls']!r}")

    # 尝试滚动并观察
    print()
    print("=" * 74)
    print("尝试滚动 6 次，每次看 scrollTop 和请求数")
    print("=" * 74)
    for i in range(6):
        before = pg.evaluate("""() => {
            let best=null,bestH=0;
            for (const el of document.querySelectorAll('*')) {
                const st=getComputedStyle(el);
                if (!/auto|scroll/.test(st.overflowY)) continue;
                const h=el.scrollHeight-el.clientHeight;
                if (h>bestH){bestH=h;best=el;}
            }
            return {top: best?best.scrollTop:-1, h: best?best.scrollHeight:-1,
                    winTop: window.scrollY, winH: document.documentElement.scrollHeight};
        }""")
        # 滚动
        pg.evaluate("""() => {
            let best=null,bestH=0;
            for (const el of document.querySelectorAll('*')) {
                const st=getComputedStyle(el);
                if (!/auto|scroll/.test(st.overflowY)) continue;
                const h=el.scrollHeight-el.clientHeight;
                if (h>bestH){bestH=h;best=el;}
            }
            if (best) best.scrollTop = best.scrollHeight;
            window.scrollTo(0, document.documentElement.scrollHeight);
        }""")
        time.sleep(3)
        after = pg.evaluate("""() => {
            let best=null,bestH=0;
            for (const el of document.querySelectorAll('*')) {
                const st=getComputedStyle(el);
                if (!/auto|scroll/.test(st.overflowY)) continue;
                const h=el.scrollHeight-el.clientHeight;
                if (h>bestH){bestH=h;best=el;}
            }
            return {top: best?best.scrollTop:-1, h: best?best.scrollHeight:-1,
                    winTop: window.scrollY, winH: document.documentElement.scrollHeight};
        }""")
        print(f"  [{i+1}] 容器top {before['top']}→{after['top']}  "
              f"容器高 {before['h']}→{after['h']}  "
              f"窗口 {before['winTop']}→{after['winTop']}  "
              f"post请求 {len(posts)} 次")

    print()
    print(f"最终 aweme/post 请求: {len(posts)} 次")
    for x in posts:
        print(f"  {x}")

    # 检查页面作品数
    n = pg.evaluate("""() => document.querySelectorAll('a[href*="/video/"]').length""")
    print(f"页面里 video 链接数: {n}")

    ctx.close()
