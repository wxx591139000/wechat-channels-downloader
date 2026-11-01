"""验证「DOM 元数据 + 网络请求地址」组合方案能否完整替代 aweme/detail。

═══ 背景 ═══
抖音已改版，aweme/detail 接口不再被调用。但实测发现：

| 数据 | 来源 | 状态 |
|---|---|---|
| 标题 | DOM `h1` | ✅ |
| 作者 | DOM `@昵称` / a[href*=user] | ✅ |
| 时长 | `video.duration` | ✅ |
| 分辨率 | `video.videoWidth/Height` | ✅ |
| 点赞等 | DOM 数字（需确认归属） | ⚠️ |
| **下载地址** | **网络请求里的 douyinvod.com mp4** | ✅（需抓 response） |

本脚本验证组合方案：边读 DOM，边拦网络请求取地址，看能否拼出完整信息。
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

media_urls = []


def on_response(r):
    try:
        u = r.url
        ct = (r.headers or {}).get("content-type", "")
        # 视频流：mp4 或 mime 含 video
        if "video" in ct or ".mp4" in u.lower():
            if "douyinvod" in u or "byteimg" in u or "douyin" in u:
                media_urls.append({
                    "url": u,
                    "ct": ct,
                    "len": r.headers.get("content-length"),
                    "status": r.status,
                    "rtype": r.request.resource_type,
                })
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
        except Exception:
            continue
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    pg.on("response", on_response)

    print(f"打开 https://www.douyin.com/video/{AID}")
    pg.goto(f"https://www.douyin.com/video/{AID}",
            wait_until="domcontentloaded", timeout=60000)
    time.sleep(7)

    # 尝试让视频真正开始播放（触发完整流请求）
    print("尝试点击播放…")
    try:
        pg.evaluate("""() => {
            const v = document.querySelector('video');
            if (v) { v.muted = true; v.play().catch(()=>{}); }
        }""")
        time.sleep(4)
    except Exception as e:
        print(f"  播放失败: {e}")

    # ══════ DOM 元数据 ══════
    meta = pg.evaluate("""() => {
        const out = {};
        const q = (s) => document.querySelector(s);
        const txt = (e) => e ? (e.textContent || '').trim() : null;
        out.title = txt(q('h1'));
        const v = q('video');
        if (v) {
            out.duration = v.duration;
            out.vw = v.videoWidth;
            out.vh = v.videoHeight;
        }
        // 作者
        const uls = [...document.querySelectorAll('a[href*="/user/"]')]
            .filter(e => e.offsetParent && !e.href.includes('/self'))
            .map(e => ({t: txt(e), h: e.getAttribute('href')}))
            .filter(o => o.t && o.t.length > 0 && o.t.length < 30);
        out.author = uls.length ? uls[0] : null;
        // @昵称
        const m = document.body.innerText.match(/@([\\u4e00-\\u9fa5\\w\\-\\.]{2,20})/);
        out.atName = m ? m[1] : null;
        // 统计数字（点赞/评论/收藏/分享，按 DOM 顺序）
        // 抖音这些数字通常在 aria-label 或特定容器里
        const statBox = [...document.querySelectorAll('[aria-label]')]
            .map(e => e.getAttribute('aria-label'))
            .filter(t => t && /(赞|评论|收藏|分享|喜欢)/.test(t));
        out.statLabels = statBox.slice(0, 8);
        return out;
    }""")
    ctx.close()

print()
print("=" * 74)
print("① DOM 元数据")
print("=" * 74)
for k, v in meta.items():
    print(f"  {k}: {v}")
if meta.get("duration"):
    d = meta["duration"]
    print(f"  → 时长: {int(d)//60}分{int(d)%60}秒")

print()
print("=" * 74)
print(f"② 网络视频流请求（{len(media_urls)} 个）")
print("=" * 74)
for m in media_urls[:8]:
    print(f"  [{m['status']}] {m['rtype']:10s} {m['ct'][:30]:32s} "
          f"len={m['len']}")
    print(f"      {m['url'][:130]}")

# 选出最像完整视频的地址
candidates = [m for m in media_urls
              if "video" in m["ct"] and m["status"] in (200, 206)]
print()
print(f"可用视频地址: {len(candidates)} 个")

if candidates:
    best = max(candidates, key=lambda x: int(x["len"] or 0))
    print(f"  最大的一个: {int(best['len'] or 0):,} 字节")
    print(f"  {best['url'][:140]}")
    Path("C:/Users/Dancing/AppData/Local/Temp/dy_best_url.txt").write_text(
        best["url"], encoding="utf-8")

    # 试下载前 1MB 验证可用
    print()
    print("验证地址可下载（取前 512KB）…")
    try:
        import urllib.request
        req = urllib.request.Request(
            best["url"], headers={
                "User-Agent": UA,
                "Referer": "https://www.douyin.com/",
                "Range": "bytes=0-524287"})
        with urllib.request.urlopen(req, timeout=25) as r:
            data = r.read()
            print(f"  ✅ HTTP {r.status}  收到 {len(data):,} 字节  "
                  f"content-type={r.headers.get('content-type')}")
            # 检查是不是真 mp4（ftyp box）
            is_mp4 = b"ftyp" in data[:64]
            print(f"  是有效 MP4: {'✅' if is_mp4 else '❌'}  "
                  f"前 32 字节: {data[:32].hex()}")
    except Exception as e:
        print(f"  ❌ 下载失败: {str(e)[:100]}")

print()
print("=" * 74)
print("结论")
print("=" * 74)
print(f"  标题: {'✅ ' + str(meta.get('title'))[:40] if meta.get('title') else '❌'}")
print(f"  作者: {'✅ ' + str(meta.get('atName') or (meta.get('author') or {}).get('t')) if (meta.get('atName') or meta.get('author')) else '❌'}")
print(f"  时长: {'✅ ' + str(int(meta.get('duration', 0))) + ' 秒' if meta.get('duration') else '❌'}")
print(f"  分辨率: {'✅ ' + str(meta.get('vw')) + 'x' + str(meta.get('vh')) if meta.get('vw') else '❌'}")
print(f"  下载地址: {'✅ 有 ' + str(len(candidates)) + ' 个' if candidates else '❌ 没抓到'}")
