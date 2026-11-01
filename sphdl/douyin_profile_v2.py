"""抖音主页抓取：用「诊断脚本验证成功的时序」重写抓取循环。

═══ 诊断脚本的成功时序（实测 5 次请求、cursor 连续推进）═══
```
goto(wait_until="commit")
sleep(8)                      ← 关键：等页面完全稳定
循环 6 次:
    记录容器 scrollTop / scrollHeight
    scrollTop = scrollHeight  ← 一次滚到最大容器的底部
    sleep(3)
→ 得到 5 次 aweme/post 请求，cursor 从 1786593802000 一路到 1728462603000
→ 最后一次 has_more=0，共 91 个作品
```

═══ 之前失败的原因（逐个试出来的）═══
1. `wait_until="domcontentloaded"` → 60 秒超时（主页太重）
2. 只等 3 秒就滚 → 此时初次 xhr=0，滚在空页面上
3. 等首批数据到了立刻滚 → 页面虚拟列表未初始化，滚到小容器(1098)
4. 分步滚动 → 只触发第一页重复加载（cursor 不前进）
5. 滚"所有容器" → 同上

核心：**必须固定等 8 秒让页面稳定，然后滚「当前最大」容器一次到底，每次滚完等 3 秒**。

本文件是 fetch_profile 的替代实现，直接复刻上述时序。
"""
from __future__ import annotations

import json
import time
from typing import Callable

from .logger import logger

_LIST_KEYS = ("aweme/v1/web/aweme/post", "aweme/post")
MAX_ITEMS_DEFAULT = 500
BOOT_WAIT = 8.0       # 打开主页后固定等待（诊断脚本验证值）
SCROLL_WAIT = 3.0     # 每次滚动后等待
MAX_ROUNDS = 80       # 最多滚多少轮（防止无限）
MAX_IDLE = 6          # 连续多少轮无新增就停


def _looks_like_list(url: str) -> bool:
    return any(k in url for k in _LIST_KEYS)


def _extract_list(data: dict):
    if not isinstance(data, dict):
        return [], False, 0
    lst = data.get("aweme_list") or data.get("awemeList") or []
    if not isinstance(lst, list):
        lst = []
    return (lst, bool(data.get("has_more") or 0),
            int(data.get("max_cursor") or 0))


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
            br_size, br_w, br_h = (best[0], int(best[2].get("width") or 0),
                                   int(best[2].get("height") or 0))
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


# 滚动 JS：找「当前可滚高度最大」的容器，一次滚到底
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
        return bestH;
    }
    const wh = document.documentElement.scrollHeight;
    if (wh > window.innerHeight + 100) {
        window.scrollTo(0, wh);
        return wh;
    }
    return 0;
}"""


def fetch_profile_v2(url: str,
                     max_items: int = MAX_ITEMS_DEFAULT,
                     on_log: Callable[[str, str], None] | None = None,
                     on_page: Callable[[int, int], None] | None = None,
                     should_stop: Callable[[], bool] | None = None):
    """按诊断脚本验证的时序抓取主页全部作品。"""
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
            res.error = ("抓取抖音主页需要先登录。\n\n"
                         "未登录时抖音不返回作品列表（页面一直显示「数据加载中」）。\n"
                         "请先点【登录抖音】扫码。")
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

    seen: dict = {}
    batch: list = []
    meta = {"resp": 0, "has_more": False, "cursors": []}

    def on_response(resp):
        try:
            u = resp.url
            if "douyin.com" not in u or not _looks_like_list(u):
                return
            ct = (resp.headers or {}).get("content-type", "")
            if "json" not in ct:
                return
            data = json.loads(resp.text())
            lst, hm, cur = _extract_list(data)
            meta["resp"] += 1
            meta["has_more"] = hm
            meta["cursors"].append(cur)
            logger.debug(f"主页接口 #{meta['resp']}: cursor={cur} "
                         f"本批 {len(lst)} 条 has_more={hm}")
            if lst:
                batch.extend(lst)
        except Exception:
            pass

    with sync_playwright() as p:
        try:
            browser = DY._launch(p)
        except Exception as e:                        # noqa: BLE001
            res.error = f"浏览器启动失败：{e}"
            return res
        try:
            page = DY._ctx(browser).new_page()
            page.on("response", on_response)
            _log("info", f"打开主页 {target}")
            try:
                # ★ commit：不等页面 ready（主页 domcontentloaded 会超时）
                page.goto(target, wait_until="commit", timeout=45000)
            except Exception as e:                    # noqa: BLE001
                _log("info", f"导航未完成（{type(e).__name__}），继续")

            # ★ 固定等 8 秒（诊断脚本验证值）—— 让虚拟列表初始化完成
            _log("info", f"等待页面稳定（{int(BOOT_WAIT)} 秒）…")
            time.sleep(BOOT_WAIT)

            # 昵称
            try:
                t = page.title() or ""
                res.nickname = t.split("-")[0].strip() or t.strip()
            except Exception:
                pass

            idle = 0
            for rnd in range(1, MAX_ROUNDS + 1):
                if should_stop and should_stop():
                    res.note = "用户停止"
                    break

                # 消费本批
                added = 0
                while batch:
                    a = batch.pop(0)
                    v = _parse_item(a)
                    if v is None or not v.aweme_id or v.aweme_id in seen:
                        continue
                    seen[v.aweme_id] = v
                    added += 1
                if added:
                    idle = 0
                    _log("info", f"第 {rnd} 轮：新增 {added}，累计 {len(seen)}")
                    if on_page:
                        try:
                            on_page(added, len(seen))
                        except Exception:
                            pass
                else:
                    idle += 1
                    _log("info", f"第 {rnd} 轮：无新增（{idle}/{MAX_IDLE}）")

                if max_items and len(seen) >= max_items:
                    res.note = f"已达上限 {max_items} 条"
                    break
                if idle >= MAX_IDLE:
                    res.note = (f"连续 {MAX_IDLE} 轮无新增，停止"
                                f"（has_more={meta['has_more']}）")
                    break

                # 滚到当前最大容器底部（诊断脚本验证的有效方式）
                try:
                    _h = page.evaluate(_SCROLL_JS)
                    logger.debug(f"滚动: 容器可滚高度 {_h}")
                except Exception as e:                # noqa: BLE001
                    logger.debug(f"滚动异常: {e}")
                time.sleep(SCROLL_WAIT)

            res.items = list(seen.values())
            res.has_more = meta["has_more"]
            _log("info", f"完成：{len(seen)} 条，接口 {meta['resp']} 次，"
                         f"cursors={meta['cursors'][:6]}")
            if not res.items:
                res.error = "没抓到作品（检查登录态/链接）"
        finally:
            try:
                browser.close()
            except Exception:
                pass

    return res
