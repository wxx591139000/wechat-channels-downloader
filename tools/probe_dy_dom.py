"""评估：只用 DOM 能拿到多少抖音视频信息（不依赖失效的 aweme/detail）。

═══ 背景 ═══
抖音已改版：作品页不再调用 aweme/detail（xhr 只有监控接口）。
数据藏在 SSR 里，但结构复杂且不稳定（RENDER_DATA 只有用户信息，
__pace_f 里标题也找不到）。

但 **DOM 里有渲染出来的真实内容**：
  h1  = 33天城市生存攒钱挑战今天是第14天
  video.duration = 2079.6 秒
  video.videoWidth/Height = 576x1024

本脚本系统评估 DOM 能提供什么，作为 aweme/detail 的替代方案。

要验证的字段：
  ① 标题        ② 作者昵称/主页链接    ③ 时长
  ④ 分辨率      ⑤ 视频地址（blob → 需另想办法）  ⑥ 点赞/评论数
  ⑦ 发布日期
"""
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
    time.sleep(7)

    print("=" * 74)
    print("DOM 可提取信息评估")
    print("=" * 74)

    info = pg.evaluate("""() => {
        const out = {};
        const q = (s) => document.querySelector(s);
        const txt = (e) => e ? (e.textContent || '').trim() : null;

        // ① 标题
        out.title = txt(q('h1'));

        // ② 作者：找 /user/ 链接里的第一个非 self
        const uls = [...document.querySelectorAll('a[href*="/user/"]')]
            .filter(e => e.offsetParent && !e.href.includes('/self'))
                .map(e => ({text: txt(e), href: e.getAttribute('href')}))
                .filter(o => o.text && o.text.length > 0 && o.text.length < 30);
        out.authorLinks = uls.slice(0, 3);

        // ③ 视频
        const v = q('video');
        if (v) {
            out.duration = v.duration;
            out.vw = v.videoWidth;
            out.vh = v.videoHeight;
            out.src = (v.src || '').slice(0, 100);
            out.currentSrc = (v.currentSrc || '').slice(0, 100);
            // source 子元素
            const srcs = [...v.querySelectorAll('source')].map(s => s.src.slice(0,100));
            out.sources = srcs;
        }

        // ④ 数字统计（点赞/评论/收藏/分享）：找带「万」或纯数字的元素
        const statEls = [...document.querySelectorAll('span,div')]
            .filter(e => e.offsetParent)
            .map(e => txt(e))
            .filter(t => t && /^(\\d+\\.?\\d*[万wW]?|\\d+)$/.test(t) && t.length < 10);
        out.numbers = statEls.slice(0, 10);

        // ⑤ 日期
        const dates = [...document.querySelectorAll('span,div')]
            .filter(e => e.offsetParent)
            .map(e => txt(e))
            .filter(t => t && /^\\d{4}[-\\/年]\\d{1,2}[-\\/月]\\d{1,2}/.test(t));
        out.dates = dates.slice(0, 3);

        // ⑥ 页面文本里找「@xxx」
        const bodyTxt = document.body.innerText;
        const ats = bodyTxt.match(/@[\\u4e00-\\u9fa5\\w\\-\\.]{2,20}/g) || [];
        out.ats = [...new Set(ats)].slice(0, 5);

        // ⑦ 描述区域（标题下方那段）
        const descCandidates = [...document.querySelectorAll(
            '[data-e2e*="desc"], [class*="desc"]')]
            .filter(e => e.offsetParent)
            .map(e => txt(e)).filter(t => t && t.length > 5);
        out.descCandidates = descCandidates.slice(0, 3);

        return out;
    }""")

    for k, v in info.items():
        print(f"\n  {k}:")
        if isinstance(v, list):
            for x in v:
                print(f"      {x}")
        elif isinstance(v, dict):
            for kk, vv in v.items():
                print(f"      {kk} = {vv}")
        else:
            print(f"      {v}")

    # 视频时长格式化
    if info.get("duration"):
        d = info["duration"]
        print(f"\n  → 时长: {int(d)//60}分{int(d)%60}秒")

    ctx.close()

print()
print("=" * 74)
print("结论")
print("=" * 74)
got = []
if info.get("title"):
    got.append("标题")
if info.get("authorLinks"):
    got.append("作者")
if info.get("duration"):
    got.append("时长")
if info.get("vw"):
    got.append("分辨率")
if info.get("numbers"):
    got.append("数字统计(点赞等,需验证归属)")
print(f"  DOM 能拿到: {', '.join(got) if got else '（无）'}")
print(f"  DOM 拿不到: 直接下载地址（video.src 是 blob:，需从网络请求里取）")
