"""B 站视频下载（单条 + 多P）。

为什么 B 站比视频号/抖音都好办（2026-11-09 实测，v3.2.0）：

  · `x/web-interface/view` 带 Referer + 桌面 UA 就能匿名调，返回 code=0
  · `x/player/playurl` 匿名调用 **不需要 WBI 签名**（w_rid/wts）：
      fnval=0 → `durl` 只 1 段，**音视频合一的 mp4**，不需要 ffmpeg 合流
      fnval=16 → `dash` 音视频分离，而且匿名只给到 480P/360P，反而更差
  · CDN **强校验 Referer**：不带 `https://www.bilibili.com/` → 403；
    带上 → 206 + `Accept-Ranges: bytes`，Range 续传天然可用
  · 匿名清晰度上限 720P（quality=64）；1080P+/4K/HDR 需登录 SESSDATA，
    大会员档还要大会员账号 —— 本模块不碰登录态，如实告知实际给到的清晰度

所以本模块**不挂代理、不装证书、不啃签名**，就是三个 HTTP 请求 + 落盘。

已实测的坑：
  · 流地址有时效（约 2 小时）→ 每次下载都**现取**，不缓存地址
  · 主地址挂了还有 `backup_url`（通常 2 个）→ 按顺序轮换
  · 地址过期表现为 403 → 重新 playurl 再试，**不是**把断点删掉
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import requests

from .logger import logger

# ────────────────── 常量 ──────────────────
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
REFERER = "https://www.bilibili.com/"
API_VIEW = "https://api.bilibili.com/x/web-interface/view"
API_PLAYURL = "https://api.bilibili.com/x/player/playurl"

#: 清晰度代号 → 人话。接口 `accept_quality` 会给候选列表，
#: 但**实际返回哪一档**要看 `data.quality`，两者经常不一致（匿名会被压档）。
QUALITY_NAMES = {
    127: "8K", 126: "杜比视界", 125: "HDR", 120: "4K",
    116: "1080P60", 112: "1080P+", 80: "1080P",
    74: "720P60", 64: "720P", 32: "480P", 16: "360P", 6: "240P",
}

#: 一次请求的超时（秒）。B 站 CDN 握手慢的时候 15s 会误判超时。
API_TIMEOUT = 20
DL_TIMEOUT = 30

_UA_HEADERS = {"User-Agent": UA, "Referer": REFERER}

# Windows 文件名非法字符
_BAD_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t\x00-\x1f]')
_DOTS = re.compile(r"[. ]+$")


# ────────────────── 数据结构 ──────────────────
@dataclass
class BiliPart:
    """一个分P（单P视频也有一个 part，index=1）。"""
    index: int
    cid: int
    title: str
    duration: int = 0          # 秒
    bvid: str = ""
    #: 实测拿到的清晰度代号（下载时才知道，0 = 还没取）
    quality: int = 0
    #: 实测拿到的流大小（字节，0 = 未知）
    size: int = 0


@dataclass
class BiliVideo:
    bvid: str
    aid: int = 0
    title: str = ""
    owner: str = ""
    duration: int = 0
    parts: list = field(default_factory=list)
    #: 发布日期（YYYY-MM-DD），只用于展示
    pubdate: str = ""

    @property
    def multi(self) -> bool:
        return len(self.parts) > 1


# ────────────────── 链接解析 ──────────────────
_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
_AV_RE = re.compile(r"(?:^|[^0-9A-Za-z])av(\d{1,12})(?![0-9])", re.I)
_SHORT_RE = re.compile(r"https?://b23\.tv/[0-9A-Za-z]+", re.I)
_URL_RE = re.compile(r"https?://[^\s'\"<>，。；、）】]+", re.I)
_PAGE_RE = re.compile(r"[?&]p=(\d+)")


def extract_links(text: str) -> list:
    """从一整段粘贴文本里抠出 B 站链接/BV 号。

    支持：完整链接、短链 b23.tv、裸 BV 号、av 号。
    分享文案里带的一堆废话（"【标题】 https://b23.tv/xxx"）也能抠出来。
    """
    out, seen = [], set()

    def _add(x: str):
        k = x.strip()
        if k and k not in seen:
            seen.add(k)
            out.append(k)

    for m in _SHORT_RE.finditer(text or ""):
        _add(m.group(0))
    for m in _URL_RE.finditer(text or ""):
        u = m.group(0)
        if "bilibili.com" in u or "b23.tv" in u:
            _add(u)
    for m in _BV_RE.finditer(text or ""):
        _add(m.group(1))
    for m in _AV_RE.finditer(text or ""):
        _add("av" + m.group(1))
    return out


def parse_target(s: str) -> dict:
    """把一个输入串解析成 {'kind', 'bvid', 'aid', 'page'}。

    kind: video（能直接拿 BV/av）/ short（b23.tv 要跟随重定向）/ unknown
    """
    s = (s or "").strip()
    page = 0
    m = _PAGE_RE.search(s)
    if m:
        try:
            page = int(m.group(1))
        except ValueError:
            page = 0

    if _SHORT_RE.search(s):
        return {"kind": "short", "bvid": "", "aid": 0, "page": page, "url": s}

    m = _BV_RE.search(s)
    if m:
        return {"kind": "video", "bvid": m.group(1), "aid": 0, "page": page,
                "url": s}
    m = _AV_RE.search(s)
    if m:
        return {"kind": "video", "bvid": "", "aid": int(m.group(1)), "page": page,
                "url": s}
    return {"kind": "unknown", "bvid": "", "aid": 0, "page": page, "url": s}


def resolve_short(url: str, sess: Optional[requests.Session] = None) -> dict:
    """跟随 b23.tv 短链重定向，拿到真实链接里的 BV / av / p。

    短链是 B 站分享面板给的（手机端最常见），不跟一次就拿不到 BV。
    """
    s = sess or _sess()
    try:
        r = s.get(url, headers=_UA_HEADERS, timeout=API_TIMEOUT,
                  allow_redirects=True)
        final = r.url or ""
    except Exception as e:                     # noqa: BLE001
        logger.warn(f"B 站短链解析失败 {url}: {type(e).__name__}: {e}")
        return {"kind": "unknown", "bvid": "", "aid": 0, "page": 0,
                "url": url, "error": f"{type(e).__name__}: {e}"}
    t = parse_target(final)
    t["url"] = final
    t["resolved_from"] = url
    return t


def _sess() -> requests.Session:
    s = requests.Session()
    s.headers.update(_UA_HEADERS)
    return s


# ────────────────── 元信息 ──────────────────
def fetch_view(bvid: str = "", aid: int = 0,
               sess: Optional[requests.Session] = None) -> dict:
    """取视频元信息 + 分P 列表。返回 {'ok', 'video'|'error'}。"""
    s = sess or _sess()
    params = {"bvid": bvid} if bvid else {"aid": aid}
    try:
        r = s.get(API_VIEW, params=params, timeout=API_TIMEOUT)
        j = r.json()
    except Exception as e:                     # noqa: BLE001
        logger.warn(f"B 站 view 接口异常 {bvid or aid}: "
                    f"{type(e).__name__}: {e}")
        return {"ok": False, "error": f"网络错误：{type(e).__name__}"}

    code = j.get("code")
    if code != 0:
        # -404 稿件不存在 / -403 权限 / -352 风控
        msg = j.get("message") or f"code={code}"
        return {"ok": False, "error": f"接口拒绝（{code}）：{msg}"}

    d = j.get("data") or {}
    v = BiliVideo(
        bvid=d.get("bvid") or bvid,
        aid=int(d.get("aid") or aid or 0),
        title=(d.get("title") or "").strip(),
        owner=((d.get("owner") or {}).get("name") or "").strip(),
        duration=int(d.get("duration") or 0),
    )
    ts = d.get("pubdate")
    if ts:
        try:
            v.pubdate = time.strftime("%Y-%m-%d", time.localtime(int(ts)))
        except (ValueError, OSError) as e:
            logger.warn(f"B 站发布时间解析失败 {ts}: {e}")

    pages = d.get("pages") or []
    if pages:
        for i, p in enumerate(pages, 1):
            v.parts.append(BiliPart(
                index=int(p.get("page") or i),
                cid=int(p.get("cid") or 0),
                title=(p.get("part") or v.title or f"P{i}").strip(),
                duration=int(p.get("duration") or 0),
                bvid=v.bvid,
            ))
    else:
        # 没有 pages（很少见）→ 退化成单P，用 cid 字段兜底
        cid = int(d.get("cid") or 0)
        if cid:
            v.parts.append(BiliPart(index=1, cid=cid, title=v.title,
                                    duration=v.duration, bvid=v.bvid))
    return {"ok": True, "video": v}


# ────────────────── 取播放地址 ──────────────────
def fetch_playurl(bvid: str, cid: int, want_qn: int = 80,
                  sess: Optional[requests.Session] = None) -> dict:
    """取**单文件 mp4** 的直链（fnval=0，音视频合一，不需要 ffmpeg）。

    返回 {'ok', 'url', 'backups', 'size', 'quality', 'quality_name', 'error'}

    ★ 为什么固定 fnval=0：
      实测 fnval=16（DASH）匿名只返回 480P/360P 的视频流，不如 fnval=0
      的 720P 单文件；而且 DASH 要下两条（音+视频）再合流，客户机器上
      未必有 ffmpeg —— 少一个依赖就少一批装不上的客户。
    """
    s = sess or _sess()
    params = {
        "bvid": bvid,
        "cid": int(cid),
        "qn": int(want_qn),
        "fnval": 0,          # ← 关键：要 durl 单文件，不要 dash
        "fourk": 1,
    }
    try:
        r = s.get(API_PLAYURL, params=params, timeout=API_TIMEOUT)
        j = r.json()
    except Exception as e:                     # noqa: BLE001
        logger.warn(f"B 站 playurl 接口异常 {bvid}/{cid}: "
                    f"{type(e).__name__}: {e}")
        return {"ok": False, "error": f"网络错误：{type(e).__name__}"}

    code = j.get("code")
    if code != 0:
        return {"ok": False,
                "error": f"取流被拒（{code}）：{j.get('message') or ''}"}

    d = j.get("data") or {}
    durl = d.get("durl") or []
    if not durl:
        # 极少数稿件（互动视频/番剧）不给 durl
        return {"ok": False,
                "error": "这个稿件没有返回单文件流（可能是互动视频或番剧）"}

    first = durl[0]
    url = first.get("url") or ""
    backups = list(first.get("backup_url") or [])
    if not url:
        return {"ok": False, "error": "接口返回了空地址"}

    q = int(d.get("quality") or 0)
    return {
        "ok": True,
        "url": url,
        "backups": backups,
        "size": int(first.get("size") or 0),
        "quality": q,
        "quality_name": QUALITY_NAMES.get(q, f"{q}"),
        "format": d.get("format") or "",
    }


# ────────────────── 文件名 ──────────────────
def safe_name(s: str, limit: int = 80) -> str:
    """把标题变成 Windows 能用的文件名（保留中文，去掉非法字符）。"""
    t = _BAD_CHARS.sub("_", str(s or "")).strip()
    t = _DOTS.sub("", t)
    t = re.sub(r"_{2,}", "_", t)
    if len(t) > limit:
        t = t[:limit].rstrip("_ ")
    return t or "bilibili"


def target_filename(video: BiliVideo, part: BiliPart) -> str:
    """落盘文件名。

    · 单P：`标题_BV号.mp4`
    · 多P：`标题_P2_分P标题_BV号.mp4`（带上分P标题，不然下完分不清第几集）
    """
    base = safe_name(video.title, 60)
    if video.multi:
        p_title = safe_name(part.title, 30)
        if p_title and p_title != base:
            return f"{base}_P{part.index}_{p_title}_{video.bvid}.mp4"
        return f"{base}_P{part.index}_{video.bvid}.mp4"
    return f"{base}_{video.bvid}.mp4"


# ────────────────── 下载 ──────────────────
def _part_size(url: str, sess: requests.Session
               ) -> tuple:
    """用 Range 试探拿总大小和支持情况。返回 (total, accepts_range)。"""
    try:
        r = sess.get(url, headers={**_UA_HEADERS, "Range": "bytes=0-0"},
                     timeout=API_TIMEOUT, stream=True)
        try:
            cr = r.headers.get("Content-Range") or ""
            if r.status_code == 206 and "/" in cr:
                try:
                    return int(cr.rsplit("/", 1)[-1]), True
                except ValueError as e:
                    logger.warn(f"B 站 Content-Range 解析失败 {cr}: {e}")
            cl = r.headers.get("Content-Length")
            if r.status_code == 200 and cl:
                return int(cl), False
        finally:
            r.close()
    except Exception as e:                     # noqa: BLE001
        logger.warn(f"B 站探测流大小失败：{type(e).__name__}: {e}")
    return 0, True


def download_part(
    video: BiliVideo,
    part: BiliPart,
    dest_dir,
    *,
    want_qn: int = 80,
    on_progress: Optional[Callable[[int, int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    on_log: Optional[Callable[[str, str], None]] = None,
    gap: float = 0.0,
) -> dict:
    """下载一个分P。返回 {'ok', 'path', 'size', 'quality_name', 'error'}。

    断点续传：边下边写 `<文件名>.part`，中断后**保留**，下次用 Range 从
    已有字节接着下。文件名由「标题 + 分P + bvid」决定，是稳定的 ——
    这条是 v3.1.5 踩过的坑（P50）：临时名带时间戳就永远续不上。

    地址过期：CDN 强校验 Referer，且地址约 2 小时失效。到期后表现为
    403/416 → 不删断点，重新 playurl 拿新地址再续。
    """
    log = on_log or (lambda lv, m: None)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / target_filename(video, part)
    part_file = dest.with_suffix(dest.suffix + ".part")

    if dest.exists() and dest.stat().st_size > 0:
        return {"ok": True, "path": str(dest), "size": dest.stat().st_size,
                "quality_name": "", "skipped": True}

    sess = _sess()
    attempts = 0
    refreshed = 0
    last_err = ""
    url_tries = 0
    info = None            # 地址缓存：只在过期/首次时重新取，少打接口

    while attempts < 3:
        attempts += 1
        if should_stop and should_stop():
            return {"ok": False, "path": str(part_file), "size": 0,
                    "error": "已停止（断点已保留）"}

        if info is None:
            info = fetch_playurl(video.bvid, part.cid, want_qn, sess)
            if not info.get("ok"):
                last_err = info.get("error") or "取流失败"
                log("warn", f"  取流失败（第 {attempts} 次）：{last_err}")
                time.sleep(1.5 * attempts)
                continue
            part.quality = info.get("quality") or 0
            if info.get("size"):
                part.size = int(info["size"])

        urls = [info["url"]] + list(info.get("backups") or [])
        ok = False
        for u in urls:
            url_tries += 1
            ok, err, got = _stream_once(u, part_file, sess,
                                        on_progress=on_progress,
                                        should_stop=should_stop)
            if ok:
                break
            if err == "stopped":
                # 用户点了停止：别去试备用地址，也别重试 —— 断点留着
                return {"ok": False, "path": str(part_file),
                        "size": got or 0, "error": "已停止（断点已保留）"}
            last_err = err
            if err == "expired":
                # 地址失效 → 同一批备用地址一起过期，跳出换新地址
                info = None
                break
            log("warn", f"  地址 {url_tries} 失败：{err}，换备用地址")
        if ok:
            try:
                os.replace(part_file, dest)
            except OSError as e:
                logger.warn(f"B 站落盘改名失败 {part_file} → {dest}: {e}")
                return {"ok": False, "path": str(part_file), "size": 0,
                        "error": f"改名失败：{e}"}
            size = dest.stat().st_size
            if gap:
                time.sleep(gap)          # 拟人间隔，别把 CDN 打急
            return {"ok": True, "path": str(dest), "size": size,
                    "quality_name": info.get("quality_name") or "",
                    "quality": info.get("quality") or 0}

        if info is None:
            refreshed += 1
            if refreshed > 2:
                return {"ok": False, "path": str(part_file), "size": 0,
                        "error": "地址反复失效（重取 2 次仍失败）"}
            log("info", "  地址已过期，重新取流后续传")
            continue
        time.sleep(1.5 * attempts)

    return {"ok": False, "path": str(part_file), "size": 0,
            "error": last_err or "下载失败"}


def _stream_once(url: str, part_file: Path, sess: requests.Session,
                 *, on_progress=None, should_stop=None):
    """单次流式下载（带 Range 续传）。返回 (ok, err, got_bytes)。

    err 里的 "expired" 是给上层看的信号：地址过期，要重新取流。
    """
    pos = part_file.stat().st_size if part_file.exists() else 0
    headers = dict(_UA_HEADERS)
    if pos:
        headers["Range"] = f"bytes={pos}-"

    try:
        r = sess.get(url, headers=headers, timeout=DL_TIMEOUT, stream=True)
    except Exception as e:                     # noqa: BLE001
        logger.warn(f"B 站下载连接失败：{type(e).__name__}: {e}")
        return False, f"连接失败：{type(e).__name__}", 0

    try:
        if r.status_code in (403, 401):
            return False, "expired", 0
        if r.status_code == 416:
            # 起点已在末尾 → 当成下完
            return True, "", pos
        if r.status_code >= 400:
            return False, f"HTTP {r.status_code}", 0

        total = 0
        cr = r.headers.get("Content-Range") or ""
        if "/" in cr:
            try:
                total = int(cr.rsplit("/", 1)[-1])
            except ValueError as e:
                logger.warn(f"B 站 Content-Range 解析失败 {cr}: {e}")
        if not total:
            cl = r.headers.get("Content-Length")
            if cl and cl.isdigit():
                total = int(cl) + (pos if r.status_code == 206 else 0)

        mode = "ab" if r.status_code == 206 and pos else "wb"
        if mode == "wb":
            pos = 0
        got = pos
        with open(part_file, mode) as f:
            for chunk in r.iter_content(256 * 1024):
                if should_stop and should_stop():
                    return False, "stopped", got
                if not chunk:
                    continue
                f.write(chunk)
                got += len(chunk)
                if on_progress:
                    try:
                        on_progress(got, total or got)
                    except Exception as e:     # noqa: BLE001
                        logger.warn(f"B 站进度回调异常：{e}")
        if total and got < total:
            return False, f"只下到 {got}/{total}", got
        return True, "", got
    except Exception as e:                     # noqa: BLE001
        logger.warn(f"B 站下载中断 {url[:60]}: {type(e).__name__}: {e}")
        return False, f"中断：{type(e).__name__}", 0
    finally:
        try:
            r.close()
        except Exception as e:                 # noqa: BLE001
            logger.warn(f"B 站下载流关闭失败：{e}")


def fmt_size(n: int) -> str:
    n = int(n or 0)
    if n >= 1073741824:
        return f"{n / 1073741824:.2f} GB"
    if n >= 1048576:
        return f"{n / 1048576:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


def fmt_dur(sec: int) -> str:
    sec = int(sec or 0)
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
