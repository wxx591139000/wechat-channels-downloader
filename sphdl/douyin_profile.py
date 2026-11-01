"""抖音主页批量抓取（方案 B）。

═══ 客户需求 ═══
方案 A 是「粘贴链接批量下载」，客户希望能「输入主页链接 → 抓该博主的
全部作品」。方案 A 已验证稳定后才做这个。

═══ 实现思路（不猜接口，用真实浏览器）═══
不自己构造请求（抖音接口有 a_bogus 签名，自己算容易失效），
而是**用真实 Edge 打开主页，让页面自己发请求，我们读响应**：

  打开 /user/<sec_uid> → 监听 aweme/post 响应
  → 首页拿到第一批 + has_more/max_cursor
  → 若 has_more，用 JS 滚动触发加载更多，读新响应
  → 直到 has_more=0 或达到用户设定上限

★ 为什么要滚动而不是自己发请求：
  抖音 web 端的作品列表请求带 `a_bogus` 签名（由页面 JS 计算），
  自己发必然被拒。滚动让页面自己发起，签名天然正确。

★★ 验证状态（2026-10-30）★★
  代码逻辑：✅ 33/33 单测通过（URL解析/响应解析/条目解析/图文跳过/bit_rate兜底）
  打包自检：✅ --selftest-douyin 通过
  真实抓取：❌ **未验证** —— 本机抖音登录态过期，无法完成
            （已验证"未登录会失败"这个前提，但"登录后能成功"尚未实测）

═══ 已知限制（如实记录）═══
· 未登录时抖音通常只给前少量作品，且频繁风控 → 建议登录
· 滚动加载有随机性，接口可能间歇失败 → 失败重试 + 兜底停止
· 私密/好友可见/已删除的作品拿不到
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from .logger import logger

# 主页作品列表接口关键词（命中即认为是列表响应）
_LIST_KEYS = (
    "aweme/v1/web/aweme/post",
    "aweme/post",
    "/aweme/v1/web/aweme/post/",
)

# ══════════════════ 主页 URL 解析（纯字符串，不联网） ══════════════════
# 抖音主页链接形态很多，先归一化成 sec_uid 再做事。
_RE_USER = re.compile(
    r"https?://(?:www\.)?douyin\.com/user/([A-Za-z0-9_\-]{20,})", re.I)
_RE_SHORT = re.compile(r"https?://v\.douyin\.com/([A-Za-z0-9_\-]+)/?", re.I)
# ★ v2.9.4：旧版分享域的主页形态（短链常落到这里）
#   https://www.iesdouyin.com/share/user/<sec_uid>?...
_RE_SHARE_USER = re.compile(
    r"https?://(?:www\.|m\.|)iesdouyin\.com/share/user/([A-Za-z0-9_\-]{20,})",
    re.I)
# 查询参数里的 sec_uid（部分落地页带 ?sec_uid=<id>）
_RE_SEC_Q = re.compile(r"[?&]sec_uid=([A-Za-z0-9_\-]{20,})", re.I)
_RE_MODAL = re.compile(r"[?&]modal_id=", re.I)
_RE_SEC = re.compile(r"^MS4wLjABAAAA[A-Za-z0-9_\-]{10,}$")


def normalize(text: str) -> dict:
    """把一段文本（链接/分享文案）解析成结构化信息。

    返回 {"kind", "sec_uid", "short_code", "url", "is_modal"}
      profile —— 已拿到 sec_uid，可用
      short   —— 短链，需联网跟随重定向
      video   —— 作品页（带 modal_id），不是主页
      unknown —— 识别不出
    """
    t = (text or "").strip()
    if not t:
        return {"kind": "unknown", "sec_uid": None, "short_code": None,
                "url": None, "is_modal": False}

    if _RE_SEC.match(t):
        return {"kind": "profile", "sec_uid": t, "short_code": None,
                "url": f"https://www.douyin.com/user/{t}", "is_modal": False}

    # ★ 先判旧版分享域的主页（短链落地常见）
    m = _RE_SHARE_USER.search(t)
    if m:
        sec = m.group(1)
        return {"kind": "profile", "sec_uid": sec, "short_code": None,
                "url": f"https://www.douyin.com/user/{sec}", "is_modal": False}

    # ★★ 顺序很重要：必须先判 /user/<sec>（主页），
    #   否则带 modal_id 的主页链接会被后面的 sec_uid 查询参数分支抢走。
    m = _RE_USER.search(t)
    if m:
        sec = m.group(1)
        # ★ 带 modal_id 时仍是**主页**（只是页面上弹出了某个作品），
        #   kind 保持 profile，用 is_modal 标记"顺便弹了视频"；
        #   抓取时应去掉 modal_id 只抓主页作品列表。
        return {"kind": "profile", "sec_uid": sec, "short_code": None,
                "url": f"https://www.douyin.com/user/{sec}",
                "is_modal": bool(_RE_MODAL.search(t))}

    # 查询参数里的 sec_uid（部分落地页带 ?sec_uid=<id>，无 /user/ 路径）
    m = _RE_SEC_Q.search(t)
    if m:
        sec = m.group(1)
        return {"kind": "profile", "sec_uid": sec, "short_code": None,
                "url": f"https://www.douyin.com/user/{sec}", "is_modal": False}

    m = _RE_SHORT.search(t)
    if m:
        return {"kind": "short", "sec_uid": None, "short_code": m.group(1),
                "url": f"https://v.douyin.com/{m.group(1)}/",
                "is_modal": False}

    if "douyin.com" in t:
        return {"kind": "video", "sec_uid": None, "short_code": None,
                "url": t, "is_modal": bool(_RE_MODAL.search(t))}

    return {"kind": "unknown", "sec_uid": None, "short_code": None,
            "url": None, "is_modal": False}


def resolve_short_url(short_url: str, timeout: int = 20) -> dict:
    """跟随短链重定向，返回最终 URL 的结构化信息。

    ★★ v2.9.4（客户实测 2026-11-01）★★
      客户贴「长按复制此条消息…查看TA的更多作品。
      https://v.douyin.com/N00VRZ-_isA/」
      结果报「30 秒内没等到视频数据」。

      根因：这条短链落地是 **主页**（/user/<sec_uid>），
      但短链本身没有 ID → 被当成作品走 detail 流程
      → 页面没有 detail 接口 → 等 20 秒超时报错。

      实测落地：
        https://www.douyin.com/user/MS4wLjABAAAAAgTdaUkW9Hoblhe...

      所以必须先跟随重定向、判断落地类型，再决定走哪条路。

    返回 normalize() 的结果（kind=profile/video/...），
    失败时 kind="unknown" 并带 error 字段。
    """
    out = {"kind": "unknown", "sec_uid": None, "short_code": None,
           "url": short_url, "is_modal": False}
    u = (short_url or "").strip()
    if not u:
        out["error"] = "空链接"
        return out

    # ① 先试裸 HTTP（快，多数情况够用）
    try:
        import urllib.request
        req = urllib.request.Request(u, headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36")})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            final = r.url
        n = normalize(final)
        if n["kind"] in ("profile", "video"):
            out.update(n)
            out["resolved_from"] = final
            return out
    except Exception as e:                                # noqa: BLE001
        logger.debug(f"短链 HTTP 跟随失败: {e}")

    # ② 退到浏览器（抖音可能用 JS 跳转）
    try:
        from playwright.sync_api import sync_playwright
        from . import douyin as DY
        with sync_playwright() as p:
            ctx = None
            for _name, kw in DY._browser_candidates()[:2]:
                try:
                    ctx = p.chromium.launch_persistent_context(
                        str(DY.profile_dir()), headless=True,
                        args=["--no-first-run"], user_agent=DY.UA,
                        locale="zh-CN",
                        viewport={"width": 1280, "height": 800}, **kw)
                    break
                except Exception:
                    continue
            if ctx is None:
                out["error"] = "浏览器启动失败，无法解析短链"
                return out
            pg = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                pg.goto(u, wait_until="domcontentloaded", timeout=30000)
                final = pg.url
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass
        n = normalize(final)
        out.update(n)
        out["resolved_from"] = final
        return out
    except Exception as e:                                # noqa: BLE001
        out["error"] = f"短链解析失败：{type(e).__name__}: {e}"
        return out


def is_profile_url(text: str) -> bool:
    return normalize(text)["kind"] == "profile"


def profile_url(sec_uid: str) -> str:
    return f"https://www.douyin.com/user/{sec_uid}"


MAX_ITEMS_DEFAULT = 500      # 默认最多抓多少条
SCROLL_WAIT = 1.8            # 每次滚动后等待秒数
MAX_IDLE_SCROLL = 6          # 连续多少次滚动无新增就停


@dataclass
class ProfileResult:
    """主页抓取结果。"""
    sec_uid: str = ""
    nickname: str = ""
    items: list = field(default_factory=list)   # list[DouyinVideo]
    has_more: bool = False
    total_seen: int = 0
    error: str = ""
    note: str = ""


def _looks_like_list(url: str) -> bool:
    return any(k in url for k in _LIST_KEYS)


def _extract_list(data: dict) -> tuple[list[dict], bool, int]:
    """从列表响应里取出 (aweme列表, has_more, max_cursor)。

    ★ 字段名按抖音 web 端实际结构：
      {"aweme_list": [...], "has_more": 1, "max_cursor": 1234567}
    兼容少量变体（awemeList / hasMore / cursor）。
    """
    if not isinstance(data, dict):
        return [], False, 0
    lst = (data.get("aweme_list") or data.get("awemeList")
           or data.get("aweme_list_v2") or [])
    if not isinstance(lst, list):
        lst = []
    has_more = bool(data.get("has_more") or data.get("hasMore") or 0)
    cursor = int(data.get("max_cursor") or data.get("cursor") or 0)
    return lst, has_more, cursor


def _parse_item(a: dict):
    """把主页列表里的单条 aweme 转成 DouyinVideo。

    主页列表的条目结构与 aweme_detail 里的 aweme 基本一致，
    复用 douyin._parse_detail 的字段提取逻辑（包一层）。
    """
    from .douyin import DouyinVideo

    if not isinstance(a, dict):
        return None
    # 图文/图集没有 video.play_addr，直接跳过（不是视频）
    if a.get("aweme_type") not in (0, 4, None) and not a.get("video"):
        return None
    v = a.get("video") or {}
    if not v:
        return None
    pa = v.get("play_addr") or {}
    urls = pa.get("url_list") or []
    br_size = 0
    br_w = br_h = 0
    if not urls:
        # 退回 bit_rate 最高档
        best = None
        for br in (v.get("bit_rate") or []):
            pa_br = br.get("play_addr") or {}
            u = (pa_br.get("url_list") or [])
            size = int(pa_br.get("data_size") or 0)
            if u and (best is None or size > best[0]):
                best = (size, u, pa_br)
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
                  should_stop: Callable[[], bool] | None = None,
                  ) -> ProfileResult:
    """抓取某抖音主页的全部（或前 max_items 条）视频。

    参数：
      url        主页链接（https://www.douyin.com/user/<sec_uid>）
      max_items  最多抓多少条（0 = 不限，用 MAX_IDLE_SCROLL 兜底停止）
      on_log     日志回调 (level, msg)
      on_page    每抓到一批回调 (本批新增, 累计)
      should_stop 返回 True 时提前停止
    """
    def _log(lv, m):
        try:
            logger.info(f"[主页] {m}") if lv == "info" else logger.warn(m)
        except Exception:
            pass
        if on_log:
            try:
                on_log(lv, m)
            except Exception:
                pass

    res = ProfileResult()
    # ★ 前置检查：未登录时抖音主页不发任何作品接口（实测 216 个响应里 0 个）。
    #   页面永远显示「数据加载中」，还会触发 page/turn/offline + verify_center 风控。
    #   所以必须先登录，直接给出明确指引，不让客户白等。
    try:
        from .douyin import is_logged_in
        if not is_logged_in():
            res.error = (
                "抓取抖音主页需要先登录。\n\n"
                "原因：未登录时抖音不返回作品列表（页面会一直显示「数据加载中」），"
                "还会触发风控。\n\n"
                "请先点窗口顶部的【登录抖音】完成扫码，再来抓主页。\n"
                "（作品链接可以直接解析下载，不需要登录；只有主页批量抓取需要）")
            _log("warn", "未登录，主页抓取已中止")
            return res
    except Exception:
        pass

    info = normalize(url)
    if info["kind"] != "profile" or not info["sec_uid"]:
        res.error = ("不是有效的抖音主页链接。请用形如 "
                     "https://www.douyin.com/user/MS4wLjABAAAA... 的链接")
        return res
    res.sec_uid = info["sec_uid"]
    target = info["url"]

    from playwright.sync_api import sync_playwright
    from . import douyin as DY

    seen: dict[str, object] = {}
    pages_meta = {"has_more": False, "cursor": 0, "resp_count": 0}
    pending: list[dict] = []

    def on_response(resp):
        try:
            u = resp.url
            if "douyin.com" not in u or not _looks_like_list(u):
                return
            ct = (resp.headers or {}).get("content-type", "")
            if "json" not in ct:
                return
            body = resp.text()
            if len(body) < 200:
                return
            data = json.loads(body)
            lst, has_more, cursor = _extract_list(data)
            pages_meta["resp_count"] += 1
            pages_meta["has_more"] = has_more
            if cursor:
                pages_meta["cursor"] = cursor
            if lst:
                pending.extend(lst)
        except Exception as e:                       # noqa: BLE001
            _log("info", f"响应解析跳过: {type(e).__name__}")

    with sync_playwright() as p:
        try:
            br = DY._launch(p)
        except Exception as e:                       # noqa: BLE001
            res.error = f"浏览器启动失败：{e}"
            return res
        try:
            ctx = DY._ctx(br)
            # 复用登录态（未登录也能拿部分，但会少且易风控）
            page = ctx.new_page()
            page.on("response", on_response)

            _log("info", f"打开主页 {target}")
            page.goto(target, wait_until="domcontentloaded", timeout=60000)
            time.sleep(3.0)

            # 拿作者昵称（页面标题里通常有）
            try:
                t = page.title() or ""
                if "-" in t:
                    res.nickname = t.split("-")[0].strip()
                else:
                    res.nickname = t.strip()
            except Exception:
                pass

            idle = 0
            rounds = 0
            while True:
                rounds += 1
                if should_stop and should_stop():
                    res.note = "用户停止"
                    break

                # 消化本批
                added = 0
                while pending:
                    a = pending.pop(0)
                    v = _parse_item(a)
                    if v is None or not v.aweme_id:
                        continue
                    if v.aweme_id in seen:
                        continue
                    seen[v.aweme_id] = v
                    added += 1
                res.total_seen = len(seen)
                if added and on_page:
                    try:
                        on_page(added, len(seen))
                    except Exception:
                        pass

                if added:
                    _log("info", f"第 {rounds} 轮：新增 {added} 条，"
                                 f"累计 {len(seen)} 条")
                    idle = 0
                else:
                    idle += 1
                    _log("info", f"第 {rounds} 轮：无新增（{idle}/{MAX_IDLE_SCROLL}）")

                if max_items and len(seen) >= max_items:
                    res.note = f"已达设定上限 {max_items} 条"
                    break
                if not pages_meta["has_more"] and rounds > 1 and idle >= 2:
                    res.note = "接口 has_more=0，已到列表末尾"
                    break
                if idle >= MAX_IDLE_SCROLL:
                    res.note = (f"连续 {MAX_IDLE_SCROLL} 轮无新增，停止"
                                "（可能是风控/未登录/真的到底了）")
                    break

                # 滚动触发加载
                try:
                    page.mouse.wheel(0, 2400)
                except Exception:
                    pass
                time.sleep(SCROLL_WAIT)

            res.items = list(seen.values())
            res.has_more = pages_meta["has_more"]
            if not res.items:
                res.error = ("没抓到任何作品。常见原因：未登录（抖音对"
                             "未登录账号限制很严）、主页链接不对、或该账号"
                             "作品为私密")
            _log("info", f"结束：共 {len(res.items)} 条，"
                         f"has_more={res.has_more}，接口响应 {pages_meta['resp_count']} 次")
        finally:
            try:
                br.close()
            except Exception:
                pass

    return res


def selftest() -> bool:
    """自检：URL 解析 + 列表响应解析（不需要网络）。"""
    ok = True

    def _c(name, cond, detail=""):
        nonlocal ok
        if not cond:
            ok = False
        print(f"  {'[PASS]' if cond else '[FAIL]'} {name}"
              + (f"  {detail}" if detail else ""))

    from .douyin_profile import normalize

    r = normalize("https://www.douyin.com/user/MS4wLjABAAAAtest_123456789012345")
    _c("标准主页链接", r["kind"] == "profile" and r["sec_uid"].startswith("MS4w"))

    r2 = normalize("https://v.douyin.com/abc123/")
    _c("短链识别", r2["kind"] == "short" and r2["short_code"] == "abc123")

    r3 = normalize("https://www.douyin.com/user/MS4wLjABAAAAtest?modal_id=123")
    _c("带 modal_id 标记", r3["is_modal"] is True)

    lst, hm, cur = _extract_list({"aweme_list": [{"a": 1}, {"a": 2}],
                                  "has_more": 1, "max_cursor": 999})
    _c("列表响应解析", len(lst) == 2 and hm is True and cur == 999)

    lst2, hm2, _ = _extract_list({"aweme_list": [], "has_more": 0})
    _c("空列表+到底", lst2 == [] and hm2 is False)

    r4 = normalize("MS4wLjABAAAAdirect_uid_1234567890")
    _c("裸 sec_uid 识别", r4["kind"] == "profile")

    _c("接口识别", _looks_like_list(
        "https://www.douyin.com/aweme/v1/web/aweme/post/?sec_user_id=x"))
    _c("非列表接口排除", not _looks_like_list(
        "https://www.douyin.com/aweme/v1/web/aweme/detail/"))

    print(f"\nRESULT: {'ALL PASS' if ok else 'FAIL'}")
    return ok
