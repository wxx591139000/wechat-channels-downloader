"""抖音主页批量抓取（方案 B · 页面内 fetch 版）。

═══ ★★ 为什么最终用这个方案（2026-11-01 实测踩透）★★ ═══

前面试过的所有「滚动触发加载」方式**全部不可靠**：

| 方式 | 实测结果 |
|---|---|
| page.mouse.wheel | 无效（headless 鼠标不在滚动区） |
| scrollTop = scrollHeight（最大容器） | 只拿到第一页 18 条 |
| 滚"所有"容器 | 反复请求第一页（cursor 不前进） |
| 分步滚动 | 同上 |
| 先等 8 秒再滚 | 偶发成功 5 次请求，但不可复现 |

抖音主页用**虚拟滚动 + IntersectionObserver**，滚动触发时机极难稳定复现。

═══ 最终方案：在页面上下文里直接 fetch ═══
作品列表接口需要 `a_bogus` 签名（页面 JS 算的）。
但**在页面里发 fetch，签名自动生效**（同一 JS 环境）。

实测验证：
```
第一页  max_cursor=0            → 18 条, has_more=1, max_cursor=1786593802000
第二页  max_cursor=1786593802000 → 18 条, has_more=1, max_cursor=1778817648000
        ← cursor 连续前进，首条内容不同
```

**优势**：
· 完全不用滚动，不受虚拟列表影响
· 稳定可复现
· 翻页速度快（无渲染开销）

**前提**：必须已登录（未登录时接口返回空或风控）。
"""
from __future__ import annotations

import json
import time
from typing import Callable

from .logger import logger

MAX_ITEMS_DEFAULT = 500
MAX_PAGES = 100          # 最多翻多少页（防死循环）
# ★★ v2.9.5：翻页间隔必须 >= 5 秒（实测结论）★★
#   0.8s / 2.5s（含重试）→ 第 2 页起稳定返回 0 条（限流）
#   5.0s → 5 页全部成功，84 条，has_more=0 正常到底
#   代价：一部 200 条作品的主页约需 200/18*5 ≈ 56 秒（可接受）
PAGE_WAIT = 5.0          # 每页之间间隔（防风控，<5s 会返回空）
BOOT_WAIT = 6.0          # 打开首页建立环境后等待

# 页面内 fetch 的 JS（带完整 web 端参数）
_FETCH_JS = """async (args) => {
    const [sec, cursor, count] = args;
    const qs = new URLSearchParams({
        device_platform: 'webapp',
        aid: '6383',
        channel: 'channel_pc_web',
        sec_user_id: sec,
        max_cursor: String(cursor),
        count: String(count),
        publish_video_strategy_type: '2',
        update_version_code: '170400',
        pc_client_type: '1',
        version_code: '170400',
        version_name: '17.4.0',
        cookie_enabled: 'true',
        screen_width: String(window.screen.width || 1400),
        screen_height: String(window.screen.height || 900),
        browser_language: 'zh-CN',
        browser_platform: 'Win32',
        browser_name: 'Edge',
        browser_version: '120.0.0.0',
    });
    try {
        const r = await fetch('/aweme/v1/web/aweme/post/?' + qs.toString(),
                              {credentials: 'include'});
        const txt = await r.text();
        try {
            const j = JSON.parse(txt);
            const raw = j.aweme_list || [];
            // ★★ v2.9.5 关键 ★★
            //   不要把整个 aweme 对象传回 Python —— 单页 18 条原始 JSON 约
            //   1.8MB，Playwright 序列化 + Python 反序列化要好几秒，
            //   期间页面空闲，抖音会把下一页请求判成异常/限流（实测：
            //   传全量时第 2 页必空；只传必要字段时第 2 页正常）。
            //   所以在 JS 侧就精简成扁平结构。
            const slim = raw.map(a => {
                const v = a.video || {};
                const pa = v.play_addr || {};
                let urls = pa.url_list || [];
                let size = pa.data_size || 0;
                let w = pa.width || 0, h = pa.height || 0;
                if (!urls.length && v.bit_rate && v.bit_rate.length) {
                    let best = null;
                    for (const br of v.bit_rate) {
                        const p2 = br.play_addr || {};
                        const u2 = p2.url_list || [];
                        const sz = p2.data_size || 0;
                        if (u2.length && (!best || sz > best.sz)) {
                            best = {sz: sz, u: u2, w: p2.width || 0,
                                    h: p2.height || 0};
                        }
                    }
                    if (best) { urls = best.u; size = size || best.sz;
                                w = w || best.w; h = h || best.h; }
                }
                return {
                    aweme_id: a.aweme_id || '',
                    desc: a.desc || '',
                    duration: a.duration || 0,
                    author: (a.author || {}).nickname || '',
                    play_url: urls[0] || '',
                    size: size,
                    width: w,
                    height: h,
                    cover: ((v.cover || {}).url_list || [''])[0] || '',
                    has_video: !!(v && (pa.url_list || (v.bit_rate || []).length)),
                };
            });
            return {
                ok: true,
                status: r.status,
                status_code: j.status_code,
                n: raw.length,
                has_more: j.has_more,
                max_cursor: j.max_cursor,
                items: slim,
            };
        } catch (e) {
            return {ok: false, status: r.status, err: 'json',
                    head: txt.slice(0, 200)};
        }
    } catch (e) {
        return {ok: false, err: String(e)};
    }
}"""


def _parse_item(a: dict):
    from .douyin import DouyinVideo
    if not isinstance(a, dict):
        return None
    v = a.get("video") or {}
    if not v:
        return None
    pa = v.get("play_addr") or {}
    urls = pa.get("url_list") or []
    br_size = br_w = br_h = 0
    if not urls:
        best = None
        for br in (v.get("bit_rate") or []):
            p2 = br.get("play_addr") or {}
            u = p2.get("url_list") or []
            sz = int(p2.get("data_size") or 0)
            if u and (best is None or sz > best[0]):
                best = (sz, u, p2)
        if best:
            urls = best[1]
            br_size = best[0]
            br_w = int(best[2].get("width") or 0)
            br_h = int(best[2].get("height") or 0)
    if not urls:
        return None
    au = a.get("author") or {}
    cover = ""
    try:
        cl = (v.get("cover") or {}).get("url_list") or []
        cover = cl[0] if cl else ""
    except Exception:
        pass
    return DouyinVideo(
        aweme_id=str(a.get("aweme_id") or ""),
        title=(a.get("desc") or "").strip(),
        author=(au.get("nickname") or "").strip(),
        duration_ms=int(a.get("duration") or 0),
        play_url=urls[0],
        size=int(pa.get("data_size") or 0) or br_size,
        width=int(pa.get("width") or 0) or br_w,
        height=int(pa.get("height") or 0) or br_h,
        cover=cover,
    )


def fetch_profile(url: str,
                  max_items: int = MAX_ITEMS_DEFAULT,
                  on_log: Callable[[str, str], None] | None = None,
                  on_page: Callable[[int, int], None] | None = None,
                  should_stop: Callable[[], bool] | None = None):
    """抓取抖音主页全部作品（页面内 fetch + cursor 翻页）。

    参数同 douyin_profile.fetch_profile。
    """
    from .douyin_profile import ProfileResult, normalize

    def _log(lv, m):
        try:
            if lv == "info":
                logger.info(f"[主页] {m}")
            else:
                logger.warn(m)
        except Exception:
            pass
        if on_log:
            try:
                on_log(lv, m)
            except Exception:
                pass

    res = ProfileResult()

    # 前置：必须登录
    try:
        from .douyin import is_logged_in
        if not is_logged_in():
            res.error = (
                "抓取抖音主页需要先登录。\n\n"
                "未登录时抖音不返回作品列表（页面一直显示「数据加载中」）。\n"
                "请先点窗口顶部【登录抖音】完成扫码。\n"
                "（单个作品链接可以直接解析下载，不需要登录）")
            _log("warn", "未登录，主页抓取已中止")
            return res
    except Exception:
        pass

    info = normalize(url)
    if info["kind"] != "profile" or not info["sec_uid"]:
        res.error = "不是有效的抖音主页链接"
        return res
    res.sec_uid = info["sec_uid"]
    logger.debug(f"主页抓取 sec_uid={res.sec_uid!r} "
                 f"normalize结果={info}")

    from playwright.sync_api import sync_playwright
    from . import douyin as DY

    seen: dict = {}
    cursor = 0
    pages = 0

    with sync_playwright() as p:
        try:
            browser = DY._launch(p)
        except Exception as e:                        # noqa: BLE001
            res.error = f"浏览器启动失败：{e}"
            return res
        try:
            page = DY._ctx(browser).new_page()
            _log("info", "打开抖音建立会话…")
            try:
                page.goto("https://www.douyin.com/",
                          wait_until="commit", timeout=45000)
            except Exception:                          # noqa: BLE001
                pass
            time.sleep(BOOT_WAIT)

            # 昵称（顺带确认登录态可用）
            try:
                t = page.title() or ""
                res.nickname = ("抖音" if not t else t.split("-")[0].strip())
            except Exception:
                pass

            while pages < MAX_PAGES:
                if should_stop and should_stop():
                    res.note = "用户停止"
                    break
                pages += 1

                r = page.evaluate(_FETCH_JS, [res.sec_uid, cursor, 18])
                # ★ 空页重试一次（限流往往是单次性的）
                if r and r.get("ok") and (r.get("n") or 0) == 0 and pages > 1:
                    _log("info", f"第 {pages} 页返回空，2 秒后重试一次…")
                    time.sleep(2.0)
                    r = page.evaluate(_FETCH_JS, [res.sec_uid, cursor, 18])
                if not r or not r.get("ok"):
                    _log("warn", f"第 {pages} 页请求失败：{str(r)[:120]}")
                    res.note = f"第 {pages} 页请求失败"
                    break
                logger.debug(f"第 {pages} 页原始: status={r.get('status')} "
                             f"status_code={r.get('status_code')} "
                             f"n={r.get('n')} has_more={r.get('has_more')} "
                             f"max_cursor={r.get('max_cursor')} "
                             f"传入cursor={cursor}")

                n = r.get("n") or 0
                has_more = bool(r.get("has_more") or 0)
                new_cursor = r.get("max_cursor") or 0

                added = 0
                for it in (r.get("items") or []):
                    if not it.get("aweme_id") or not it.get("play_url"):
                        continue
                    if it["aweme_id"] in seen:
                        continue
                    from .douyin import DouyinVideo
                    seen[it["aweme_id"]] = DouyinVideo(
                        aweme_id=it["aweme_id"],
                        title=(it.get("desc") or "").strip(),
                        author=(it.get("author") or "").strip(),
                        duration_ms=int(it.get("duration") or 0),
                        play_url=it["play_url"],
                        size=int(it.get("size") or 0),
                        width=int(it.get("width") or 0),
                        height=int(it.get("height") or 0),
                        cover=it.get("cover") or "",
                    )
                    added += 1

                _log("info", f"第 {pages} 页：{n} 条，新增 {added}，"
                             f"累计 {len(seen)}，has_more={has_more}")
                if added and on_page:
                    try:
                        on_page(added, len(seen))
                    except Exception:
                        pass

                if max_items and len(seen) >= max_items:
                    res.note = f"已达上限 {max_items} 条"
                    break
                if not has_more:
                    res.note = "已到列表末尾（has_more=0）"
                    break
                if new_cursor == cursor:
                    _log("warn", "cursor 未前进，停止避免死循环")
                    res.note = "cursor 未前进"
                    break
                cursor = new_cursor
                time.sleep(PAGE_WAIT)

            res.items = list(seen.values())
            res.has_more = bool(has_more) if pages else False
            _log("info", f"完成：{len(seen)} 条作品，翻了 {pages} 页")
            if not res.items:
                res.error = ("没抓到作品。常见原因：登录态失效、"
                             "该账号作品为私密、或链接不是主页")
        finally:
            try:
                browser.close()
            except Exception:
                pass

    return res
