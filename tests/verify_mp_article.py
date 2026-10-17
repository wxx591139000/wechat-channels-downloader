#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公众号文章视频解析测试（v2.0.19 新增模块）。

覆盖：
  M1 离线解析（多清晰度合并 / 去重 / 实体转义 / 标题 / 文件名）
  M2 ★转义坑回归：\\x26amp; 形式不能被截断（否则 CDN 403）
  M3 URL 判定（公众号 vs 视频号 vs 空）
  M4 联网实测（真实文章：视频数 / 参数完整 / 能下载）

跑法：python tests/verify_mp_article.py
     加 --offline 跳过联网部分
"""
import sys
import tempfile
import urllib.parse as up
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sphdl import mp_article as mp  # noqa: E402

RESULTS = []
OFFLINE = "--offline" in sys.argv
REAL_URL = "https://mp.weixin.qq.com/s/BAvjuF4k6BLWlyCHTY58xA"


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


# ---------- M1 离线解析 ----------
print("=" * 74)
print("M1 离线解析：多清晰度合并 / 去重 / 实体转义")
print("=" * 74)
SAMPLE = '''
<html><head><title>测试文章标题</title></head><body>
<video src="https://mpvideo.qpic.cn/aaa.f10002.mp4?dis_k=k1&amp;auth_key=a1"
       poster="https://x/p1.jpg"></video>
<video src="https://mpvideo.qpic.cn/aaa.f10104.mp4?dis_k=k2&amp;auth_key=a2"></video>
<div data-src="https://mpvideo.qpic.cn/bbb.f10002.mp4?dis_k=k3"></div>
<script>var v="https://mpvideo.qpic.cn/ccc.f10002.mp4?dis_k=k4\\x26amp;dis_t=123\\x26amp;auth_key=a9";</script>
<video src="https://mpvideo.qpic.cn/aaa.f10002.mp4?dis_k=k1&amp;auth_key=a1"></video>
</body></html>'''
vs = mp.parse_article(SAMPLE)
check("M1a 合并后 3 个视频（aaa/bbb/ccc）", len(vs) == 3, f"got={len(vs)}")
vids = [v.vid for v in vs]
check("M1b vid 取文件名主干", vids == ["aaa", "bbb", "ccc"], str(vids))
check("M1c aaa 有 2 档清晰度（f10002/f10104）",
      len(vs[0].renditions) == 2, f"{len(vs[0].renditions)} 档")
check("M1d 默认取最高清晰度（f10104）",
      "f10104" in vs[0].url, vs[0].url[-40:])
check("M1e 清晰度有中文标签",
      any(lbl in ("蓝光", "超清") for _f, _u, lbl in vs[0].renditions),
      str([lbl for _f, _u, lbl in vs[0].renditions]))
check("M1f 封面已提取", vs[0].poster.endswith("p1.jpg"), vs[0].poster)
check("M1g HTML 实体已解码（无 &amp;）",
      all("&amp;" not in v.url for v in vs))
check("M1h 重复链接被去重（aaa 只出现 1 次）", vids.count("aaa") == 1)
check("M1i 标题提取正确", mp.article_title(SAMPLE) == "测试文章标题",
      mp.article_title(SAMPLE))

# ---------- M2 转义坑回归 ----------
print("\n" + "=" * 74)
print("M2 ★转义坑回归：\\x26amp; 形式不能被截断（否则 CDN 403）")
print("=" * 74)
# 这是 2026-10-17 实测踩到的真实坑：正则若排除反斜杠，URL 会在 \x26 处截断
ESC_SAMPLE = (
    '<script>var v="https://mpvideo.qpic.cn/xxx.f10002.mp4?dis_k=AAA'
    '\\x26amp;dis_t=1792203001\\x26amp;play_scene=10120'
    '\\x26amp;auth_info=BBB==\\x26amp;auth_key=CCC";</script>')
vs2 = mp.parse_article(ESC_SAMPLE)
check("M2a 解析出 1 个视频", len(vs2) == 1, f"got={len(vs2)}")
if vs2:
    u = vs2[0].url
    params = dict(up.parse_qsl(up.urlparse(u).query))
    check("M2b ★参数数 = 5（未被截断）", len(params) == 5,
          f"got={len(params)}: {list(params)}")
    check("M2c ★含 auth_key", "auth_key" in params and params["auth_key"] == "CCC",
          params.get("auth_key", "(缺)"))
    check("M2d ★含 auth_info", params.get("auth_info") == "BBB==",
          params.get("auth_info", "(缺)"))
    check("M2e ★含 dis_t / play_scene",
          params.get("dis_t") == "1792203001" and params.get("play_scene") == "10120")
    check("M2f URL 里无 \\x26 残留",
          "\\x26" not in u, "已清理" if "\\x26" not in u else "仍残留")

# _clean 单测
check("M2g _clean 处理 \\x26amp;",
      mp._clean("a=1\\x26amp;b=2") == "a=1&b=2",
      mp._clean("a=1\\x26amp;b=2"))
check("M2h _clean 处理双重转义 &amp;amp;",
      mp._clean("a=1&amp;amp;b=2") == "a=1&b=2",
      mp._clean("a=1&amp;amp;b=2"))
check("M2i _clean 处理 \\u0026",
      mp._clean("a=1\\u0026b=2") == "a=1&b=2",
      mp._clean("a=1\\u0026b=2"))

# ---------- M3 URL 判定 ----------
print("\n" + "=" * 74)
print("M3 URL 判定")
print("=" * 74)
check("M3a 公众号 /s/ 链接识别", mp.is_mp_article_url(REAL_URL))
check("M3b 公众号 /s?__biz 链接识别",
      mp.is_mp_article_url("https://mp.weixin.qq.com/s?__biz=MzA5&mid=1"))
check("M3c 视频号链接不误判",
      not mp.is_mp_article_url("https://channels.weixin.qq.com/web/pages/home"))
check("M3d 空串不误判", not mp.is_mp_article_url(""))
check("M3e None 不误判", not mp.is_mp_article_url(None))
check("M3f 文件名去非法字符", mp.safe_filename('a/b:c*d?"e') == "a_b_c_d__e",
      mp.safe_filename('a/b:c*d?"e'))
check("M3g 空标题兜底", mp.safe_filename("") == "video")

# ---------- M4 联网实测 ----------
print("\n" + "=" * 74)
print("M4 联网实测（真实文章）")
print("=" * 74)
if OFFLINE:
    print("  （--offline：跳过）")
else:
    try:
        h = mp.fetch_article(REAL_URL)
        check("M4a 抓取文章页成功", len(h) > 100000, f"{len(h)} 字节")
        title = mp.article_title(h)
        check("M4b 标题非空且不是默认值",
              bool(title) and title != "公众号视频", title[:40])
        rv = mp.parse_article(h)
        check("M4c ★视频数 = 3", len(rv) == 3, f"got={len(rv)}")
        if rv:
            check("M4d 每个视频有多档清晰度",
                  all(len(v.renditions) >= 2 for v in rv),
                  str([len(v.renditions) for v in rv]))
            fid, url, label = rv[0].renditions[0]
            params = dict(up.parse_qsl(up.urlparse(url).query))
            check("M4e ★真实链接参数完整（5 个）", len(params) == 5,
                  f"{list(params)}")
            # 真下载前 4KB 验证可下
            import urllib.request
            req = urllib.request.Request(
                url, headers={"User-Agent": mp.UA,
                              "Referer": "https://mp.weixin.qq.com/"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    d = r.read(4096)
                    check("M4f ★真实视频可下载（HTTP 200）", r.status == 200,
                          f"{r.status} {r.headers.get('Content-Type')}")
                    check("M4g 返回合法 MP4 容器", d[4:8] == b"ftyp",
                          str(d[4:8]))
                    cl = int(r.headers.get("Content-Length") or 0)
                    check("M4h 文件大小合理（>1MB）", cl > 1024 * 1024,
                          f"{cl/1024/1024:.1f} MB")
            except Exception as e:  # noqa: BLE001
                check("M4f ★真实视频可下载（HTTP 200）", False,
                      f"{type(e).__name__}: {e}")
    except Exception as e:  # noqa: BLE001
        check("M4a 抓取文章页成功", False, f"{type(e).__name__}: {e}")

print()
ok = sum(1 for _, p, _ in RESULTS if p)
print(f"RESULT: {'ALL PASS' if ok == len(RESULTS) else 'FAIL'} ({ok}/{len(RESULTS)})")
for n, p, _ in RESULTS:
    if not p:
        print("  未通过: " + n)
sys.exit(0 if ok == len(RESULTS) else 1)
