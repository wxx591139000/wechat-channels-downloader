"""小鹅通课程视频下载（单条链接 / 批量链接）。

═══ 为什么小鹅通能下（2026-11-10 实测，v3.4.0）═══

  · 课程页 `{appid}.h5.xiaoeknow.com/p/course/video/{v_id}?product_id={p_id}`
    首次访问会 **302 回自身** 并下发 `anony_token` —— 拿到它就算「匿名会话」，
    之后的接口都不用登录
  · `xe.course.business.video.detail_info.get/2.0.0` 匿名返回 `code:0`，
    `data.video_urls` 是**混淆过的 Base64**（见 `deobf`）
  · 解出来是 m3u8（标准 HLS），`#EXT-X-KEY` 为 **AES-128 + IV 全零**
  · key 走 `app.xiaoe-tech.com/.../kds/api/v1/keys`，**匿名 GET 就返回 16 字节**，
    不需要 userId 二次异或（社区文章有那种说法，这版实测不需要）
  · 分片走 CDN，`Range` 可用、无强 Referer 校验（带上更稳）

═══ 几个反直觉的点（都是实测踩出来的）═══

  ① `is_drm = 1` **不等于** Widevine。小鹅通把自家 AES-128 也叫 drm，
     真 DRM 的课会返回 mpd 而不是 m3u8，这里会明确报错，不会静默失败。
  ② CDN 上**没有裸 mp4**：`v.f421220.mp4` 是 404，只有 HLS。
     所以必须「下载分片 → 解密 → 拼接 → 换容器」，ffmpeg 只做最后一步 remux。
  ③ 混淆串尾巴带 `__ba` 之类噪声，`=` 之后是垃圾。必须**在最后一个 `=` 处截断**，
     做 URL-safe 替换（`_`→`/`）会把噪声变成合法字符再报填充错。
  ④ 单线程下载只有 0.18 MB/s，**并发 8 路 0.93 MB/s** —— 必须并发。
     但别开到 16+，那是给别人 CDN 添堵，也容易触发风控。

═══ 清晰度 ═══

  `video_urls` 一般是 1~3 档（高清/超清/蓝光），实际给哪档由讲师上传的源决定。
  本模块**取最高档**，清不清楚以接口返回为准，不承诺 1080P。
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import requests

from .logger import logger

# ────────────────── 常量 ──────────────────
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")

#: 详情接口（H5 域名下的通用网关，appid 在子域里）
API_DETAIL = ("https://{appid}.h5.xiaoeknow.com"
              "/xe.course.business.video.detail_info.get/2.0.0")

#: 一次请求超时。CDN 握手慢时 15s 会误判，实测 20s 稳。
API_TIMEOUT = 20
DL_TIMEOUT = 30

#: 分片并发数。实测 8 路 0.93 MB/s，再高收益递减且容易惹风控。
DEFAULT_WORKERS = 8

#: 单片失败重试次数（不含首次）
SEG_RETRY = 2

#: 混淆字符表：小鹅通把 base64 的 `+/` 换成了这四个符号
_OBF_MAP = {"@": "1", "#": "2", "$": "3", "%": "4"}

_BAD_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t\x00-\x1f]')
_DOTS = re.compile(r"[. ]+$")


# ────────────────── 数据结构 ──────────────────
@dataclass
class XiaeoVideo:
    """一节课程视频（小鹅通没有分P 概念，一节就是一条）。"""
    appid: str = ""
    resource_id: str = ""
    product_id: str = ""
    title: str = ""
    duration: int = 0              # 秒
    #: 加密串（含 sign/t/us 参数，有时效）—— 不落盘，只在本次下载内使用
    m3u8: str = ""
    quality: str = ""              # 清晰度名（高清/超清…）
    size: int = 0                  # 估计总大小（字节，0 = 未知）
    segments: int = 0              # 分片数
    #: 是否 AES-128 加密
    encrypted: bool = False
    #: 是否是试看片段（接口 is_try=1）
    is_try: int = 0

    @property
    def key(self) -> str:
        """去重/续传用的稳定标识。"""
        return f"{self.appid}/{self.resource_id}"


# ────────────────── 链接解析 ──────────────────
#: 小鹅通的几个域名（h5 / 老版 / 主站）
_HOST_RE = re.compile(
    r"https?://(?P<host>[A-Za-z0-9\-]+)\.(?:h5\.)?"
    r"(?:xiaoeknow\.com|xiaoe-tech\.com|xeknow\.com)[^\s'\"<>，。；、）】]*",
    re.I)
_URL_RE = re.compile(r"https?://[^\s'\"<>，。；、）】]+", re.I)
_RID_RE = re.compile(r"(v_[0-9a-zA-Z]{8,})")
_PID_RE = re.compile(r"[?&](?:product_id|pro_id|productId)=(p_[0-9a-zA-Z_]+)")
_APPID_RE = re.compile(r"^(app[0-9a-zA-Z]{6,})$")


def extract_links(text: str) -> list:
    """从一整段粘贴文本里抠出小鹅通课程链接（保持原顺序、去重）。

    支持分享文案（"课程名 https://xxx.h5.xiaoeknow.com/p/course/video/v_xxx"）、
    一行一个链接、多行混排。
    """
    out, seen = [], set()

    def _add(x: str):
        k = (x or "").strip().rstrip(".,;，。；")
        if k and k not in seen:
            seen.add(k)
            out.append(k)

    for m in _HOST_RE.finditer(text or ""):
        _add(m.group(0))
    # 兜底：域名大小写/新后缀没覆盖到时，按通用 URL 再捞一遍
    for m in _URL_RE.finditer(text or ""):
        u = m.group(0)
        low = u.lower()
        if ("xiaoeknow.com" in low or "xiaoe-tech.com" in low
                or "xeknow.com" in low):
            _add(u)
    return out


def parse_target(s: str) -> dict:
    """把一个链接解析成 {'kind', 'appid', 'resource_id', 'product_id'}。

    kind:
      · video   —— 拿到了 appid + resource_id，可以直接调接口
      · unknown —— 是链接但缺关键信息（比如课程目录页、专栏页）
    """
    s = (s or "").strip()
    appid = ""
    m = _HOST_RE.search(s)
    if m:
        host = m.group("host")
        if _APPID_RE.match(host):
            appid = host
    if not appid:
        # 有些分享链接把 appid 放在路径里：/app/xxx/
        m2 = re.search(r"/app/(app[0-9a-zA-Z]{6,})", s)
        if m2:
            appid = m2.group(1)

    rid = ""
    m3 = re.search(r"/video/(v_[0-9a-zA-Z]+)", s)
    if m3:
        rid = m3.group(1)
    if not rid:
        m3 = _RID_RE.search(s)
        if m3:
            rid = m3.group(1)

    pid = ""
    m4 = _PID_RE.search(s)
    if m4:
        pid = m4.group(1)

    if appid and rid:
        return {"kind": "video", "appid": appid, "resource_id": rid,
                "product_id": pid, "url": s}
    return {"kind": "unknown", "appid": appid, "resource_id": rid,
            "product_id": pid, "url": s}


# ────────────────── 解混淆 ──────────────────
def deobf(s: str) -> str:
    """解 `video_urls`：4 个符号替换 + Base64，尾巴带噪声。

    ★ 必须**在最后一个 `=` 处截断**（实测串尾是 `...XQ==__ba`），
      不能先做 `_`→`/` 之类 URL-safe 替换 —— 那会把噪声变成合法字符，
      于是长度对、字符也合法，但 base64 填充校验直接报错。
    """
    t = str(s or "").strip()
    for k, v in _OBF_MAP.items():
        t = t.replace(k, v)
    cut = t.rfind("=")
    if cut > 0:
        t = t[:cut + 1]
    t += "=" * (-len(t) % 4)
    return base64.b64decode(t).decode("utf-8", "replace")


# ────────────────── 会话 ──────────────────
def make_session(cookie: str = "") -> requests.Session:
    """建会话。`cookie` 是网页版登录后的 Cookie 整串（付费课需要）。

    ★ 匿名也能下**公开课/免费课**，所以 cookie 是可选参数；
      付费课没带 cookie 时接口会返回非 0 code，由 fetch_video 归成可读错误。
    """
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    if cookie:
        # 允许用户直接粘贴整条 Cookie 头（"a=1; b=2"）
        s.headers["Cookie"] = cookie.strip()
    return s


def warmup(sess: requests.Session, appid: str, page_url: str = "") -> bool:
    """先访问课程页拿 `anony_token`。

    不带它直接打接口会返回**空 body**（不是报错，是 200 + 0 字节），
    很容易被误判成"接口挂了"。所以每次新建会话都先走这一步。
    """
    url = page_url or f"https://{appid}.h5.xiaoeknow.com/"
    try:
        r = sess.get(url, timeout=API_TIMEOUT, allow_redirects=True)
        logger.info(f"小鹅通会话预热 {appid}：HTTP {r.status_code}，"
                    f"cookie={[c.name for c in sess.cookies]}")
        return True
    except Exception as e:                     # noqa: BLE001
        logger.warn(f"小鹅通会话预热失败 {appid}：{type(e).__name__}: {e}")
        return False


# ────────────────── 元信息 ──────────────────
def _pick_best(items: list) -> dict:
    """从 video_urls 解出的清晰度列表里挑最高档。

    实测字段：definition_name（高清/超清/蓝光）、definition_p（720P/1080P）、
    url、is_support。按 definition_p 里的数字排，取不到就取第一条。
    """
    if not items:
        return {}
    def _score(it: dict) -> int:
        txt = f"{it.get('definition_p') or ''} {it.get('definition_name') or ''}"
        m = re.search(r"(\d{3,4})\s*[pP]", txt)
        if m:
            return int(m.group(1))
        # 没有数字就按中文档位粗排
        name = str(it.get("definition_name") or "")
        for rank, kw in enumerate(("蓝光", "超清", "高清", "标清", "流畅")):
            if kw in name:
                return 1080 - rank * 120
        return 0
    return sorted(items, key=_score, reverse=True)[0]


def fetch_video(target: dict, sess: Optional[requests.Session] = None,
                cookie: str = "") -> dict:
    """取一节课的元信息 + m3u8 地址。返回 {'ok', 'video'|'error'}。

    ★ m3u8 里的 sign 有时效（实测约几小时），所以**每次下载都现取**，
      不缓存到清单里 —— 存了会让人误以为"地址在手随时能下"。
    """
    if target.get("kind") != "video":
        return {"ok": False,
                "error": "这不是单节视频链接（可能是课程目录/专栏页，"
                         "请点进具体一节再复制地址）"}
    appid = target["appid"]
    rid = target["resource_id"]
    pid = target.get("product_id") or ""

    own = sess is None
    s = sess or make_session(cookie)
    page = (f"https://{appid}.h5.xiaoeknow.com/p/course/video/{rid}"
            + (f"?product_id={pid}" if pid else ""))
    if own:
        warmup(s, appid, page)

    form = {
        "bizData[resource_id]": rid,
        "bizData[opr_sys]": "Windows",
    }
    if pid:
        form["bizData[product_id]"] = pid

    try:
        r = s.post(API_DETAIL.format(appid=appid), data=form,
                   headers={"Referer": page,
                            "Origin": f"https://{appid}.h5.xiaoeknow.com",
                            "Content-Type": "application/x-www-form-urlencoded"},
                   timeout=API_TIMEOUT)
        txt = (r.text or "").strip()
        if not txt:
            logger.warn(f"小鹅通详情接口返回空 body（{appid}/{rid}）"
                        f"HTTP {r.status_code} —— 通常是缺 anony_token 会话")
            return {"ok": False, "error": "接口返回空响应，请重试一次"}
        j = r.json()
    except Exception as e:                     # noqa: BLE001
        logger.warn(f"小鹅通详情接口异常 {appid}/{rid}："
                    f"{type(e).__name__}: {e}")
        return {"ok": False, "error": f"网络错误：{type(e).__name__}"}

    code = j.get("code")
    if code != 0:
        msg = j.get("msg") or j.get("message") or f"code={code}"
        # 常见：未购买/未登录 → 要求授权；课程下架 → 不存在
        return {"ok": False, "error": f"接口拒绝（{code}）：{msg}。"
                                      f"若是付费课，请在下方填登录 Cookie"}

    d = j.get("data") or {}
    vi = d.get("video_info") or {}

    try:
        items = json.loads(deobf(d.get("video_urls") or ""))
    except Exception as e:                     # noqa: BLE001
        logger.warn(f"小鹅通 video_urls 解码失败 {appid}/{rid}："
                    f"{type(e).__name__}: {e}")
        return {"ok": False, "error": "播放地址解不出来（接口格式可能变了）"}

    if not isinstance(items, list) or not items:
        return {"ok": False, "error": "接口没给播放地址（可能未购买或不支持"
                                      "网页播放，只能在 App 内看）"}

    best = _pick_best(items)
    m3u8 = best.get("url") or ""
    if not m3u8:
        return {"ok": False, "error": "播放地址为空"}

    file_name = str(vi.get("file_name") or "").strip()
    if file_name.lower().endswith(".mp4"):
        file_name = file_name[:-4]
    title = file_name or rid

    try:
        length = int(vi.get("video_length") or 0)
    except (TypeError, ValueError) as e:
        logger.warn(f"小鹅通 video_length 非数字 {vi.get('video_length')}: {e}")
        length = 0

    v = XiaeoVideo(
        appid=appid,
        resource_id=rid,
        product_id=pid,
        title=title,
        duration=length,
        m3u8=m3u8,
        quality=str(best.get("definition_p") or best.get("definition_name") or ""),
        is_try=int(d.get("is_try") or 0),
    )
    logger.info(f"小鹅通解析成功 {rid}：{title}（{v.quality or '未知清晰度'}，"
                f"{length}s，共 {len(items)} 档）")
    return {"ok": True, "video": v}


# ────────────────── m3u8 ──────────────────
def parse_m3u8(text: str, base_url: str) -> dict:
    """解析 HLS 播放列表。返回 {'segments', 'key', 'iv', 'encrypted'}。

    只处理小鹅通用到的子集：AES-128 + 绝对/相对分片路径。
    分片可能是相对路径（`v.f421220_0.ts`），要按 m3u8 所在目录拼。
    """
    segs, key, iv = [], "", b""
    ordered = []            # [(url, 时长)]，时长用于估总长度
    base = base_url.rsplit("/", 1)[0] + "/"

    for raw in (text or "").splitlines():
        ln = raw.strip()
        if not ln:
            continue
        if ln.startswith("#EXT-X-KEY"):
            m = re.search(r'URI="([^"]+)"', ln)
            if m:
                key = m.group(1)
                if not key.startswith("http"):
                    key = urllib.parse.urljoin(base, key)
            m2 = re.search(r"IV=0x([0-9A-Fa-f]+)", ln)
            if m2:
                h = m2.group(1)
                h = h.rjust(32, "0")[:32]
                try:
                    iv = bytes.fromhex(h)
                except ValueError as e:
                    logger.warn(f"小鹅通 m3u8 IV 解析失败 {m2.group(1)}: {e}")
        elif ln.startswith("#EXTINF:"):
            try:
                dur = float(ln.split(":", 1)[1].split(",")[0])
            except (ValueError, IndexError):
                dur = 0.0
            ordered.append([dur, ""])
        elif not ln.startswith("#"):
            u = ln if ln.startswith("http") else urllib.parse.urljoin(base, ln)
            segs.append(u)
            if ordered:
                ordered[-1][1] = u

    return {
        "segments": segs,
        "key": key,
        "iv": iv,
        "encrypted": bool(key),
        "durations": [d for d, _ in ordered],
    }


def fetch_m3u8(video: XiaeoVideo, sess: requests.Session) -> dict:
    """拉播放列表。返回 {'ok', 'segments', 'key', 'iv', 'encrypted'|'error'}。"""
    referer = f"https://{video.appid}.h5.xiaoeknow.com/"
    try:
        r = sess.get(video.m3u8, headers={"Referer": referer},
                     timeout=API_TIMEOUT)
    except Exception as e:                     # noqa: BLE001
        logger.warn(f"小鹅通 m3u8 拉取失败 {video.resource_id}："
                    f"{type(e).__name__}: {e}")
        return {"ok": False, "error": f"播放列表请求失败：{type(e).__name__}"}

    if r.status_code != 200:
        return {"ok": False,
                "error": f"播放列表 HTTP {r.status_code}"
                         f"（地址可能已过期，重新解析一次）"}
    text = r.text or ""
    if "#EXTM3U" not in text:
        # 真 DRM（mpd）或错误页都会走到这里
        return {"ok": False,
                "error": "返回的不是 HLS 播放列表（这课可能启用了"
                         " Widevine DRM，只能在线看）"}

    info = parse_m3u8(text, video.m3u8)
    if not info["segments"]:
        return {"ok": False, "error": "播放列表里没有分片"}
    return {"ok": True, **info}


def fetch_key(key_url: str, sess: requests.Session, referer: str) -> bytes:
    """取 AES-128 密钥（16 字节）。失败返回 b""。"""
    try:
        r = sess.get(key_url, headers={"Referer": referer}, timeout=API_TIMEOUT)
        if r.status_code != 200:
            logger.warn(f"小鹅通 key 请求 HTTP {r.status_code}")
            return b""
        k = r.content or b""
        if len(k) != 16:
            logger.warn(f"小鹅通 key 长度异常：{len(k)} 字节（期望 16）")
        return k
    except Exception as e:                     # noqa: BLE001
        logger.warn(f"小鹅通 key 请求异常：{type(e).__name__}: {e}")
        return b""


# ────────────────── 文件名 ──────────────────
def safe_name(s: str, limit: int = 80) -> str:
    t = _BAD_CHARS.sub("_", str(s or "")).strip()
    t = _DOTS.sub("", t)
    t = re.sub(r"_{2,}", "_", t)
    if len(t) > limit:
        t = t[:limit].rstrip("_ ")
    return t or "xiaoe"


def target_filename(video: XiaeoVideo) -> str:
    """落盘文件名：`标题_清晰度.mp4`。

    ★ 必须稳定（不含时间戳/序号）：断点续传靠它找 `.part` 目录，
      踩过的坑跟 B 站 P50 一样 —— 名字一变就永远续不上。
    """
    base = safe_name(video.title, 70)
    if video.quality:
        return f"{base}_{safe_name(video.quality, 12)}.mp4"
    return f"{base}.mp4"


# ────────────────── ffmpeg ──────────────────
def find_ffmpeg() -> str:
    """找 ffmpeg：先程序目录（将来内置），再系统 PATH。找不到返回 ""。"""
    cands = []
    try:
        import sys
        base = Path(sys.executable).parent
        cands += [base / "ffmpeg.exe", base / "_internal" / "ffmpeg.exe",
                  base.parent / "ffmpeg.exe"]
    except Exception as e:                     # noqa: BLE001
        logger.warn(f"小鹅通定位程序目录失败：{e}")
    exe = shutil.which("ffmpeg")
    if exe:
        cands.insert(0, Path(exe))
    for c in cands:
        try:
            if c and Path(c).exists():
                return str(c)
        except OSError as e:
            logger.warn(f"小鹅通检查 ffmpeg 路径失败 {c}: {e}")
    return ""


def remux_to_mp4(ts_path: Path, mp4_path: Path,
                 ffmpeg: str = "") -> tuple:
    """TS → MP4（只换容器，`-c copy` 不重新编码，几秒钟）。返回 (ok, err)。

    ★ 没有 ffmpeg 时**不报错**：调用方保留 .ts 并提示。
      .ts 本身是可播放的标准视频，只是播放器兼容性差一些。
    """
    exe = ffmpeg or find_ffmpeg()
    if not exe:
        return False, "没有 ffmpeg"
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(ts_path), "-c", "copy", "-movflags", "+faststart",
           str(mp4_path)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except Exception as e:                     # noqa: BLE001
        logger.warn(f"小鹅通 remux 调用失败：{type(e).__name__}: {e}")
        return False, f"{type(e).__name__}: {e}"
    if p.returncode != 0 or not mp4_path.exists():
        err = (p.stderr or "").strip()[:300]
        logger.warn(f"小鹅通 remux 失败 rc={p.returncode}: {err}")
        return False, err or f"ffmpeg 返回 {p.returncode}"
    return True, ""


# ────────────────── 下载 ──────────────────
def _decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-128-CBC 解密一片。失败返回原文（宁可留错也不丢片）。"""
    if not key or not data:
        return data
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except Exception as e:                     # noqa: BLE001
        logger.warn(f"小鹅通缺少 cryptography，无法解密：{e}")
        return data
    use_iv = iv or b"\x00" * 16
    try:
        c = Cipher(algorithms.AES(key), modes.CBC(use_iv))
        dec = c.decryptor()
        out = dec.update(data)
        out += dec.finalize()
        return out
    except Exception as e:                     # noqa: BLE001
        logger.warn(f"小鹅通分片解密失败（长度 {len(data)}）："
                    f"{type(e).__name__}: {e}")
        return data


def _seg_path(tmp_dir: Path, idx: int) -> Path:
    return tmp_dir / f"{idx:05d}.ts"


def download_video(
    video: XiaeoVideo,
    dest_dir,
    *,
    sess: Optional[requests.Session] = None,
    cookie: str = "",
    workers: int = DEFAULT_WORKERS,
    on_progress: Optional[Callable[[int, int, int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    on_log: Optional[Callable[[str, str], None]] = None,
    ffmpeg: str = "",
) -> dict:
    """下载一节课。返回 {'ok', 'path', 'size', 'segments', 'error', 'ts_only'}。

    流程：拉播放列表 → 并发下分片（解密后落盘）→ 按序拼接 → remux 成 mp4。

    断点续传：分片缓存在 `<目标名>.parts/` 里，已下好的分片跳过。
      中断后重跑同一条链接，只补缺的片；全部下完才拼接，所以不会出现
      "半个 mp4"。
    """
    log = on_log or (lambda lv, m: None)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / target_filename(video)
    tmp_dir = dest_dir / (dest.stem + ".parts")

    if dest.exists() and dest.stat().st_size > 0:
        return {"ok": True, "path": str(dest), "size": dest.stat().st_size,
                "segments": 0, "skipped": True}
    # ★ 没装 ffmpeg 的客户，上一轮留下的是 .ts（见文末 ts_only 分支）。
    #   只认 .mp4 的话，他再点一次「下载」会把整节课**重下一遍**
    #   （实测 122MB × 每点一次）。所以 .ts 也算已完成。
    ts_done = dest_dir / (dest.stem + ".ts")
    if ts_done.exists() and ts_done.stat().st_size > 0:
        return {"ok": True, "path": str(ts_done),
                "size": ts_done.stat().st_size, "segments": 0,
                "skipped": True, "ts_only": True}

    s = sess or make_session(cookie)
    referer = f"https://{video.appid}.h5.xiaoeknow.com/"
    if sess is None:
        # 付费课的 key 接口会看登录态，所以下载会话也要预热/带 cookie
        warmup(s, video.appid, referer)

    info = fetch_m3u8(video, s)
    if not info.get("ok"):
        # ★ 解析完很久才点下载 → sign 过期（返回 403/404/非 HLS）。
        #   这里重取一次地址再试，不直接判死 —— 客户最烦「刚才还能解析，
        #   点下载就报错」。
        log("warn", f"  播放地址不可用（{info.get('error')}），重新取一次…")
        again = fetch_video({"kind": "video", "appid": video.appid,
                             "resource_id": video.resource_id,
                             "product_id": video.product_id},
                            sess=s, cookie=cookie)
        if again.get("ok"):
            fresh = again["video"]
            video.m3u8 = fresh.m3u8
            video.quality = video.quality or fresh.quality
            info = fetch_m3u8(video, s)
    if not info.get("ok"):
        return {"ok": False, "error": info.get("error") or "取播放列表失败"}

    segs = info["segments"]
    total = len(segs)
    video.segments = total
    encrypted = bool(info.get("encrypted"))
    key = b""
    if encrypted:
        key = fetch_key(info["key"], s, referer)
        if not key:
            return {"ok": False,
                    "error": "拿不到解密密钥（这课可能限制在 App 内播放）"}
        log("info", f"  已取到 AES-128 密钥（16 字节），{total} 个分片")
    video.encrypted = encrypted

    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warn(f"小鹅通创建分片目录失败 {tmp_dir}: {e}")
        return {"ok": False, "error": f"建目录失败：{e}"}

    # 已下好的分片（续传）
    # ★ P64-②：只认 .ts 正式文件名。分片一律先写 `.ts.tmp` 再 os.replace，
    #   所以「存在且 >0 字节」就等于「完整」—— 进程被杀不会留下半截片被
    #   当成已完成（P28 同族：别用『文件存不存在/够不够大』判完成）。
    done_set = {i for i in range(total)
                if _seg_path(tmp_dir, i).exists()
                and _seg_path(tmp_dir, i).stat().st_size > 0}
    # 清掉上次被杀留下的半截临时片，免得越攒越多
    try:
        for stale in tmp_dir.glob("*.ts.tmp"):
            stale.unlink()
    except OSError as e:
        logger.warn(f"小鹅通清理半截分片失败：{e}")
    if done_set:
        log("info", f"  断点续传：已有 {len(done_set)}/{total} 片，只补缺的")

    bytes_box = {"n": sum(_seg_path(tmp_dir, i).stat().st_size
                          for i in sorted(done_set))}
    done_box = {"n": len(done_set)}
    last_put = [0.0]

    def _report(force: bool = False):
        now = time.time()
        if not force and now - last_put[0] < 0.2:
            return
        last_put[0] = now
        if on_progress:
            try:
                on_progress(done_box["n"], total, bytes_box["n"])
            except Exception as e:             # noqa: BLE001
                logger.warn(f"小鹅通进度回调异常：{e}")

    def _one(idx: int) -> tuple:
        """下一个分片。返回 (ok, 字节数, 错误)。"""
        out = _seg_path(tmp_dir, idx)
        if idx in done_set:
            return True, out.stat().st_size, ""
        url = segs[idx]
        last = ""
        for attempt in range(SEG_RETRY + 1):
            if should_stop and should_stop():
                return False, 0, "stopped"
            try:
                r = s.get(url, headers={"Referer": referer},
                          timeout=DL_TIMEOUT)
                if r.status_code != 200:
                    last = f"HTTP {r.status_code}"
                    time.sleep(0.4 * (attempt + 1))
                    continue
                raw = r.content or b""
                if not raw:
                    last = "空分片"
                    continue
                data = _decrypt(raw, key, info.get("iv") or b"") if encrypted else raw
                # ★ 原子落盘：先写 .tmp 再改名。直接 write_bytes 到 .ts，
                #   一旦此刻被杀/断电，就会留下「有内容但不完整」的分片，
                #   下次续传把它当已完成 → 拼出来的片子在那个位置花屏。
                tmp_seg = out.with_name(out.name + ".tmp")
                tmp_seg.write_bytes(data)
                os.replace(tmp_seg, out)
                return True, len(data), ""
            except Exception as e:             # noqa: BLE001
                last = f"{type(e).__name__}"
                logger.warn(f"小鹅通分片 {idx} 第 {attempt + 1} 次失败：{last}")
                time.sleep(0.4 * (attempt + 1))
        return False, 0, last or "失败"

    from concurrent.futures import ThreadPoolExecutor, as_completed

    failed = []
    try:
        with ThreadPoolExecutor(max_workers=max(1, int(workers))) as ex:
            futs = {ex.submit(_one, i): i for i in range(total) if i not in done_set}
            for fut in as_completed(futs):
                if should_stop and should_stop():
                    for f in futs:
                        f.cancel()
                    break
                ok, n, err = fut.result()
                if ok:
                    done_box["n"] += 1
                    bytes_box["n"] += n
                    _report()
                else:
                    if err == "stopped":
                        break
                    failed.append(futs[fut])
                    log("warn", f"  第 {futs[fut] + 1} 片失败：{err}")
    except Exception as e:                     # noqa: BLE001
        logger.warn(f"小鹅通并发下载异常：{type(e).__name__}: {e}")
        return {"ok": False, "error": f"下载异常：{e}"}
    finally:
        _report(force=True)

    if should_stop and should_stop():
        return {"ok": False, "error": "已停止（分片已保留，重下会接着来）",
                "segments": done_box["n"]}

    if failed:
        return {"ok": False,
                "error": f"{len(failed)} 个分片下载失败（已保留 {done_box['n']}/{total}，"
                         f"重试会补下）",
                "segments": done_box["n"]}

    # ── 拼接 ──
    log("info", f"  分片齐了（{total} 片），开始拼接…")
    ts_path = dest_dir / (dest.stem + ".ts")
    ts_tmp = ts_path.with_name(ts_path.name + ".part")
    try:
        with open(ts_tmp, "wb") as out:
            for i in range(total):
                p = _seg_path(tmp_dir, i)
                if not p.exists():
                    # 半截拼接文件不能留：下次有人手点这个 .ts 会当成成片
                    try:
                        ts_tmp.unlink()
                    except OSError as e:
                        logger.warn(f"小鹅通清理半截拼接失败：{e}")
                    return {"ok": False,
                            "error": f"第 {i + 1} 片不见了，请重新解析下载"}
                out.write(p.read_bytes())
        os.replace(ts_tmp, ts_path)
    except Exception as e:                     # noqa: BLE001
        logger.warn(f"小鹅通拼接失败：{type(e).__name__}: {e}")
        return {"ok": False, "error": f"拼接失败：{e}"}

    ts_size = ts_path.stat().st_size
    log("info", f"  拼接完成：{fmt_size(ts_size)}，转封装中…")

    ok, err = remux_to_mp4(ts_path, dest, ffmpeg)
    if ok:
        try:
            ts_path.unlink()
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except OSError as e:
            logger.warn(f"小鹅通清理临时文件失败：{e}")
        size = dest.stat().st_size
        log("ok", f"  完成：{dest.name}（{fmt_size(size)}）")
        return {"ok": True, "path": str(dest), "size": size,
                "segments": total, "ts_only": False}

    # 没有 ffmpeg（或转封装失败）→ 保留 .ts，不让用户白下
    final_ts = dest_dir / (dest.stem + ".ts")
    log("warn", f"  转 mp4 失败（{err}）→ 已保留 TS 文件，可直接播放")
    size = final_ts.stat().st_size if final_ts.exists() else ts_size
    return {"ok": True, "path": str(final_ts), "size": size,
            "segments": total, "ts_only": True,
            "note": "没有 ffmpeg，已存为 .ts（PotPlayer/VLC/剪映可直接打开）"}


# ────────────────── 小工具 ──────────────────
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
