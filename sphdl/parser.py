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


# ═══════════════════════════════════════════════════════════════════
# ★ v2.5.0 视频统计数据提取（点赞 / 转发 / 推荐 / 收藏 / 评论数）
#
#   客户需求：「能抓取视频的点赞数、转发数、推荐数，并支持按点赞数排序」
#
#   ★ 为什么必须容错：微信不同版本 / 不同接口里，这些字段的**名字不一样**，
#     我们还不能确定当前客户端用哪个（需要真实 payload 才能定死）。
#     所以策略是「广撒网 + 结构兜底」：
#       1. 先按已知别名列表在 objectDesc 顶层找
#       2. 再递归到 "stat"/"stats"/"counts"/"extInfo" 这类子对象里找
#       3. 都找不到就是 0（界面显示「—」），**绝不编造**
#   找到哪些键会记进 _last_stat_keys，方便诊断真实字段名。
# ═══════════════════════════════════════════════════════════════════

_STAT_ALIASES = {
    "like_count": ("likeCount", "like_count", "likeNum", "like_num",
                   "diggCount", "digg_count", "praiseCount", "praise_count",
                   "favLikeCount", "thumbUpCount", "thumb_up_count"),
    "forward_count": ("forwardCount", "forward_count", "shareCount",
                      "share_count", "transmitCount", "transmit_count",
                      "forwardNum", "forward_num"),
    "recommend_count": ("recommendCount", "recommend_count", "recCount",
                        "rec_count", "recommendNum", "recommend_num"),
    "fav_count": ("favCount", "fav_count", "collectCount", "collect_count",
                  "favoriteCount", "favorite_count", "favNum", "fav_num"),
    "comment_count": ("commentCount", "comment_count", "commentNum",
                      "comment_num", "commentsCount"),
}

# 统计字段常常挂在这些子对象里
_STAT_CONTAINERS = ("stat", "stats", "statistics", "counts", "counter",
                    "extInfo", "ext_info", "baseInfo", "base_info")

_last_stat_keys: dict = {}       # 诊断用：最近一次实际命中的键名


def _as_int(v):
    try:
        if isinstance(v, bool):
            return 0
        return max(0, int(v))
    except Exception:
        return 0


def extract_stats(obj: Any) -> dict:
    """从 objectDesc 里容错提取统计数据，返回 5 个 int 字段。"""
    global _last_stat_keys
    out = {k: 0 for k in _STAT_ALIASES}
    found = {}

    def _scan(d):
        if not isinstance(d, dict):
            return
        for field, aliases in _STAT_ALIASES.items():
            if out[field]:
                continue
            for a in aliases:
                if a in d:
                    n = _as_int(d.get(a))
                    if n > 0:
                        out[field] = n
                        found[field] = a
                        break

    _scan(obj)
    # 子容器里再找一遍
    if isinstance(obj, dict):
        for ck in _STAT_CONTAINERS:
            sub = obj.get(ck)
            if isinstance(sub, dict):
                _scan(sub)
            elif isinstance(sub, str) and sub.strip()[:1] == "{":
                try:
                    _scan(json.loads(sub))
                except Exception:
                    pass

    _last_stat_keys = found
    return out


def last_stat_keys() -> dict:
    """最近一次提取命中的键名（诊断真实字段名用）。"""
    return dict(_last_stat_keys)


def _extract_stats_kwargs(obj) -> dict:
    """extract_stats 的薄包装，供 VideoData(...) 展开用。"""
    try:
        return extract_stats(obj)
    except Exception:
        return {}


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
        # ★ v2.2.16：nonce 用于给评论找对宿主视频
        nonce_id = str(_pick(obj if isinstance(obj, dict) else {},
                             "objectNonceId", "object_nonce_id",
                             "nonceId", "nonce_id", "exportId", "export_id")
                       or "")
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
            nonce_id=nonce_id,
            **_extract_stats_kwargs(obj),
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


# ★ v2.2.14：嵌套回复（粉丝互相对答）
#   视频号评论区里，一条评论下面常有「A 回复 B：...」的串，
#   这些在数据里是**嵌套列表**（replyList / replies / subComments ...）。
#   旧实现只取「条数最多的那一层」，嵌套回复全被丢弃 →
#   客户看到的 24 条 vs 视频号 69 条，差的就是这些。
_REPLY_LIST_KEYS = (
    "replyList", "reply_list", "replies", "reply",
    "subComments", "sub_comments", "subCommentList", "sub_comment_list",
    "children", "childComments", "child_comments",
    "replyComments", "reply_comments", "replyCommentList",
)


def _parse_comment_node(d, depth: int = 0):
    """把一个评论 dict 解析成 {nick, content, likes, time, cid, replies:[...]}。

    会递归解析它内部的嵌套回复列表（最多 4 层，防病态数据）。
    返回 None = 不像评论。
    """
    base = _norm_comment(d)
    if base is None:
        return None
    base["replies"] = []
    if depth >= 4:
        return base
    for k, v in d.items():
        if k in _NICK_KEYS or k in _CONTENT_KEYS:
            continue
        lst = None
        if isinstance(v, list):
            lst = v
        elif isinstance(v, str) and v.strip()[:1] == "[":
            dv = _try_json(v)
            if isinstance(dv, list):
                lst = dv
        if not lst:
            continue
        # 只有「明确是回复字段」或「列表项确实像评论」才收
        key_is_reply = k in _REPLY_LIST_KEYS
        kids = []
        for it in lst:
            if not isinstance(it, dict):
                continue
            child = _parse_comment_node(it, depth + 1)
            if child is not None:
                kids.append(child)
        if kids and (key_is_reply or len(kids) == len(lst)):
            base["replies"].extend(kids)
    return base


def _walk_comment_roots(obj, depth: int = 0, out=None):
    """收集所有「候选根评论列表」：(depth, [comment_node...], total)。

    ★ 只把**最浅一层**当根：更深的列表属于某条评论的回复，
      已被 _parse_comment_node 挂进 replies，再当根会重复计数。
    """
    if out is None:
        out = []
    if depth > 8 or obj is None:
        return out
    if isinstance(obj, dict):
        total = _find_total(obj)
        for k, v in obj.items():
            lst = None
            if isinstance(v, list):
                lst = v
            elif isinstance(v, str) and v.strip()[:1] == "[":
                dv = _try_json(v)
                if isinstance(dv, list):
                    lst = dv
            if lst:
                nodes = []
                for it in lst:
                    if isinstance(it, dict):
                        n = _parse_comment_node(it)
                        if n is not None:
                            nodes.append(n)
                if nodes:
                    out.append((depth, nodes, total))
                    continue          # 这一支已经当根了，别再把子列表当根
            if isinstance(v, (dict, list)):
                _walk_comment_roots(v, depth + 1, out)
            elif isinstance(v, str) and v.strip()[:1] in ("{", "["):
                dv = _try_json(v)
                if isinstance(dv, (dict, list)):
                    _walk_comment_roots(dv, depth + 1, out)
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, (dict, list)):
                _walk_comment_roots(v, depth + 1, out)
    return out


def count_tree(nodes) -> int:
    """统计评论树的总条数（含所有嵌套回复）。"""
    n = 0
    for c in nodes or []:
        n += 1 + count_tree(c.get("replies") if isinstance(c, dict) else None)
    return n


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
    """从任意 payload 里抽出评论树（含嵌套回复）。

    返回 (comments:list, total:int|None)；没找到返回 ([], None)。

    ★ v2.2.14 改进：不再「选条数最多的那一层」——
      那样会把每条评论下面的**粉丝互相对答**当独立列表丢掉
      （客户实测 24 条 vs 视频号 69 条，差的就是这部分）。
      现在按「最浅一层为根 + 嵌套列表挂到父评论 replies」解析，
      取根列表节点数最多的那份。
    """
    best = None
    try:
        cands = _walk_comment_roots(payload)
    except Exception:
        cands = []
    for depth, nodes, total in cands:
        if not nodes:
            continue
        key = (len(nodes), count_tree(nodes))
        if best is None or key > best[0]:
            best = (key, nodes, total)
    if best is None:
        return [], None
    return best[1], best[2]
