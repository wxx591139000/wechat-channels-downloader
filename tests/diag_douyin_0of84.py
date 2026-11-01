"""诊断：抖音主页抓到了 84 条，但逐条解析全部失败（成功 0/84）。

═══ 客户实测 ═══
「刚试了抖音解析博主，显示解析完成，成功 0/84 条」

含义：
  · 主页批量抓取（方案B）成功 → 84 条元数据拿到了
  · 但把这 84 条「解析成可下载」时全部失败
  · 说明问题在 resolve_many（逐条 workspace 解析）这一环

可能原因：
  ① 抓到的 play_url 是简化的（我在 _slim 里只取了 url_list[0]），
     这个地址可能不是最长有效期的那条
  ② 抖音对 worker 里的独立页面解析做了限流
  ③ 84 条一次解析太多，触发了频控

本脚本：
  1. 先跑一次主页抓取，看拿到的 play_url 有效性
  2. 取前 3 条，直接用 urllib 测能否下载（绕过 resolve_many）
  3. 测 resolve_many 对单条的解析
"""
import sys
import time
from pathlib import Path

ROOT = Path(r"E:/myHermesWorkSpace/wechat-channels-downloader/sphdl")
sys.path.insert(0, str(ROOT))

from sphdl.douyin_profile_scroll import fetch_profile_by_scroll  # noqa: E402

SEC = "MS4wLjABAAAAAgTdaUkW9HoblheZJ-W44LQ-5YkSC2pbNbssIgKa8tg"
URL = f"https://www.douyin.com/user/{SEC}"

print("=" * 74)
print("① 主页抓取（取 5 条验证）")
print("=" * 74)
res = fetch_profile_by_scroll(URL, max_items=5, visible=False,
                              auto_scroll=True,
                              on_log=lambda lv, m: None)
print(f"抓到 {len(res.items)} 条  说明={res.note}  错误={res.error}")

if not res.items:
    print("❌ 没抓到作品，先解决抓取问题")
    sys.exit(1)

print()
for i, v in enumerate(res.items[:5]):
    print(f"[{i}] {v.title[:34]:36s} {v.duration_text:>8s}")
    print(f"     play_url: {v.play_url[:100]}")
    print(f"     audio_url: {getattr(v, 'audio_url', '')[:60] or '（无）'}")

print()
print("=" * 74)
print("② 直接用 play_url 测下载（绕过 resolve_many）")
print("=" * 74)
from sphdl.douyin import UA  # noqa: E402
import urllib.request  # noqa: E402

for i, v in enumerate(res.items[:3]):
    try:
        req = urllib.request.Request(
            v.play_url,
            headers={"User-Agent": UA,
                     "Referer": "https://www.douyin.com/"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read(4096)
            ct = r.headers.get("content-type", "")
            print(f"  [{i}] HTTP {r.status}  {ct[:30]:32s} "
                  f"前 4KB 有效 MP4={b'ftyp' in data[:64]}")
    except Exception as e:
        print(f"  [{i}] ❌ {type(e).__name__}: {str(e)[:80]}")

print()
print("=" * 74)
print("③ resolve_many 解析单条（复现客户流程）")
print("=" * 74)
from sphdl.douyin import resolve_many  # noqa: E402

# 需要 aweme_id 才能走 detail 路径
print("注：resolve_many 接受的是 URL，主页抓到的条目没有 URL")
print(f"  条目的 aweme_id: {[v.aweme_id for v in res.items[:3]]}")
print()
print("★ 关键：客户流程是「抓主页 → 得到 84 条 → 逐条 resolve_many(URL)")
print("  但我们只有 play_url，没有可解析的网页 URL！")
print()
urls = [f"https://www.douyin.com/video/{v.aweme_id}" for v in res.items[:2]
        if v.aweme_id]
print(f"用 aweme_id 构造 URL 测试: {urls}")

if urls:
    out = []

    def on_item(i, u, v):
        out.append((u, v))

    t0 = time.time()
    resolve_many(urls, on_item=on_item,
                 on_log=lambda lv, m: print(f"    [{lv}] {m}"))
    print(f"\n  耗时 {time.time()-t0:.1f}s")
    for u, v in out:
        ok = getattr(v, "ok", False)
        print(f"  {'✅' if ok else '❌'} {u[:60]}")
        if not ok:
            print(f"       error={getattr(v, 'error', '')[:100]}")
