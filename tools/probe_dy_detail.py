"""深挖抖音视频详情接口（客户：标题/作者/时长全丢了）。

═══ 背景 ═══
修完浏览器启动问题后，客户那条链接能解析出**地址**，但：
  标题 = 默认名「抖音视频_<id>」
  作者 = 空      时长 = 0 秒      大小 = —

抓包确认三种链接形态都拿不到 `aweme/detail` 响应（0 个）。
说明抖音改版了。本脚本要找出**现在数据从哪来**。

探测策略（不猜，全量记录）：
  1. 打开作品页（不是精选页），记录**所有** XHR/fetch
  2. 记录所有 JSON 响应（不限关键词）
  3. 在页面里找渲染出来的标题/作者（DOM 兜底方案）
  4. 检查 window 上的全局数据对象（SSR 或 __INIT_PROPS__）
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(r"E:/myHermesWorkSpace/wechat-channels-downloader/sphdl")
sys.path.insert(0, str(ROOT))

from sphdl import douyin as DY  # noqa: E402
from sphdl.douyin import profile_dir, UA  # noqa: E402

AID = sys.argv[1] if len(sys.argv) > 1 else "7702018171426868531"
OUT = Path("C:/Users/Dancing/AppData/Local/Temp/dy_detail_probe")
OUT.mkdir(parents=True, exist_ok=True)

TARGETS = [
    ("作品页", f"https://www.douyin.com/video/{AID}"),
    ("精选页", f"https://www.douyin.com/jingxuan?modal_id={AID}"),
]

from playwright.sync_api import sync_playwright  # noqa: E402

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
            print(f"{name} 失败: {str(e)[:70]}")
    if ctx is None:
        sys.exit(1)

    for label, url in TARGETS:
        print()
        print("=" * 74)
        print(f"【{label}】{url[:80]}")
        print("=" * 74)

        jsons = []
        all_reqs = []

        def on_resp(r, j=jsons, a=all_reqs):
            try:
                a.append((r.request.resource_type, r.url))
                if r.request.resource_type not in ("xhr", "fetch"):
                    return
                ct = (r.headers or {}).get("content-type", "")
                if "json" not in ct:
                    return
                b = r.text()
                if len(b) > 150:
                    j.append({"url": r.url, "len": len(b), "body": b})
            except Exception:
                pass

        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        pg.on("response", on_resp)
        try:
            pg.goto(url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(6)
        except Exception as e:
            print(f"  打开失败: {str(e)[:70]}")

        print(f"  xhr/fetch 请求 {sum(1 for t,_ in all_reqs if t in ('xhr','fetch'))} 个，"
              f"JSON 响应 {len(jsons)} 个")

        print("\n  ── 含 aweme/video 的请求 ──")
        for t, u in all_reqs:
            if t in ("xhr", "fetch") and any(k in u for k in ("aweme", "video", "item")):
                print(f"    [{t}] {u[:135]}")

        print("\n  ── JSON 响应（按大小）──")
        for j in sorted(jsons, key=lambda x: -x["len"])[:8]:
            print(f"    {j['len']:>9,}  {j['url'][:120]}")

        # 保存最大的几个
        for i, j in enumerate(sorted(jsons, key=lambda x: -x["len"])[:3]):
            fn = OUT / f"{label}_{i}.json"
            fn.write_text(j["body"], encoding="utf-8")
            try:
                d = json.loads(j["body"])
                keys = list(d.keys())[:12]
                print(f"\n    {fn.name}: 顶层键 {keys}")
                # 找含 desc/author/duration 的字段
                txt = j["body"]
                for kw in ('"desc"', '"nickname"', '"duration"', '"aweme_id"'):
                    if kw in txt:
                        print(f"      含 {kw} ✅")
            except Exception:
                pass

        # ★ DOM 兜底：页面渲染出来的标题
        print("\n  ── DOM 里能看到的信息 ──")
        dom = pg.evaluate("""() => {
            const out = {};
            // 标题：h1 或 title
            const h1 = document.querySelector('h1');
            out.h1 = h1 ? h1.textContent.trim().slice(0,120) : null;
            out.docTitle = document.title.slice(0,120);
            // 作者：找带 @ 的文本
            const atEls = [...document.querySelectorAll('a,span,div')]
                .filter(e => /^@/.test((e.textContent||'').trim()) && e.offsetParent);
            out.author = atEls.length ? atEls[0].textContent.trim().slice(0,40) : null;
            // 全局数据
            out.hasInitProps = typeof window.__INIT_PROPS__ !== 'undefined';
            out.hasRENDER_DATA = typeof window._ROUTER_DATA !== 'undefined';
            out.globals = Object.keys(window).filter(k =>
                /aweme|video|render|init|props|data/i.test(k)).slice(0,15);
            return out;
        }""")
        for k, v in dom.items():
            print(f"    {k}: {v}")

        print(f"\n  已存 JSON 到 {OUT}")

    ctx.close()
