#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公众号文章视频下载（v2.0.19 新增附加模块）。

产品定位
--------
**附加功能，默认关闭**，需专门的授权码开启（功能位 FLAG_MP_ARTICLE_VIDEO）。
与视频号下载完全独立：不同域名、不同抓取方式、不同授权位。

为什么单独做
------------
视频号（finder.video.qq.com）走加密流 + 代理嗅探 + 解密；
公众号文章视频（mpvideo.qpic.cn）是**明文 MP4 直链**，只要解析页面即可。
两者技术栈不同，混在嗅探流程里会互相污染，故独立成模块。

技术要点（2026-10-17 实测）
--------------------------
· 文章页 HTML 里 `<video>` 标签的 src 就是可直下的 mp4：
      https://mpvideo.qpic.cn/0bc3xxx.f10002.mp4?dis_k=..&auth_key=..
· **无需解密、无需代理**，直接 HTTP 下载。
· 链接带 auth_key 有**时效**，过期需重新解析文章页 —— 故下载前应
  重新抓一次页面，而不是缓存链接。
· 一篇文章可能有多个视频（实测某文 3 个），全部列出供用户选择。
"""
import html
import json
import re
import urllib.request
from typing import List, NamedTuple


class MpVideo(NamedTuple):
    """公众号文章里的一个视频（**同一视频的多个清晰度已合并**）。"""
    url: str            # 默认/最高清晰度直链（带 auth_key，有时效）
    vid: str            # wxv_xxx 稳定标识（URL 参数里的 vid，非文件名）
    title: str = ""     # 标题（多数情况为空）
    poster: str = ""    # 封面图
    renditions: tuple = ()   # 全部清晰度 [(format_id, url, 说明)]，供选择


# 清晰度格式档（公众号视频常见 4 档；数字越大越清晰）
_QUALITY_HINT = {
    "f10002": "标清", "f10003": "标清",
    "f10004": "高清", "f10102": "超清", "f10104": "蓝光",
}


def _format_id(u: str) -> str:
    m = re.search(r"\.(f\d{5})\.mp4", u or "")
    return m.group(1) if m else ""


def _vid_of(u: str) -> str:
    """取 vid。优先 URL 参数 vid=wxv_xxx；否则用文件名主干（同视频各清晰度共享）。"""
    m = re.search(r"[?&]vid=(wxv_[0-9A-Za-z]+)", u or "")
    if m:
        return m.group(1)
    m = re.search(r"/([0-9a-z]+)\.f\d{5}\.mp4", u or "")
    return m.group(1) if m else (u or "").split("?")[0]


UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def is_mp_article_url(url: str) -> bool:
    """判断是否公众号文章链接（mp.weixin.qq.com/s/... 或 /s?__biz=...）。"""
    u = (url or "").strip()
    return bool(re.match(r"^https?://mp\.weixin\.qq\.com/s[/?]", u))


def fetch_article(url: str, timeout: float = 20.0) -> str:
    """抓文章页 HTML。返回空串表示失败。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    # 公众号页面多为 utf-8；容错处理
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _clean(u: str) -> str:
    """清洗 URL：处理 HTML 实体与 JS 字符串里的转义。

    ★ 公众号文章页里的视频链接有三种形态（2026-10-17 实测）：
        正常 HTML:  ...?a=1&amp;b=2        → html.unescape 后 a=1&b=2
        JS 字符串:  ...?a=1\\x26amp;b=2     → 需先去 \\x26 → &，再 unescape
        双重转义:   ...?a=1&amp;amp;b=2     → 反复 unescape 直到稳定
    处理不对的症状：整个查询串被当成 dis_k 的值 → CDN 返回 **403 Forbidden**。
    """
    s = (u or "").strip()
    # ① 还原 \x26 / \u0026 等 JS 转义（公众号正文常放在 JS 字符串里）
    s = s.replace("\\x26", "&").replace("\\x3d", "=").replace("\\u0026", "&")
    # ② 反复解 HTML 实体（最多 3 轮，防双重/三重转义）
    for _ in range(3):
        nxt = html.unescape(s)
        if nxt == s:
            break
        s = nxt
    return s.strip()


def parse_article(html_text: str) -> List[MpVideo]:
    """从文章 HTML 里提取全部视频。

    ★ 关键：同一视频有多个清晰度（f10002/f10004/f10102/f10104），
      必须**按 vid 合并**，否则一篇文章 3 个视频会被报成 12 个。

    三条来源，按可靠性排序、去重合并：
      ① <video src="...mp4...">            —— 最可靠（PC 端常见）
      ② data-src / data-url 属性里的 mp4    —— 部分模板
      ③ 全文正则兜底 mpvideo.qpic.cn 链接   —— 手机端兜底
    """
    # vid → {"renditions": {format_id: url}, "poster": str}
    groups = {}
    order = []

    def add(u: str, poster: str = ""):
        u = _clean(u)
        if not u or ".mp4" not in u:
            return
        vid = _vid_of(u)
        if not vid:
            return
        if vid not in groups:
            groups[vid] = {"renditions": {}, "poster": ""}
            order.append(vid)
        fid = _format_id(u) or "default"
        groups[vid]["renditions"].setdefault(fid, u)
        if poster and not groups[vid]["poster"]:
            groups[vid]["poster"] = _clean(poster)

    # ① <video ...> 标签（含 src / poster 属性，顺序不限）
    for tag in re.findall(r"<video\b[^>]*>", html_text, re.I):
        src = re.search(r'\bsrc\s*=\s*["\']([^"\']+)["\']', tag, re.I)
        poster = re.search(r'\bposter\s*=\s*["\']([^"\']+)["\']', tag, re.I)
        if src:
            add(src.group(1), poster.group(1) if poster else "")

    # ② data-src / data-url
    for m in re.finditer(
            r'\bdata-(?:src|url|video)\s*=\s*["\']([^"\']*mp4[^"\']*)["\']',
            html_text, re.I):
        add(m.group(1))

    # ③ 全文兜底：直接扫 mpvideo.qpic.cn 的 mp4 链接
    #    ★ 注意：不能排除反斜杠 —— 公众号正文里链接常写成 \x26amp; 形式，
    #      若把 \ 排除在外，URL 会在第一个 \x26 处被截断，只剩 dis_k 一个参数
    #      → CDN 返回 403 Forbidden（2026-10-17 实测坑）。
    for m in re.finditer(
            r'https?://mpvideo\.qpic\.cn/[^\s"\'<>]+?\.mp4[^\s"\'<>]*',
            html_text):
        add(m.group(0))

    out: List[MpVideo] = []
    for vid in order:
        g = groups[vid]
        rends = g["renditions"]
        # 排序：按 format_id 升序（数字越大越清晰），取最后一档为默认
        keys = sorted(rends, key=lambda k: (k == "default", k))
        items = tuple((k, rends[k], _QUALITY_HINT.get(k, k)) for k in keys)
        default_url = rends[keys[-1]] if keys else ""
        out.append(MpVideo(url=default_url, vid=vid, poster=g["poster"],
                           renditions=items))
    return out


def fetch_mp_videos(url: str, timeout: float = 20.0) -> List[MpVideo]:
    """一步到位：抓页面 + 解析。失败抛异常（由调用方决定提示方式）。"""
    if not is_mp_article_url(url):
        raise ValueError("这不是公众号文章链接（应形如 "
                         "https://mp.weixin.qq.com/s/xxxx）")
    return parse_article(fetch_article(url, timeout=timeout))


def article_title(html_text: str) -> str:
    """取文章标题（用于命名文件）。"""
    # 优先 og:title / msg_title
    for pat in (r'property=["\']og:title["\']\s+content=["\']([^"\']+)',
                r'var\s+msg_title\s*=\s*["\']([^"\']+)["\']',
                r"var\s+msg_title\s*=\s*'([^']+)'",
                r"<title>([^<]+)</title>"):
        m = re.search(pat, html_text, re.I)
        if m:
            t = _clean(m.group(1)).strip()
            if t and t not in ("微信公众平台",):
                return t
    return "公众号视频"


def safe_filename(name: str, maxlen: int = 60) -> str:
    """把标题转成安全的文件名（去掉路径非法字符）。"""
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", (name or "").strip())
    s = re.sub(r"\s+", " ", s).strip(" .")
    return (s[:maxlen] or "video")


def download_url(url: str, referer: str = "https://mp.weixin.qq.com/") -> str:
    """返回可直下的 URL（已附加必要的请求头说明）。

    实测（2026-10-17）：mpvideo.qpic.cn 需要 **Referer** 才给 200，
    否则 403 Forbidden。下载时应带上：
        User-Agent: 见 UA 常量
        Referer:    文章页 URL（或 https://mp.weixin.qq.com/）

    本函数只做校验/规范化，不发起下载；实际下载复用 sphdl.downloader。
    """
    if not url or ".mp4" not in url:
        raise ValueError("不是有效的视频直链")
    return url


DOWNLOAD_HEADERS = {
    "User-Agent": UA,
    # ★ 必需：不带 Referer 会被 CDN 拒（403）
    "Referer": "https://mp.weixin.qq.com/",
}


def selftest() -> list:
    """离线自检（不联网）：解析逻辑 + URL 判定。"""
    out = []

    def ck(n, c, d=""):
        out.append((n, bool(c), d))

    sample = '''
    <html><head><title>测试文章</title></head><body>
    <video src="https://mpvideo.qpic.cn/a.f10002.mp4?vid=wxv_111&amp;auth_key=x"
           poster="https://x/p.jpg"></video>
    <div data-src="https://mpvideo.qpic.cn/b.f10002.mp4?vid=wxv_222"></div>
    <script>var x="https://mpvideo.qpic.cn/c.f10002.mp4?vid=wxv_333";</script>
    <video src="https://mpvideo.qpic.cn/a.f10002.mp4?vid=wxv_111&amp;auth_key=x"></video>
    </body></html>'''
    vs = parse_article(sample)
    ck("解析出 3 个视频（去重后）", len(vs) == 3, f"got={len(vs)}")
    ck("vid 提取正确", [v.vid for v in vs] == ["wxv_111", "wxv_222", "wxv_333"],
       str([v.vid for v in vs]))
    ck("HTML 实体已解码（&amp;→&）", all("&amp;" not in v.url for v in vs))
    ck("封面已提取", bool(vs[0].poster))
    ck("标题提取", article_title(sample) == "测试文章")
    ck("公众号链接判定 True",
       is_mp_article_url("https://mp.weixin.qq.com/s/BAvjuF4k6BLWlyCHTY58xA"))
    ck("视频号链接判定 False",
       not is_mp_article_url("https://channels.weixin.qq.com/x"))
    ck("空链接判定 False", not is_mp_article_url(""))
    ck("文件名去非法字符", safe_filename('a/b:c*d?"e') == "a_b_c_d__e",
       safe_filename('a/b:c*d?"e'))
    ck("空标题有兜底", safe_filename("") == "video")
    return out


if __name__ == "__main__":
    for n, ok, d in selftest():
        print(f"{'[PASS]' if ok else '[FAIL]'} {n} {d}")
