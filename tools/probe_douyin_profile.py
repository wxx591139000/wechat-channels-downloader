"""探测抖音「主页作品列表」接口的真实结构（方案 B 前置调研）。

目的：不靠猜，拿到真实 payload 再写实现。

做法：
  1. 用现有 douyin 模块的浏览器配置（系统 Edge + 持久化 profile）
  2. 打开一个抖音用户主页
  3. 拦截所有 JSON 响应，找出「作品列表」那条
  4. 打印结构：条数、字段名、是否含视频地址、有没有分页游标

用法：
  python tools/probe_douyin_profile.py <主页URL>
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sphdl import douyin as DY  # noqa: E402
from sphdl.logger import logger  # noqa: E402

TARGET = sys.argv[1] if len(sys.argv) > 1 else ""

if not TARGET:
    print("用法: python tools/probe_douyin_profile.py <抖音主页URL>")
    print("例:   python tools/probe_douyin_profile.py "
          "https://www.douyin.com/user/MS4wLjABAAAA...")
    sys.exit(1)

OUT = Path("C:/Users/Dancing/AppData/Local/Temp/douyin_probe")
OUT.mkdir(parents=True, exist_ok=True)
print(f"目标主页: {TARGET}")
print(f"输出目录: {OUT}")
print()

from playwright.sync_api import sync_playwright  # noqa: E402

captured = []


def on_response(resp):
    try:
        url = resp.url
        if "douyin.com" not in url:
            return
        ct = (resp.headers or {}).get("content-type", "")
        if "json" not in ct:
            return
        # 只关心可能含作品列表的接口
        keys = ("aweme/post", "aweme/v1/web/aweme/post", "user/post",
                "profile", "user/detail", "aweme/list")
        if not any(k in url for k in keys):
            return
        body = resp.text()
        if len(body) < 500:
            return
        captured.append({"url": url, "len": len(body), "body": body})
        print(f"  📥 捕获 {len(body):>9,} 字节  {url[:110]}")
    except Exception:
        pass


with sync_playwright() as p:
    br = p.chromium.launch(
        headless=True,
        channel="msedge",
        args=["--disable-blink-features=AutomationControlled",
              "--disable-dev-shm-usage", "--no-first-run"],
    )
    ctx = br.new_context(
        user_agent=DY.UA, locale="zh-CN",
        viewport={"width": 1280, "height": 800},
        extra_http_headers={"Referer": "https://www.douyin.com/"},
    )
    # 复用已有登录态（如果有）
    try:
        prof = DY.profile_dir()
        print(f"profile 目录: {prof}")
        print(f"已登录: {DY.is_logged_in()}")
    except Exception as e:
        print(f"profile 检查失败: {e}")

    page = ctx.new_page()
    page.on("response", on_response)

    print("\n打开主页…")
    page.goto(TARGET, wait_until="domcontentloaded", timeout=60000)
    time.sleep(3)

    # 滚动触发加载更多（验证是否有分页）
    print("滚动 3 次触发加载…")
    for i in range(3):
        page.mouse.wheel(0, 2000)
        time.sleep(2.0)
        print(f"  第 {i+1} 次滚动后，已捕获 {len(captured)} 条接口")

    # 保存
    if captured:
        for idx, c in enumerate(captured):
            fn = OUT / f"resp_{idx:02d}.json"
            fn.write_text(c["body"], encoding="utf-8")
        print(f"\n已保存 {len(captured)} 个响应的原始 body")

        # 分析最大的那个
        big = max(captured, key=lambda x: x["len"])
        print(f"\n{'='*70}")
        print(f"分析最大的响应（{big['len']:,} 字节）")
        print(f"{'='*70}")
        print(f"URL: {big['url'][:200]}")
        try:
            d = json.loads(big["body"])
            print(f"\n顶层键: {list(d.keys())}")
            # 找列表
            for k, v in d.items():
                if isinstance(v, list) and v:
                    print(f"\n  {k}: list，{len(v)} 条")
                    first = v[0]
                    if isinstance(first, dict):
                        print(f"    条目字段({len(first)}个): "
                              f"{sorted(first.keys())[:25]}")
            # 分页游标
            for k in ("has_more", "max_cursor", "cursor", "min_cursor"):
                if k in d:
                    print(f"\n  分页: {k} = {d[k]}")
        except Exception as e:
            print(f"JSON 解析失败: {e}")
            print(f"前 300 字符: {big['body'][:300]}")
    else:
        print("\n❌ 没捕获到任何作品列表接口")
        print("可能原因：需要登录 / URL 不对 / 接口名不在关键词里")

    br.close()
