"""在页面上下文里自己调 aweme/post 接口（复用页面的签名能力）。

═══ 为什么这条路可行 ═══
抖音作品列表请求需要 `a_bogus` 签名（页面 JS 算的）。
但从**页面上下文内部**发起 fetch，页面里的签名逻辑会自动生效
（因为请求走的是同一个 JS 环境和同一个 window）。

实测验证：
  1. 打开任意抖音页面（有登录态）
  2. 在页面里 fetch('/aweme/v1/web/aweme/post/?sec_user_id=...&max_cursor=...')
  3. 看是否返回 aweme_list

如果成功 → 可以按 cursor 循环翻页，一次拿完全部作品，
不依赖滚动（滚动不可靠是本次踩的最大的坑）。
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
        except Exception:
            continue
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()

    # 先进抖音首页（建立 session / cookie / 签名环境）
    print("打开抖音首页建立环境…")
    try:
        pg.goto("https://www.douyin.com/", wait_until="commit", timeout=45000)
    except Exception as e:
        print(f"  （导航未完成 {type(e).__name__}，继续）")
    time.sleep(6)

    print()
    print("=" * 74)
    print("在页面上下文里直接 fetch aweme/post")
    print("=" * 74)

    # 先试第一页
    result = pg.evaluate("""async (sec) => {
        const qs = new URLSearchParams({
            device_platform: 'webapp',
            aid: '6383',
            channel: 'channel_pc_web',
            sec_user_id: sec,
            max_cursor: '0',
            count: '18',
            publish_video_strategy_type: '2',
            update_version_code: '170400',
            pc_client_type: '1',
            version_code: '170400',
            version_name: '17.4.0',
            cookie_enabled: 'true',
            screen_width: '1400',
            screen_height: '900',
            browser_language: 'zh-CN',
            browser_platform: 'Win32',
            browser_name: 'Edge',
            browser_version: '120.0.0.0',
        });
        const url = '/aweme/v1/web/aweme/post/?' + qs.toString();
        try {
            const r = await fetch(url, {credentials: 'include'});
            const txt = await r.text();
            let j = null;
            try { j = JSON.parse(txt); } catch(e) {}
            return {
                status: r.status,
                len: txt.length,
                head: txt.slice(0, 200),
                ok: !!j,
                status_code: j ? j.status_code : null,
                n: j && j.aweme_list ? j.aweme_list.length : 0,
                has_more: j ? j.has_more : null,
                max_cursor: j ? j.max_cursor : null,
            };
        } catch(e) {
            return {error: String(e)};
        }
    }""", SEC)

    for k, v in result.items():
        print(f"  {k}: {str(v)[:120]}")

    ok = result.get("n", 0) > 0
    print()
    print(f"第一页结果: {'✅ 拿到 ' + str(result.get('n')) + ' 条' if ok else '❌ 失败'}")

    if ok:
        # 试翻页
        print()
        print("=" * 74)
        print("试第二页（用 max_cursor）")
        print("=" * 74)
        cur = result.get("max_cursor")
        r2 = pg.evaluate("""async (args) => {
            const [sec, cursor] = args;
            const qs = new URLSearchParams({
                device_platform: 'webapp', aid: '6383',
                channel: 'channel_pc_web', sec_user_id: sec,
                max_cursor: String(cursor), count: '18',
                publish_video_strategy_type: '2',
                update_version_code: '170400', pc_client_type: '1',
                version_code: '170400', version_name: '17.4.0',
                cookie_enabled: 'true', screen_width: '1400',
                screen_height: '900', browser_language: 'zh-CN',
                browser_platform: 'Win32', browser_name: 'Edge',
                browser_version: '120.0.0.0',
            });
            try {
                const r = await fetch('/aweme/v1/web/aweme/post/?' + qs.toString(),
                                      {credentials: 'include'});
                const j = await r.json();
                return {
                    status_code: j.status_code,
                    n: j.aweme_list ? j.aweme_list.length : 0,
                    has_more: j.has_more,
                    max_cursor: j.max_cursor,
                    first_id: j.aweme_list && j.aweme_list[0]
                              ? j.aweme_list[0].aweme_id : null,
                    first_desc: j.aweme_list && j.aweme_list[0]
                              ? (j.aweme_list[0].desc || '').slice(0, 40) : null,
                };
            } catch(e) {
                return {error: String(e)};
            }
        }""", [SEC, cur])
        for k, v in r2.items():
            print(f"  {k}: {str(v)[:100]}")

        if r2.get("n", 0) > 0:
            print()
            print("✅✅ 翻页成功！可以按 cursor 循环拿完全部作品")
            print("     → 不需要滚动，不受虚拟列表影响")
        else:
            print()
            print("⚠ 翻页失败 —— 可能需要 a_bogus 参数，只用页面 fetch 不够")

    ctx.close()
