"""从抖音作品页的 SSR 数据里提取视频信息。

═══ 发现（实测 2026-11-01）═══
抖音作品页**不再调用 aweme/detail 接口**（xhr 只有 2 个监控接口），
但页面 DOM 里有完整标题：`h1 = 33天城市生存攒钱挑战今天是第14天`

页面全局变量里发现可疑项：
  SSR_RENDER_DATA
  initialRscFlightDataEnd
  EXPOSE_DATA

本脚本要确认：**视频元数据是不是就在 SSR_RENDER_DATA 里**。
如果在 → 直接从 SSR 读，不依赖任何接口（最稳定）。
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

from playwright.sync_api import sync_playwright  # noqa: E402

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
    pg.goto(f"https://www.douyin.com/video/{AID}",
            wait_until="domcontentloaded", timeout=60000)
    time.sleep(6)

    print("=" * 74)
    print("检查页面全局数据源")
    print("=" * 74)
    info = pg.evaluate("""() => {
        const out = {};
        for (const k of ['SSR_RENDER_DATA','_ROUTER_DATA','__INIT_PROPS__',
                         'EXPOSE_DATA','abTestData','initialRscFlightDataEnd']) {
            try {
                const v = window[k];
                out[k] = (typeof v === 'undefined') ? 'undefined'
                        : (typeof v === 'string' ? `string(${v.length})`
                        : (v === null ? 'null' : typeof v));
            } catch(e) { out[k] = 'error'; }
        }
        return out;
    }""")
    for k, v in info.items():
        print(f"  {k}: {v}")

    # 找含 aweme 数据的脚本标签（SSR 常把数据塞 <script> 里）
    print()
    print("=" * 74)
    print("扫描 <script> 标签里的数据")
    print("=" * 74)
    scripts = pg.evaluate("""() => {
        const out = [];
        for (const s of document.querySelectorAll('script')) {
            const t = s.textContent || '';
            if (t.length > 2000 && /aweme|video|desc/i.test(t)) {
                out.push({id: s.id || '', len: t.length,
                          head: t.slice(0, 120)});
            }
        }
        return out;
    }""")
    for s in scripts[:8]:
        print(f"  len={s['len']:>9,} id={s['id']!r}  {s['head'][:80]!r}")

    # 直接尝试从 SSR_RENDER_DATA 里挖
    print()
    print("=" * 74)
    print("尝试从 SSR_RENDER_DATA 提取视频信息")
    print("=" * 74)
    ssr = pg.evaluate("""() => {
        try {
            const raw = window.SSR_RENDER_DATA;
            if (!raw) return null;
            return typeof raw === 'string' ? raw : JSON.stringify(raw);
        } catch(e) { return null; }
    }""")
    if ssr:
        print(f"  SSR_RENDER_DATA 长度: {len(ssr)}")
        Path("C:/Users/Dancing/AppData/Local/Temp/dy_ssr.json").write_text(
            ssr, encoding="utf-8")
        for kw in ('"desc"', '"nickname"', '"duration"', '"aweme_id"',
                   '"play_addr"', '"video_id"'):
            print(f"    含 {kw}: {kw in ssr}")
        try:
            d = json.loads(ssr)
            print(f"  顶层键: {list(d.keys())[:15]}")
        except Exception as e:
            print(f"  不是纯 JSON: {e}")
            print(f"  前 200 字符: {ssr[:200]}")
    else:
        print("  SSR_RENDER_DATA 为空/不存在")

    # DOM 兜底能力评估
    print()
    print("=" * 74)
    print("DOM 兜底：能读到什么")
    print("=" * 74)
    dom = pg.evaluate("""() => {
        const out = {};
        const h1 = document.querySelector('h1');
        out.title = h1 ? h1.textContent.trim() : null;

        // 作者：找带 @ 或用户链接
        const userLinks = [...document.querySelectorAll('a[href*="/user/"]')]
            .filter(e => e.offsetParent);
        out.userLinks = userLinks.slice(0,3).map(e => ({
            text: e.textContent.trim().slice(0,30),
            href: e.getAttribute('href').slice(0,60)
        }));

        // 视频元素
        const v = document.querySelector('video');
        out.video = v ? {src: (v.src||'').slice(0,80),
                         duration: v.duration, w: v.videoWidth,
                         h: v.videoHeight} : null;

        // 时间
        const timeEls = [...document.querySelectorAll('span,div')]
            .filter(e => /^\\d{4}-\\d{2}-\\d{2}/.test((e.textContent||'').trim()));
        out.dateText = timeEls.length ? timeEls[0].textContent.trim().slice(0,30) : null;

        return out;
    }""")
    for k, v in dom.items():
        print(f"  {k}: {v}")

    ctx.close()
