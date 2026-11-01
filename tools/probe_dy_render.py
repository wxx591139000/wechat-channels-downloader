"""解析抖音 RENDER_DATA，提取完整视频元数据。

═══ 发现（实测 2026-11-01）═══
抖音作品页把数据塞在 `<script id="RENDER_DATA">` 里（URL 编码的 JSON，215KB）。
这是 SSR 数据，**不依赖任何 xhr 接口** —— 比抓 aweme/detail 更稳定。

已知包含字段：desc / nickname / duration
待确认：play_addr（下载地址）、aweme_id、statistics（点赞等）

本脚本解析并列出完整可提取字段。
"""
import json
import sys
import time
import urllib.parse
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

    raw = pg.evaluate("""() => {
        const el = document.getElementById('RENDER_DATA');
        return el ? el.textContent : null;
    }""")
    ctx.close()

if not raw:
    print("❌ 没找到 RENDER_DATA")
    sys.exit(1)

print(f"RENDER_DATA 原始长度: {len(raw)}")
decoded = urllib.parse.unquote(raw)
print(f"URL 解码后长度: {len(decoded)}")
Path("C:/Users/Dancing/AppData/Local/Temp/dy_render.json").write_text(
    decoded, encoding="utf-8")

try:
    d = json.loads(decoded)
except Exception as e:
    print(f"JSON 解析失败: {e}")
    sys.exit(1)

print(f"顶层键: {list(d.keys())}")
print()


def walk(o, path="", depth=0, hits=None):
    """找出所有含目标字段的路径。"""
    if hits is None:
        hits = []
    if depth > 12:
        return hits
    if isinstance(o, dict):
        for k, v in o.items():
            p2 = f"{path}.{k}" if path else k
            if k in ("desc", "nickname", "duration", "aweme_id",
                     "play_addr", "statistics", "digg_count",
                     "video_id", "uri", "url_list"):
                hits.append((p2, type(v).__name__,
                             str(v)[:70] if not isinstance(v, (dict, list))
                             else f"{type(v).__name__}({len(v)})"))
            walk(v, p2, depth + 1, hits)
    elif isinstance(o, list):
        for i, v in enumerate(o[:3]):
            walk(v, f"{path}[{i}]", depth + 1, hits)
    return hits


hits = walk(d)
print(f"找到 {len(hits)} 个目标字段：")
seen_prefix = set()
for path, typ, val in hits:
    # 只显示前几个不同前缀的，避免刷屏
    prefix = ".".join(path.split(".")[:-2])
    if prefix in seen_prefix:
        continue
    seen_prefix.add(prefix)
    print(f"  {path}")
    print(f"      类型={typ} 值={val}")
    if len(seen_prefix) > 25:
        print("  ...（省略）")
        break

# 重点：找 video 对象
print()
print("=" * 74)
print("查找 video 对象（含下载地址）")
print("=" * 74)


def find_video_objs(o, path="", out=None, depth=0):
    if out is None:
        out = []
    if depth > 12 or len(out) > 5:
        return out
    if isinstance(o, dict):
        # 典型 video 对象：有 play_addr 或 bit_rate
        if "play_addr" in o or ("bit_rate" in o and "duration" in o):
            out.append((path, o))
        for k, v in o.items():
            find_video_objs(v, f"{path}.{k}" if path else k, out, depth + 1)
    elif isinstance(o, list):
        for i, v in enumerate(o[:3]):
            find_video_objs(v, f"{path}[{i}]", out, depth + 1)
    return out


vids = find_video_objs(d)
print(f"找到 {len(vids)} 个 video 对象")
for path, v in vids[:3]:
    print(f"\n  路径: {path}")
    print(f"  键: {sorted(v.keys())[:15]}")
    pa = v.get("play_addr") or {}
    print(f"  play_addr.url_list: {(pa.get('url_list') or [])[:1]}")
    print(f"  duration: {v.get('duration')}")
    print(f"  data_size: {pa.get('data_size')}")
    print(f"  width/height: {pa.get('width')}/{pa.get('height')}")

# 找 aweme 主体
print()
print("=" * 74)
print("查找 aweme 主体（desc + author）")
print("=" * 74)


def find_aweme(o, path="", out=None, depth=0):
    if out is None:
        out = []
    if depth > 12 or len(out) > 5:
        return out
    if isinstance(o, dict):
        if "desc" in o and ("author" in o or "video" in o):
            out.append((path, o))
        for k, v in o.items():
            find_aweme(v, f"{path}.{k}" if path else k, out, depth + 1)
    elif isinstance(o, list):
        for i, v in enumerate(o[:3]):
            find_aweme(v, f"{path}[{i}]", out, depth + 1)
    return out


aws = find_aweme(d)
print(f"找到 {len(aws)} 个 aweme 主体")
for path, a in aws[:2]:
    print(f"\n  路径: {path}")
    print(f"  desc: {str(a.get('desc'))[:70]}")
    print(f"  aweme_id: {a.get('aweme_id')}")
    print(f"  duration: {a.get('duration')}")
    au = a.get("author") or {}
    print(f"  author.nickname: {au.get('nickname')}")
    st = a.get("statistics") or {}
    print(f"  statistics: digg={st.get('digg_count')} "
          f"comment={st.get('comment_count')} share={st.get('share_count')}")
