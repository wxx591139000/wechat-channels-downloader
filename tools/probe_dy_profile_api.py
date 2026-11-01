"""深挖抖音主页作品列表接口（方案 B 抓不到任何作品）。

═══ 客户实测（2026-11-01）═══
短链正确识别为主页，但抓取结果：
```
[主页] 第 1 轮：无新增（1/6）
[主页] 结束：共 0 条，has_more=False，接口响应 0 次
```

**关键：`接口响应 0 次`** —— 说明我们的关键词
（`aweme/v1/web/aweme/post`）一个都没匹配到。

可能原因：
  ① 接口路径变了
  ② 主页数据走 SSR（不在 xhr 里）
  ③ 需要滚动才发请求，但滚动没生效

做法：打开主页，记录**所有** xhr/fetch + 所有 JSON 响应（不限关键词），
再滚动几次，看数据到底从哪来。
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(r"E:/myHermesWorkSpace/wechat-channels-downloader/sphdl")
sys.path.insert(0, str(ROOT))

from sphdl import douyin as DY  # noqa: E402
from sphdl.douyin import profile_dir, UA  # noqa: E402

SEC = "MS4wLjABAAAAAgTdaUkW9HoblheZJ-W44LQ-5YkSC2pbNbssIgKa8tg"
URL = f"https://www.douyin.com/user/{SEC}"
OUT = Path("C:/Users/Dancing/AppData/Local/Temp/dy_profile_probe")
OUT.mkdir(parents=True, exist_ok=True)

from playwright.sync_api import sync_playwright  # noqa: E402

xhrs = []
jsons = []


def on_resp(r):
    try:
        rt = r.request.resource_type
        if rt not in ("xhr", "fetch"):
            return
        xhrs.append(r.url)
        ct = (r.headers or {}).get("content-type", "")
        if "json" in ct:
            b = r.text()
            if len(b) > 200:
                jsons.append({"url": r.url, "len": len(b), "body": b})
    except Exception:
        pass


with sync_playwright() as p:
    ctx = None
    for name, kw in DY._browser_candidates()[:2]:
        try:
            ctx = p.chromium.launch_persistent_context(
                str(profile_dir()), headless=True,
                args=["--no-first-run"], user_agent=UA, locale="zh-CN",
                viewport={"width": 1400, "height": 900}, **kw)
            print(f"浏览器: {name}")
            break
        except Exception as e:
            print(f"{name} 失败: {str(e)[:60]}")
    if ctx is None:
        sys.exit(1)

    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    pg.on("response", on_resp)

    print(f"打开 {URL}")
    pg.goto(URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(5)

    print(f"\n【初次加载】xhr/fetch {len(xhrs)} 个，JSON {len(jsons)} 个")
    for u in xhrs:
        if any(k in u for k in ("aweme", "post", "user", "item", "mix")):
            print(f"  {u[:130]}")

    # 滚动多次
    print("\n滚动 5 次…")
    for i in range(5):
        pg.mouse.wheel(0, 2500)
        time.sleep(2.5)
        print(f"  第 {i+1} 次滚动后：xhr {len(xhrs)} 个，JSON {len(jsons)} 个")

    print()
    print("=" * 74)
    print(f"全部 xhr/fetch（{len(xhrs)} 个，去重）")
    print("=" * 74)
    seen = set()
    for u in xhrs:
        base = u.split("?")[0]
        if base in seen:
            continue
        seen.add(base)
        print(f"  {base[:120]}")

    print()
    print("=" * 74)
    print(f"JSON 响应（{len(jsons)} 个，按大小）")
    print("=" * 74)
    for j in sorted(jsons, key=lambda x: -x["len"])[:10]:
        print(f"  {j['len']:>9,}  {j['url'][:115]}")

    # 检查页面里有没有作品（DOM）
    print()
    print("=" * 74)
    print("DOM 里有没有作品")
    print("=" * 74)
    info = pg.evaluate("""() => {
        const out = {};
        // 作品链接
        const vids = [...document.querySelectorAll('a[href*="/video/"]')]
            .filter(e => e.offsetParent);
        out.videoLinks = vids.length;
        out.firstFew = vids.slice(0,3).map(e => e.getAttribute('href'));
        // 页面文字
        out.bodyLen = document.body.innerText.length;
        out.bodyHead = document.body.innerText.slice(0, 200);
        // 是否有「作品」字样
        out.hasWorks = document.body.innerText.includes('作品');
        return out;
    }""")
    for k, v in info.items():
        print(f"  {k}: {v}")

    # 保存所有 JSON
    for i, j in enumerate(sorted(jsons, key=lambda x: -x["len"])[:5]):
        fn = OUT / f"json_{i}.json"
        fn.write_text(j["body"], encoding="utf-8")
        try:
            d = json.loads(j["body"])
            print(f"\n  {fn.name} 顶层键: {list(d.keys())[:12]}")
            for k, v in d.items():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    print(f"    {k}: {len(v)} 条，首条键 {sorted(v[0].keys())[:10]}")
        except Exception:
            pass

    ctx.close()
