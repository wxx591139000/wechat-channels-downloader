"""抖音主页批量抓取（方案 A · 客户滚动 + 工具监听）。

═══ ★★ 实测验证的正确方案（2026-11-01）★★ ═══

## 走过的弯路（全部实测否定）

| 尝试 | 结果 |
|---|---|
| 工具自己 fetch 翻页 | 第 1 页 18 条，第 2 页起稳定返回 0（限流） |
| 工具自己滚 scrollTop=scrollHeight | 只拿到第 1 页 |
| 工具自己分步滚 | 反复请求第 1 页，cursor 不前进 |
| 工具自己滚 + 等 8 秒 | 偶发成功，不可复现 |

## 最终方案：客户滚动 + 工具监听

**关键洞察**：抖音对「页面自己发的请求」放行，「工具主动发的请求」限流。
所以让**页面自己发**（客户一滚就发），工具只监听响应。

**实测数据（84 条作品的主页）**：
```
滚 1 次 → 批 1: cursor=1786593802000  18 条
滚 2 次 → 批 2: cursor=1778817648000  18 条
滚 3 次 → 批 3: cursor=1767605400000  18 条
滚 4 次 → 批 4/5: cursor=1728462603000  has_more=0
→ 共 84 条，4 次滚动，每次只等 2.5 秒，总耗时 ~15 秒
```

## 客户需要做什么

1. 在工具里粘贴主页链接
2. 工具打开一个**可见的浏览器窗口**显示该主页
3. 客户在窗口里**往下滚**（滚到底就行，每滚一次工具自动收一批）
4. 滚到底后工具提示「已抓完，共 N 条」
5. 回到工具勾选下载

**体感**：跟平时刷抖音一样，滚 4 下就抓完 84 条。不用点任何"抓取"按钮。

## 为什么必须可见窗口

headless 模式下滚动不触发 IntersectionObserver（实测无效）。
有头窗口才和真实用户一致。
"""
from __future__ import annotations

import json
import time
from typing import Callable

from .logger import logger

_LIST_KEYS = ("aweme/v1/web/aweme/post", "aweme/post")
MAX_ITEMS_DEFAULT = 500
SCROLL_WAIT = 2.5          # 每次滚动后等待（实测 2.5 秒够）
BOOT_WAIT = 8.0            # 打开主页后等待（实测 8 秒页面才稳定）
MAX_ROUNDS = 200           # 最多滚多少轮


def _looks_like_list(url: str) -> bool:
    return any(k in url for k in _LIST_KEYS)


def _slim(a: dict) -> dict | None:
    """把 aweme 对象精简成下载需要的字段（原始对象几百 KB，不要往 Python 传）。"""
    if not isinstance(a, dict):
        return None
    v = a.get("video") or {}
    pa = v.get("play_addr") or {}
    urls = pa.get("url_list") or []
    size = int(pa.get("data_size") or 0)
    w = int(pa.get("width") or 0)
    h = int(pa.get("height") or 0)
    if not urls:
        best = None
        for br in (v.get("bit_rate") or []):
            p2 = br.get("play_addr") or {}
            u2 = p2.get("url_list") or []
            sz = int(p2.get("data_size") or 0)
            if u2 and (best is None or sz > best[0]):
                best = (sz, u2, int(p2.get("width") or 0),
                        int(p2.get("height") or 0))
        if best:
            size = size or best[0]
            urls = best[1]
            w = w or best[2]
            h = h or best[3]
    if not urls:
        return None
    try:
        cover = ((v.get("cover") or {}).get("url_list") or [""])[0]
    except Exception:
        cover = ""
    au = a.get("author") or {}
    return {
        "aweme_id": str(a.get("aweme_id") or ""),
        "title": (a.get("desc") or "").strip(),
        "author": (au.get("nickname") or "").strip(),
        "duration_ms": int(a.get("duration") or 0),
        "play_url": urls[0],
        "size": size,
        "width": w,
        "height": h,
        "cover": cover,
    }


_SCROLL_JS = """() => {
    let best = null, bestH = 0;
    for (const el of document.querySelectorAll('*')) {
        const st = getComputedStyle(el);
        if (!/auto|scroll/.test(st.overflowY)) continue;
        const h = el.scrollHeight - el.clientHeight;
        if (h > bestH) { bestH = h; best = el; }
    }
    if (best && bestH > 100) {
        best.scrollTop = best.scrollHeight;
        return best.scrollHeight - best.clientHeight;
    }
    window.scrollTo(0, document.documentElement.scrollHeight);
    return document.documentElement.scrollHeight;
}"""


def fetch_profile_by_scroll(url: str,
                            max_items: int = MAX_ITEMS_DEFAULT,
                            on_log: Callable[[str, str], None] | None = None,
                            on_page: Callable[[int, int], None] | None = None,
                            should_stop: Callable[[], bool] | None = None,
                            visible: bool = True,
                            auto_scroll: bool = True):
    """客户滚动 + 工具监听，抓取抖音主页全部作品。

    参数：
        visible     True=弹出可见浏览器（推荐，客户自己滚）；
                    False=headless（实测滚动无效，仅供调试）
        auto_scroll True=工具自动帮客户滚（客户只需看着）
                    注意：必须 visible=True 才可能生效

    返回 ProfileResult（含 items）。
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

    try:
        from .douyin import is_logged_in
        if not is_logged_in():
            res.error = (
                "抓取抖音主页需要先登录。\n\n"
                "未登录时抖音不返回作品列表（页面一直显示「数据加载中」）。\n"
                "请先点窗口顶部【登录抖音】完成扫码。")
            _log("warn", "未登录，主页抓取已中止")
            return res
    except Exception:
        pass

    info = normalize(url)
    if info["kind"] != "profile" or not info["sec_uid"]:
        res.error = "不是有效的抖音主页链接"
        return res
    res.sec_uid = info["sec_uid"]
    target = info["url"]

    from playwright.sync_api import sync_playwright
    from . import douyin as DY
    from .douyin import DouyinVideo

    seen: dict = {}
    meta = {"batches": 0, "has_more": True, "cursors": []}

    def on_response(resp):
        try:
            u = resp.url
            if "douyin.com" not in u or not _looks_like_list(u):
                return
            ct = (resp.headers or {}).get("content-type", "")
            if "json" not in ct:
                return
            d = json.loads(resp.text())
            lst = d.get("aweme_list") or []
            meta["batches"] += 1
            meta["has_more"] = bool(d.get("has_more") or 0)
            meta["cursors"].append(d.get("max_cursor"))
            added = 0
            for a in lst:
                it = _slim(a)
                if not it or not it["aweme_id"] or it["aweme_id"] in seen:
                    continue
                seen[it["aweme_id"]] = DouyinVideo(
                    aweme_id=it["aweme_id"], title=it["title"],
                    author=it["author"], duration_ms=it["duration_ms"],
                    play_url=it["play_url"], size=it["size"],
                    width=it["width"], height=it["height"],
                    cover=it["cover"])
                added += 1
            logger.debug(f"主页批次 #{meta['batches']}: "
                         f"cursor={d.get('max_cursor')} 本批 {len(lst)} 条 "
                         f"新增 {added} 累计 {len(seen)} "
                         f"has_more={meta['has_more']}")
            if added and on_page:
                try:
                    on_page(added, len(seen))
                except Exception:
                    pass
        except Exception:
            pass

    with sync_playwright() as p:
        ctx = None
        try:
            # 用持久化 profile（带登录态）
            for _name, kw in DY._browser_candidates():
                try:
                    ctx = p.chromium.launch_persistent_context(
                        str(DY.profile_dir()),
                        headless=not visible,
                        args=["--no-first-run",
                              "--disable-blink-features=AutomationControlled"],
                        user_agent=DY.UA, locale="zh-CN",
                        viewport={"width": 1400, "height": 900}, **kw)
                    break
                except Exception:
                    continue
            if ctx is None:
                res.error = "浏览器启动失败（需要 Edge 或 Chrome）"
                return res

            pg = ctx.pages[0] if ctx.pages else ctx.new_page()
            pg.on("response", on_response)

            _log("info", f"打开主页 {target}")
            try:
                pg.goto(target, wait_until="commit", timeout=45000)
            except Exception:                          # noqa: BLE001
                pass
            _log("info", f"等待页面加载（{int(BOOT_WAIT)} 秒）…")
            time.sleep(BOOT_WAIT)

            try:
                t = pg.title() or ""
                res.nickname = t.split("-")[0].strip() if t else ""
            except Exception:
                pass

            if visible:
                _log("ok", "请在弹出的窗口里【往下滚动】，工具会自动收集作品")
                _log("info", "滚到底后稍等，看到「已到底」提示即可回到本窗口")

            idle = 0
            last_n = 0
            for rnd in range(1, MAX_ROUNDS + 1):
                if should_stop and should_stop():
                    res.note = "用户停止"
                    break

                cur_n = len(seen)
                if cur_n > last_n:
                    idle = 0
                    last_n = cur_n
                    _log("info", f"第 {rnd} 轮：已收集 {cur_n} 条")
                else:
                    idle += 1

                if max_items and cur_n >= max_items:
                    res.note = f"已达上限 {max_items} 条"
                    break
                # 已到底：has_more=0 且至少拿到一批
                if meta["batches"] > 0 and not meta["has_more"]:
                    res.note = f"已到底，共 {cur_n} 条"
                    _log("ok", f"✅ 已到底，共 {cur_n} 条作品")
                    break
                if idle >= 30:
                    res.note = (f"连续 30 轮无新增，停止"
                                f"（可能未登录/私密/到底）")
                    _log("warn", res.note)
                    break

                if auto_scroll:
                    try:
                        pg.evaluate(_SCROLL_JS)
                    except Exception:
                        pass
                time.sleep(SCROLL_WAIT)

            res.items = list(seen.values())
            res.has_more = meta["has_more"]
            _log("ok", f"完成：{len(seen)} 条作品，{meta['batches']} 批响应")
            if not res.items:
                res.error = ("没抓到作品。常见原因：登录态失效、"
                             "账号作品私密、或链接不是主页")
        finally:
            try:
                if ctx:
                    ctx.close()
            except Exception:
                pass

    return res
