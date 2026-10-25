"""
object_desc 解析：从任意 JSON（微信 API 响应 / TikHub 响应 / JS 回调体）
中提取视频信息 (url + decode_key + 描述 + 尺寸)。

兼容两种字段命名：
  - snake_case: decode_key / file_size / cover_url   （微信 API、TikHub）
  - camelCase:  decodeKey  / fileSize / coverUrl     （JS 回调 objectDesc）
"""
import json
import re
from typing import Any, Optional

from .entities import VideoData


def _mb(size):
    try:
        return f"{int(size) / 1048576:.1f}MB"
    except (TypeError, ValueError):
        return ""


def _dims(w, h):
    try:
        if float(w) > 0 and float(h) > 0:
            return f"{int(float(w))}x{int(float(h))}"
    except (TypeError, ValueError):
        pass
    return ""


def _kbps(bitrate):
    try:
        if int(bitrate) > 0:
            return f"{int(bitrate) // 1000}kbps"
    except (TypeError, ValueError):
        pass
    return ""


def _make_rendition(m: dict, *, url_keys, size_keys, w_keys, h_keys, bit_keys,
                    label, token_keys=()):
    """从 media 条目的 标准(url) 或 原始(full_url) 字段构造清晰度候选。
    token 只应拼到同一条 media 自己的标准 url（url 以 ? 结尾、token 以 & 开头）；
    原始 full_url 自带完整鉴权参数，不拼。
    兼容 snake_case（微信 API）与 camelCase（JS 回调/bridge objectDesc）。"""
    url = str(_pick(m, *url_keys) or "").strip()
    if not url:
        return None
    token = _pick(m, *token_keys) or ""
    if token and "token=" not in url and not url.endswith(str(token)):
        url = url + str(token)
    size = int(_pick(m, *size_keys) or 0)
    detail = " / ".join(x for x in
                        (_dims(_pick(m, *w_keys), _pick(m, *h_keys)),
                         _kbps(_pick(m, *bit_keys)), _mb(size)) if x)
    return {"url": url, "size": size, "label": label + (f"（{detail}）" if detail else ""),
            "width": _pick(m, *w_keys) or 0, "height": _pick(m, *h_keys) or 0,
            "bitrate": _pick(m, *bit_keys) or 0}


def _quality_note(media_list) -> str:
    """从 spec 阶梯生成可读说明（仅展示用，不含 URL/token）。
    例：「微信提供 6 档规格，最高 720x1280」。"""
    try:
        for m in media_list:
            if not isinstance(m, dict):
                continue
            specs = m.get("spec")
            if not isinstance(specs, list) or not specs:
                continue
            best = None
            for s in specs:
                if isinstance(s, dict) and s.get("level_order") == 100:
                    best = s
                    break
            top = ""
            if best is not None and best.get("width") and best.get("height"):
                br = best.get("video_bitrate") or best.get("bit_rate")
                top = (f"，最高 {int(best['width'])}x{int(best['height'])}"
                       + (f"@{int(br)}kbps" if br else ""))
            return f"微信提供 {len(specs)} 档规格{top}"
    except Exception:
        pass
    return ""


def _pick(mapping: dict, *names):
    """按多个候选键取值（第一个非空生效）"""
    for name in names:
        v = mapping.get(name)
        if v:
            return v
    return None


def extract_from_object_desc(obj: Any, description_hint: str = "") -> Optional[VideoData]:
    """
    从 object_desc（dict 含 media 列表）或 media 列表本身中提取视频信息。
    """
    try:
        if isinstance(obj, list):
            media_list = obj
        elif isinstance(obj, dict):
            media_list = _pick(obj, "media", "media_list", "mediaList") or []
        else:
            return None
        if not isinstance(media_list, list) or not media_list:
            return None

        # 取第一个带 url 的 media
        first = None
        for m in media_list:
            if isinstance(m, dict) and (_pick(m, "url", "urlToken", "url_token")):
                first = m
                break
        if first is None:
            return None

        # 收集全部清晰度：每条 media 的标准 url + 原始 full_url
        renditions = []
        for m in media_list:
            if not isinstance(m, dict):
                continue
            r = _make_rendition(m, url_keys=("url",),
                                size_keys=("file_size", "fileSize"),
                                w_keys=("width",), h_keys=("height",),
                                bit_keys=("bitrate",), label="标准",
                                token_keys=("urlToken", "url_token"))
            if r:
                renditions.append(r)
            r_full = _make_rendition(m,
                                     url_keys=("full_url", "fullUrl"),
                                     size_keys=("full_file_size", "fullFileSize"),
                                     w_keys=("full_width", "fullWidth"),
                                     h_keys=("full_height", "fullHeight"),
                                     bit_keys=("full_bitrate", "fullBitrate"),
                                     label="原始")
            if r_full:
                renditions.append(r_full)

        url = str(_pick(first, "url") or "")
        token = _pick(first, "urlToken", "url_token") or ""
        # url 可能已自带完整 token（微信签名的完整 URL）——此时不能再拼
        # url_token，否则参数重复（历史 bug：样例 url 754 字符、拼接后 1271）。
        if token and "token=" not in url and not url.endswith(str(token)):
            url = url + str(token)  # token 需要拼到 url 末尾

        decode_key = _pick(first, "decode_key", "decodeKey") or ""
        if decode_key and not isinstance(decode_key, str):
            decode_key = str(decode_key)

        size = int(_pick(first, "file_size", "fileSize") or 0)
        media_type = _pick(first, "mediaType", "media_type") or 0
        is_image = int(media_type) == 9  # 9 = 图片

        description = ""
        if isinstance(obj, dict):
            description = obj.get("description") or ""
        description = description or description_hint

        # 稳定视频 ID：优先 media 的 md5sum（内容哈希，跨 token 恒定），
        # 没有则退回 URL 里的 encfilekey（同一文件恒定，token 拼在末尾）
        video_id = str(_pick(first, "md5sum", "full_md5sum") or "")
        if not video_id:
            m = re.search(r"encfilekey=([A-Za-z0-9_\-]+)", url)
            if m:
                video_id = m.group(1)

        return VideoData(
            url=url,
            decode_key=decode_key,
            size=size,
            description=description,
            is_image=is_image,
            cover_url=str(_pick(first, "coverUrl", "cover_url") or ""),
            video_id=video_id,
            renditions=renditions,
            quality_note=_quality_note(media_list),
            raw=obj,
        )
    except Exception:
        return None


def find_object_desc_in_json(obj: Any, depth: int = 0) -> Optional[Any]:
    """
    在任意 JSON 结构中递归查找 object_desc / objectDesc 节点。
    支持传入完整 API 响应（如 TikHub 的 data 包裹）。
    额外处理：值本身是 JSON 字符串的情况（如 bridge 钩子回传的
    jsapi_resp 字段——字符串里再套一层 object_desc），会尝试二次解码。
    """
    if depth > 12 or obj is None:
        return None
    if isinstance(obj, dict):
        for key in ("object_desc", "objectDesc"):
            v = obj.get(key)
            if isinstance(v, (dict, list)) and v:
                return v
            if isinstance(v, str) and v.strip()[:1] in ("{", "["):
                dv = _try_json(v)
                if isinstance(dv, (dict, list)) and dv:
                    return dv
        for v in obj.values():
            found = find_object_desc_in_json(v, depth + 1)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_object_desc_in_json(v, depth + 1)
            if found is not None:
                return found
    elif isinstance(obj, str):
        # 字符串里嵌套的 JSON（含 object_desc 字样才值得尝试解码）
        s = obj.strip()
        if len(s) > 8 and s[0] in "{[" and ("object_desc" in s or "objectDesc" in s):
            dv = _try_json(s)
            if dv is not None:
                return find_object_desc_in_json(dv, depth + 1)
    return None


def iter_object_descs(obj: Any, depth: int = 0):
    """
    递归找出 JSON 中**所有** object_desc / objectDesc 节点（生成器）。
    与 find_object_desc_in_json 的区别：返回全部而不是第一个——
    视频号 feed/主页列表的 bridge 回传常含多个视频的 objectDesc，
    只取第一个会把其余视频全部漏掉（2026-08-27 真机复现：
    播放同一视频号的不同视频，列表数据第一个 objectDesc 是旧视频，
    导致新视频全部被误判"重复"）。
    同样兼容：值为 JSON 字符串时二次解码；字符串内嵌 object_desc 时递归。
    """
    if depth > 12 or obj is None:
        return
    if isinstance(obj, dict):
        for key in ("object_desc", "objectDesc"):
            v = obj.get(key)
            if isinstance(v, (dict, list)) and v:
                yield v
            elif isinstance(v, str) and v.strip()[:1] in ("{", "["):
                dv = _try_json(v)
                if isinstance(dv, (dict, list)) and dv:
                    yield dv
        for v in obj.values():
            yield from iter_object_descs(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_object_descs(v, depth + 1)
    elif isinstance(obj, str):
        s = obj.strip()
        if len(s) > 8 and s[0] in "{[" and ("object_desc" in s or "objectDesc" in s):
            dv = _try_json(s)
            if dv is not None:
                yield from iter_object_descs(dv, depth + 1)


def _try_json(s: str):
    try:
        return json.loads(s)
    except Exception:
        return None


def load_and_extract(json_text: str) -> Optional[VideoData]:
    """从 JSON 文本中提取视频信息（自动找 object_desc）"""
    obj = json.loads(json_text)
    od = find_object_desc_in_json(obj)
    if od is None:
        # 允许直接传入 object_desc 本体或 media 列表
        od = obj
    return extract_from_object_desc(od)


# ═══════════════════════════════════════════════════════════════════
# ★ v2.2.12 视频号评论提取
#
#   客户需求：「能下载对应视频所对应的视频号评论吗」
#   落地形态：「先做个存进视频清单里，有个『查看评论』的选项展开评论，
#              跟视频号同样的评论区展示效果」
#
# ★ 设计原则：**不依赖固定 cmdName / 固定字段名**
#   视频号接口会改名，写死 cmdName 就等于写死一个会过期的功能。
#   这里按「结构特征」识别评论条目：
#     · 有昵称类字段  nick / nickname / nickName / userName / displayName
#     · 有内容类字段  content / text / msg / commentContent
#     · 可选：点赞数 likeCount / likeNum / likes
#   只要一个 list 里有多条这种 dict，就当成评论列表。
# ═══════════════════════════════════════════════════════════════════

_NICK_KEYS = ("nickname", "nickName", "nick_name", "nick",
              "userName", "user_name", "displayName", "display_name")
_CONTENT_KEYS = ("content", "text", "msg", "commentContent",
                 "comment_content", "comment")
_LIKE_KEYS = ("likeCount", "like_count", "likeNum", "like_num", "likes")
_TIME_KEYS = ("createTime", "create_time", "timestamp", "commentTime",
              "comment_time", "time")
_ID_KEYS = ("commentId", "comment_id", "id")


def _norm_comment(d: dict):
    """把一个疑似评论的 dict 规范化成 {nick, content, likes, time, cid}。

    返回 None = 不像评论（缺昵称或缺内容）。
    """
    if not isinstance(d, dict):
        return None
    nick = _pick(d, *_NICK_KEYS)
    content = _pick(d, *_CONTENT_KEYS)
    if nick is None or content is None:
        return None
    # content 必须是短的纯文本（排除把整个对象当 content 的畸形数据）
    if not isinstance(content, (str, int, float)):
        return None
    content = str(content).strip()
    if not content or len(content) > 5000:
        return None
    nick = str(nick).strip()
    if not nick or len(nick) > 200:
        return None
    likes = _pick(d, *_LIKE_KEYS)
    try:
        likes = int(likes) if likes is not None else 0
    except Exception:
        likes = 0
    tm = _pick(d, *_TIME_KEYS)
    cid = _pick(d, *_ID_KEYS)
    return {
        "nick": nick,
        "content": content,
        "likes": likes,
        "time": str(tm) if tm is not None else "",
        "cid": str(cid) if cid is not None else "",
    }


def _find_total(d: dict):
    """在一个 dict 里找评论总数（视频号常见字段名）。找不到返回 None。"""
    for k in ("totalCount", "total_count", "commentCount",
              "comment_count", "total", "totalNum", "total_num"):
        v = d.get(k)
        if isinstance(v, int) and v >= 0:
            return v
        if isinstance(v, str) and v.isdigit():
            return int(v)
    return None


def iter_comment_lists(obj: Any, depth: int = 0):
    """递归找出 JSON 里所有「像评论列表」的节点。

    生成 (comments:list[dict], total:int|None)。

    ★ 判定：一个 list 里 ≥1 条能规范化成评论的 dict。
      只要求 1 条而非多条 —— 单条评论也要能抓到（比如只有一条评论的视频）。
    """
    if depth > 14 or obj is None:
        return
    if isinstance(obj, dict):
        total = _find_total(obj)
        for v in obj.values():
            if isinstance(v, str) and v.strip()[:1] in ("[", "{"):
                dv = _try_json(v)
                if dv is not None:
                    for lst, t in iter_comment_lists(dv, depth + 1):
                        yield (lst, t if t is not None else total)
                    continue
            if isinstance(v, (dict, list)):
                for lst, t in iter_comment_lists(v, depth + 1):
                    yield (lst, t if t is not None else total)
        return

    if isinstance(obj, list):
        norm = []
        for it in obj:
            n = _norm_comment(it)
            if n is not None:
                norm.append(n)
        if norm:
            yield (norm, None)
        # 更深层也可能有嵌套回复
        for it in obj:
            if isinstance(it, (dict, list)):
                yield from iter_comment_lists(it, depth + 1)


def extract_comments(payload: Any):
    """从任意 payload 里抽出「最像评论列表」的那一份。

    返回 (comments:list, total:int|None)；没找到返回 ([], None)。

    ★ 选「条数最多的那份」：一页 bridge 回传里可能同时有
      嵌套回复列表和主评论列表，主列表通常条数更多。
    """
    best = []
    best_total = None
    try:
        for lst, tot in iter_comment_lists(payload):
            if len(lst) > len(best):
                best = lst
                best_total = tot
    except Exception:
        pass
    return best, best_total
