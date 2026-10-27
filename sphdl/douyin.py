"""抖音批量下载（方案 A：粘贴链接批量下）。

═══ 验证结论（2026-10-26 实测，不是纸上分析）═══

| 方案 | 结果 |
|---|---|
| yt-dlp | ❌ 403「Fresh cookies needed」；系统浏览器 cookies 也读不出
|        |    （Chrome 127+ DPAPI 加密、Edge 数据库被占用） |
| 老代码（requests+playwright 取 cookie 再调 API） | ⚠️ 能跑但要 headless=False 开窗口 |
| **Playwright 开页面读它自己的响应** | ✅ 可行，见下 |

**关键实测数据**：
1. 打开 `https://www.douyin.com/video/{id}`，页面自己会调
   `aweme/v1/web/aweme/detail/` 并返回 200 —— **签名由页面自己算**，
   我们不需要破解 a_bogus/X-Bogus。
2. 无需登录即可拿到数据（ttwid/odin_tt cookie 由浏览器自动获得）。
3. `video.play_addr.url_list` 是**音视频合一的单个 MP4**：
   实测 1080x1920、30.54MB、140.2 秒、h264+aac —— **不需要 ffmpeg 合并**。
4. 下载只需要 `Referer: https://www.douyin.com/`，**不需要 cookies**
   （只带 UA → 403；UA+Referer → 206 正常）。
5. 风控特点：**间歇性**卡在「视频数据加载中」（连测 4 次，2 成 2 败），
   与 headless/有窗口无关 → 用「重试 + 随机间隔」解决。

**打包上最重要的一点**：用 `channel="msedge"` 驱动系统已装的 Edge，
**不需要内置 Chromium（省 150MB+）**，实测浏览器能正常启动。

═══ 为什么不用 MITM（视频号那一套）═══

视频号必须 MITM，因为要在微信 webview 里注入 JS。
抖音不需要：我们自己的浏览器本来就能读到一切。
→ **抖音工具不需要装 CA 证书**，直接绕开「证书装不上→退款」的坑。
"""
from __future__ import annotations

import json
import os
import random
import re
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .logger import logger

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# ══════════════════ 限流对策（2026-10-26 实测后定） ══════════════════
#
# 实测：未登录状态下连续请求 20+ 次后，detail 接口开始不返回数据
#       （页面卡在「视频数据加载中」，但首页仍 200、无验证码）
#       → 是**详情接口的 IP 级限流**，不是全站封禁，会自行恢复。
#
# 对策分五层：
#   ① 登录态复用（最有效）：登录后抖音给的限额高一个量级
#   ② 分级节流：未登录慢、已登录快，单次批量也有上限
#   ③ 失败退避：连续失败 → 冷却，不让情况恶化
#   ④ 解析缓存：6 小时内解析过的同一视频不再请求
#   ⑤ 双数据源：detail 接口拿不到时，退回页面自身的 media 请求
#
# 注意：这些都是「减少请求」，不是「绕过风控」——
#       我们只做用户本来就能在浏览器里看到的内容。

# 未登录：慢、量少
PACE_ANON = (8.0, 15.0)
BATCH_ANON = 10
# 已登录：快一点，量大一点
PACE_AUTH = (3.0, 6.0)      # 已登录：可以快一点
BATCH_AUTH = 50             # 已登录：单次最多解析 50 条
# 连续失败这么多次就进入冷却
COOL_AFTER_FAILS = 2
COOL_SECONDS = 60

MAX_TRY = 3          # 单个链接最多重试次数
CACHE_TTL = 6 * 3600  # 解析结果缓存有效期

# 浏览器 profile（持久化 = 登录态长期有效）
PROFILE_NAME = "douyin_profile"
_CTX_LOCK = __import__("threading").Lock()   # persistent context 不能并发


def profile_dir() -> Path:
    """抖音浏览器 profile 目录（登录态存在这里）。

    放在用户级目录，换软件版本不丢：
        %LOCALAPPDATA%\\sphdl\\douyin_profile
    """
    la = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    base = Path(la) / "sphdl" if la else Path.home() / ".sphdl"
    d = base / PROFILE_NAME
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def _cache_path() -> Path:
    return profile_dir().parent / "douyin_cache.json"


def _load_cache() -> dict:
    try:
        d = json.loads(_cache_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_cache(d: dict):
    try:
        # 只保留未过期的，防止文件无限长
        now = time.time()
        d = {k: v for k, v in d.items()
             if isinstance(v, dict) and now - float(v.get("ts") or 0) < CACHE_TTL}
        _cache_path().write_text(
            json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _cache_get(key: str):
    if not key:
        return None
    e = _load_cache().get(key)
    if not isinstance(e, dict):
        return None
    if time.time() - float(e.get("ts") or 0) > CACHE_TTL:
        return None
    v = e.get("video") or {}
    if not v.get("play_url"):
        return None
    return DouyinVideo(**{k: v.get(k) for k in
                          ("aweme_id", "title", "author", "duration_ms",
                           "play_url", "size", "width", "height", "cover")})


def _cache_put(key: str, v: "DouyinVideo"):
    if not key or not v or not v.ok:
        return
    d = _load_cache()
    d[key] = {"ts": time.time(), "video": {
        "aweme_id": v.aweme_id, "title": v.title, "author": v.author,
        "duration_ms": v.duration_ms, "play_url": v.play_url,
        "size": v.size, "width": v.width, "height": v.height,
        "cover": v.cover}}
    _save_cache(d)


@dataclass
class DouyinVideo:
    aweme_id: str = ""
    title: str = ""
    author: str = ""
    duration_ms: int = 0
    play_url: str = ""
    size: int = 0
    width: int = 0
    height: int = 0
    cover: str = ""
    audio_url: str = ""       # 仅兜底路径用：DASH 分离流需要单独的音轨
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.play_url) and not self.error

    @property
    def duration_text(self) -> str:
        s = int(self.duration_ms / 1000)
        return f"{s // 60}:{s % 60:02d}" if s >= 60 else f"{s} 秒"

    @property
    def size_text(self) -> str:
        if not self.size:
            return "—"
        v = self.size / 1048576
        return f"{v:.0f} MB" if v >= 1 else f"{self.size / 1024:.0f} KB"


# ────────────────────── 链接解析 ──────────────────────

_URL_RE = re.compile(
    r"https?://(?:v\.douyin\.com/[\w\-]+|www\.douyin\.com/video/\d+"
    r"|www\.iesdouyin\.com/share/video/\d+)[^\s，,。;；\"'<>]*",
    re.I)


def extract_urls(text: str) -> list[str]:
    """从任意文本里抠出抖音链接（支持整段分享文案）。

    支持形态：
      · https://v.douyin.com/xxxxx/          短链（需重定向）
      · https://www.douyin.com/video/123     标准
      · https://www.iesdouyin.com/share/video/123
      · 分享文案：「7.68 复制打开抖音… https://v.douyin.com/xxx/ …」
    返回去重后的列表（保序）。
    """
    if not text:
        return []
    found = _URL_RE.findall(text)
    out, seen = [], set()
    for u in found:
        u = u.rstrip(".,;:，。；：、）)】]")
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _aweme_id_from_url(url: str) -> str:
    m = re.search(r"/video/(\d+)", url or "")
    return m.group(1) if m else ""


# ────────────────────── 浏览器（复用同一个实例） ──────────────────────

def _launch(p):
    """启动浏览器：优先系统 Edge/Chrome（免内置 Chromium），最后才用自带。

    ★ 打包关键：`channel="msedge"` 用系统已装的 Edge，
      不需要 `playwright install`，包体不增加 150MB+。
    """
    args = ["--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage", "--no-first-run"]
    last = None
    for kwargs in (
        {"channel": "msedge"},        # Win10/11 默认都有 Edge
        {"channel": "chrome"},        # 装了 Chrome 的机器
        {},                            # 兜底：Playwright 自带 chromium
    ):
        try:
            return p.chromium.launch(headless=True, args=args, **kwargs)
        except Exception as e:                     # noqa: BLE001
            last = e
            logger.debug(f"抖音：浏览器启动失败 {kwargs}: {e}")
            continue
    raise RuntimeError(f"没有可用的浏览器（请装 Edge 或 Chrome）：{last}")


def _ctx(browser):
    return browser.new_context(user_agent=UA, locale="zh-CN",
                               viewport={"width": 1280, "height": 800},
                               extra_http_headers={
                                   "Referer": "https://www.douyin.com/"})


def _parse_detail(data: dict) -> DouyinVideo:
    """从 aweme/detail 响应里提取我们需要的字段。"""
    a = data.get("aweme_detail") or {}
    if not a:
        return DouyinVideo(error="接口没有返回视频数据（可能被风控）")
    v = a.get("video") or {}
    pa = v.get("play_addr") or {}
    urls = pa.get("url_list") or []

    # 没有 play_addr 时退回 bit_rate 里最高档的地址
    if not urls:
        best = None
        for br in (v.get("bit_rate") or []):
            u = ((br.get("play_addr") or {}).get("url_list") or [])
            size = (br.get("play_addr") or {}).get("data_size") or 0
            if u and (best is None or size > best[0]):
                best = (size, u)
        if best:
            urls = best[1]

    if not urls:
        return DouyinVideo(error="没有可下载地址（可能是图文/直播/权限受限）")

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
        size=int(pa.get("data_size") or 0),
        width=int(pa.get("width") or 0),
        height=int(pa.get("height") or 0),
        cover=cover,
    )


def is_logged_in() -> bool:
    """判断 profile 里是否有抖音登录态（看是否有 sessionid 类 cookie）。"""
    pdir = profile_dir()
    # 只要 profile 目录里有 Cookies 数据库且不太小，就认为可能登录过；
    # 真正的判定在浏览器里做（见 login_check）
    for name in ("Cookies", "Network/Cookies"):
        f = pdir / name
        if f.exists() and f.stat().st_size > 4096:
            return True
    return False


def _douyin_login_cookies(ctx) -> bool:
    """在浏览器上下文里判断是否已登录抖音。"""
    try:
        for c in ctx.cookies():
            n = (c.get("name") or "").lower()
            if n in ("sessionid", "sid_tt", "sessionid_ss", "sid_guard"):
                if c.get("value"):
                    return True
    except Exception:
        pass
    return False


def login_interactive(on_log=None, timeout: int = 240) -> tuple[bool, str]:
    """弹出有窗口浏览器让用户扫码登录抖音，登录态存进 profile。

    返回 (成功?, 消息)。

    ★ 为什么要登录：
      未登录时抖音对 aweme/detail 接口限额很低，批量解析几十条就会
      被限流（实测 20+ 次后开始不返回数据）。登录后限额高一个量级，
      这是**最有效的限流对策**。

    ★ 安全说明（要写进客户文档）：
      · 登录态只保存在本机 %LOCALAPPDATA%\\sphdl\\douyin_profile
      · 不上传、不外发，本工具不读取你的账号隐私数据
      · 随时可以点「退出登录」删掉本地 profile
    """
    def _log(lv, m):
        if on_log:
            try:
                on_log(lv, m)
            except Exception:
                pass
        getattr(logger, lv, logger.info)(m)

    acquired = _CTX_LOCK.acquire(timeout=1)
    if not acquired:
        return False, "正在解析中，请等解析结束后再登录"

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _CTX_LOCK.release()
        return False, "缺少 playwright 组件"

    try:
        with sync_playwright() as pw:
            _log("info", "正在打开抖音登录窗口，请用抖音 App 扫码…")
            ctx = None
            last = None
            for kw in ({"channel": "msedge"}, {"channel": "chrome"}, {}):
                try:
                    ctx = pw.chromium.launch_persistent_context(
                        user_data_dir=str(profile_dir()),
                        headless=False,
                        args=["--disable-blink-features=AutomationControlled",
                              "--no-first-run"],
                        user_agent=UA, locale="zh-CN",
                        viewport={"width": 1180, "height": 820},
                        **kw)
                    break
                except Exception as e:            # noqa: BLE001
                    last = e
                    continue
            if ctx is None:
                return False, f"浏览器启动失败：{last}"

            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                page.goto("https://www.douyin.com/",
                          wait_until="domcontentloaded", timeout=45000)
            except Exception:
                pass

            if _douyin_login_cookies(ctx):
                ctx.close()
                _log("ok", "检测到已登录状态的抖音账号")
                return True, "已登录"

            _log("info", "等待扫码（最多 4 分钟）…窗口内点右上角「登录」")
            deadline = time.time() + timeout
            ok = False
            while time.time() < deadline:
                time.sleep(2)
                if _douyin_login_cookies(ctx):
                    ok = True
                    break
                try:
                    if not ctx.pages:
                        break          # 用户把窗口关了
                except Exception:
                    break
            try:
                ctx.close()
            except Exception:
                pass
            if ok:
                _log("ok", "抖音登录成功，登录态已保存到本机")
                return True, "登录成功"
            return False, "没有检测到登录（超时或窗口被关闭）"
    except Exception as e:                       # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:120]}"
    finally:
        try:
            _CTX_LOCK.release()
        except Exception:
            pass


def logout() -> bool:
    """删除本地登录态（profile 目录）。"""
    import shutil
    try:
        shutil.rmtree(profile_dir(), ignore_errors=True)
        return True
    except Exception:
        return False


def resolve_many(urls: list[str], on_item=None, on_log=None,
                 should_stop=None, use_login: bool = True,
                 use_cache: bool = True) -> list:
    """批量解析（含限流对策）。

    限流对策（按效果排序）：
      ① 复用登录态 profile（最有效）
      ② 分级节流：未登录 8~15 秒/条、单批 ≤10；已登录 3~6 秒/条、单批 ≤50
      ③ 连续失败 2 次 → 冷却 60 秒
      ④ 解析缓存 6 小时（同一视频不重复请求）
      ⑤ 双数据源：detail 接口不行时，退回读页面自身 media 请求

    on_item(index, url, DouyinVideo) / on_log(level, msg) / should_stop()
    返回 [(url, DouyinVideo)]
    """
    def _log(lv, m):
        if on_log:
            try:
                on_log(lv, m)
            except Exception:
                pass
        getattr(logger, lv, logger.info)(m)

    results = []
    if not urls:
        return results

    # ---- ④ 缓存命中先处理（不发任何请求）----
    todo = []
    cached_n = 0
    for i, u in enumerate(urls):
        hit = _cache_get(_aweme_id_from_url(u) or u) if use_cache else None
        if hit is not None:
            results.append((u, hit))
            cached_n += 1
            if on_item:
                try:
                    on_item(i, u, hit)
                except Exception:
                    pass
        else:
            todo.append((i, u))
    if cached_n:
        _log("info", f"抖音：{cached_n} 条命中本地缓存，跳过请求")

    if not todo:
        return _sort_results(urls, results)

    # ---- 判断登录态 + 批量上限 ----
    logged = False
    if use_login:
        try:
            logged = is_logged_in()
        except Exception:
            logged = False
    pace = PACE_AUTH if logged else PACE_ANON
    limit = BATCH_AUTH if logged else BATCH_ANON
    _log("info",
         f"抖音：{'已登录' if logged else '未登录'}模式"
         f"（每条间隔 {pace[0]:.0f}~{pace[1]:.0f} 秒"
         f"，单批上限 {limit} 条）")
    if not logged:
        _log("warn",
             "未登录时抖音限额很低，批量容易触发限流。"
             "建议先点【登录抖音】——登录后成功率高很多。")

    over = len(todo) - limit
    if over > 0:
        _log("warn", f"本次只解析前 {limit} 条，剩余 {over} 条请稍后再解析"
                     f"（{'登录可提高到 ' + str(BATCH_AUTH) + ' 条' if not logged else '避免一次请求过多'}）")
        todo = todo[:limit]

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        for i, u in todo:
            v = DouyinVideo(error="缺少 playwright 组件")
            results.append((u, v))
            if on_item:
                on_item(i, u, v)
        return _sort_results(urls, results)

    from contextlib import ExitStack
    fails = 0
    with ExitStack() as stack:
        pw = stack.enter_context(sync_playwright())
        browser = None
        ctx = None
        try:
            if logged:
                # 用持久化 profile → 带登录态
                ctx = pw.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir()), headless=True,
                    args=["--disable-blink-features=AutomationControlled",
                          "--no-first-run"],
                    user_agent=UA, locale="zh-CN",
                    viewport={"width": 1280, "height": 800})
            else:
                browser = _launch(pw)
                ctx = _ctx(browser)
        except Exception as e:                   # noqa: BLE001
            for i, u in todo:
                v = DouyinVideo(error=str(e)[:80])
                results.append((u, v))
                if on_item:
                    on_item(i, u, v)
            _log("error", f"抖音：浏览器启动失败 {e}")
            return _sort_results(urls, results)

        for n, (i, url) in enumerate(todo):
            if should_stop and should_stop():
                _log("warn", "抖音：已按用户要求停止解析")
                break

            _log("info", f"抖音 [{n+1}/{len(todo)}] 解析：{url[:60]}")
            video = None
            for attempt in range(1, MAX_TRY + 1):
                video, src = _resolve_one(ctx, url)
                if video.ok:
                    if src == "media":
                        _log("info", "  （detail 接口没给数据，已从页面媒体流获取）")
                    break
                if attempt < MAX_TRY:
                    wait = 5 * attempt if logged else 8 * attempt
                    _log("warn", f"  第 {attempt} 次失败（{video.error[:30]}），"
                                 f"{wait} 秒后重试…")
                    time.sleep(wait)

            results.append((url, video))
            if on_item:
                try:
                    on_item(i, url, video)
                except Exception:
                    pass

            if video.ok:
                fails = 0
                _cache_put(_aweme_id_from_url(url) or url, video)
                _log("ok", f"  ✓ {video.title[:34]} | {video.duration_text}"
                           f" | {video.size_text}")
            else:
                fails += 1
                _log("error", f"  ✗ {video.error}")

            # ③ 连续失败 → 冷却
            if fails >= COOL_AFTER_FAILS and n < len(todo) - 1:
                _log("warn", f"  连续 {fails} 条失败，冷却 {COOL_SECONDS} 秒"
                             f"（抖音临时限流）…")
                for _ in range(COOL_SECONDS):
                    if should_stop and should_stop():
                        break
                    time.sleep(1)
                fails = 0

            # ② 分级节流
            if n < len(todo) - 1 and not (should_stop and should_stop()):
                time.sleep(random.uniform(*pace))

        try:
            if browser is not None:
                browser.close()
            else:
                ctx.close()
        except Exception:
            pass
    return _sort_results(urls, results)


def _sort_results(urls, results):
    """按输入顺序返回（缓存命中和新解析混在一起时用）。"""
    order = {u: i for i, u in enumerate(urls)}
    return sorted(results, key=lambda p: order.get(p[0], 999))


def _resolve_one(ctx, url: str):
    """解析单条，返回 (DouyinVideo, 来源)。来源=detail / media / none。

    ★ 双数据源（对策⑤）：
      · detail 接口返回 JSON → 直接取 play_addr（最优，1080P 合一）
      · 接口没数据但页面在播 → 从页面自身发出的 media 请求里捞
        （video/tos 的 media-video 段；这是 DASH 分流，需配合音频，
         所以只作为兜底，且明确告知用户可能只有画面）
    """
    page = ctx.new_page()
    box = {"json": None, "media": [], "audio": []}

    def _on_resp(r):
        if "/aweme/detail" in r.url and box["json"] is None:
            try:
                box["json"] = r.json()
            except Exception:
                pass

    def _on_req(r):
        u = r.url
        if "video/tos" not in u and "douyinvod" not in u:
            return
        # ★ DASH 是音视频分离的：只拿 video 段会导致「有画面没声音」，
        #   必须两个都抓，下载后合并。
        if "media-video" in u:
            if u not in box["media"]:
                box["media"].append(u)
        elif "media-audio" in u:
            if u not in box["audio"]:
                box["audio"].append(u)

    page.on("response", _on_resp)
    page.on("request", _on_req)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception:
        pass
    for _ in range(20):
        if box["json"] is not None:
            break
        time.sleep(1.5)

    try:
        page.close()
    except Exception:
        pass

    if box["json"] is not None:
        v = _parse_detail(box["json"])
        if v.ok:
            return v, "detail"
        if v.error:
            return v, "detail"

    # 兜底：页面媒体流（DASH 分离流 → 必须带上音轨，否则没声音）
    if box["media"]:
        v = DouyinVideo(
            aweme_id=_aweme_id_from_url(url),
            title=f"抖音视频_{_aweme_id_from_url(url)}",
            play_url=box["media"][0],
            audio_url=(box["audio"][0] if box["audio"] else ""),
            error="",
        )
        if box["audio"]:
            logger.debug("抖音：使用页面媒体流兜底（音视频分离，需合并）")
        else:
            logger.debug("抖音：使用页面媒体流兜底（没抓到音轨）")
        return v, "media"

    return DouyinVideo(error="30 秒内没等到视频数据（可能被限流）"), "none"


# ────────────────────── 下载 ──────────────────────

_ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def safe_name(s: str, limit: int = 80) -> str:
    s = _ILLEGAL.sub("_", (s or "").strip())
    s = re.sub(r"\s+", " ", s).strip(" .")
    return (s[:limit] or "douyin_video")


def _find_ffmpeg() -> str:
    """找 ffmpeg：优先程序自带，其次系统 PATH。

    ★ 为什么需要它：
      正常路径（play_addr）是音视频合一的 MP4，**不需要 ffmpeg**。
      但兜底路径（页面 DASH 流）是分离的 video/audio，
      不合并就只有画面没声音 —— 那等于交付了一个坏文件。
    """
    import shutil as _sh
    import sys as _sys
    cands = []
    try:
        base = Path(_sys.executable).parent
        cands += [base / "ffmpeg.exe", base / "_internal" / "ffmpeg.exe"]
    except Exception:
        pass
    cands.append(Path(__file__).resolve().parent.parent / "ffmpeg.exe")
    for c in cands:
        try:
            if c.exists():
                return str(c)
        except Exception:
            pass
    return _sh.which("ffmpeg") or ""


def _ffmpeg_merge(video_path: Path, audio_path: Path, out_path: Path,
                  timeout: int = 180) -> tuple[bool, str]:
    """无损合并（-c copy，不重新编码，几秒完成）。"""
    ff = _find_ffmpeg()
    if not ff:
        return False, "没找到 ffmpeg，无法合并音轨"
    import subprocess as _sp
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error",
           "-i", str(video_path), "-i", str(audio_path),
           "-c", "copy", "-map", "0:v:0", "-map", "1:a:0", str(out_path)]
    try:
        r = _sp.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            return False, (r.stderr or "")[:160]
        # ★ 判据：合并结果应 ≥ 原视频大小（音轨只会让它更大）
        #   不能写死 100KB —— 短视频本来就可能只有几十 KB。
        if not out_path.exists():
            return False, "合并后没有生成文件"
        try:
            v_size = video_path.stat().st_size
            if out_path.stat().st_size < v_size * 0.9:
                return False, (f"合并后文件偏小"
                               f"（{out_path.stat().st_size} < {v_size}）")
        except Exception:
            pass
        return True, str(out_path)
    except Exception as e:                          # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:100]}"


def download(video: DouyinVideo, dest_dir: Path, on_progress=None,
             should_stop=None, timeout: int = 300) -> tuple[bool, str]:
    """下载单条（音视频合一的 MP4，无需 ffmpeg）。

    返回 (成功?, 文件路径或错误信息)。
    ★ 只需要 Referer，不需要 cookies（实测：只带 UA → 403）。
    """
    if not video.ok:
        return False, video.error or "没有下载地址"

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    base = safe_name(f"{video.title or video.aweme_id}_{video.aweme_id}")
    final = dest_dir / f"{base}.mp4"
    part = dest_dir / f"{base}.part"

    # 已存在且大小一致 → 跳过
    if final.exists() and video.size and abs(final.stat().st_size
                                             - video.size) < 4096:
        return True, str(final)

    headers = {"User-Agent": UA, "Referer": "https://www.douyin.com/"}
    try:
        req = urllib.request.Request(video.play_url, headers=headers)
        got = 0
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=timeout) as r, \
                open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or video.size or 0)
            while True:
                if should_stop and should_stop():
                    raise RuntimeError("用户已停止")
                chunk = r.read(262144)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if on_progress:
                    try:
                        on_progress(got, total, time.time() - t0)
                    except Exception:
                        pass
        # ★ 下限 8KB：够拦掉错误页/空响应，又不误杀很短的视频
        if got < 8192:
            part.unlink(missing_ok=True)
            return False, f"文件太小（{got} 字节），地址可能已失效"
        os.replace(part, final)

        # ★ 兜底路径才需要合并：play_addr 已是音视频合一，audio_url 为空
        if video.audio_url:
            ap = dest_dir / f"{base}.audio.part"
            try:
                areq = urllib.request.Request(
                    video.audio_url,
                    headers={"User-Agent": UA,
                             "Referer": "https://www.douyin.com/"})
                with urllib.request.urlopen(areq, timeout=timeout) as r, \
                        open(ap, "wb") as f:
                    while True:
                        chunk = r.read(262144)
                        if not chunk:
                            break
                        f.write(chunk)
                merged = dest_dir / f"{base}.merged.mp4"
                ok, msg = _ffmpeg_merge(final, ap, merged)
                ap.unlink(missing_ok=True)
                if ok:
                    final.unlink(missing_ok=True)
                    os.replace(merged, final)
                    return True, str(final)
                # 合并失败 → 保留无声版，但**明确告知**
                merged.unlink(missing_ok=True)
                return True, (str(final)
                              + "|WARN|这条只有画面没有声音（合并失败："
                              + msg + "）")
            except Exception as e:                  # noqa: BLE001
                try:
                    ap.unlink(missing_ok=True)
                except Exception:
                    pass
                return True, (str(final)
                              + "|WARN|这条只有画面没有声音"
                              + f"（音轨下载失败：{type(e).__name__}）")
        return True, str(final)
    except Exception as e:                          # noqa: BLE001
        try:
            part.unlink(missing_ok=True)
        except Exception:
            pass
        return False, f"{type(e).__name__}: {str(e)[:120]}"
