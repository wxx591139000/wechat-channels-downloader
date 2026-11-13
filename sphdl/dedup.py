"""视频身份归一（v3.4.4）——同一个视频的所有标识共用一套去重口径。

★ 背景（2026-11-13 客户实拍）：
    同一个视频在 13:32:32~13:32:33 被抓成 3 行，点赞数分别是 4 / 20 / 7。
    三条记录的 `video_id`（media.md5sum）分别是
    5d704157… / 355465de… / c400a1fa…，`encfilekey`、`decode_key`、URL 全不同。
    md5sum 是「**某一路转码文件**」的内容哈希，不是「**这一条视频（feed 对象）**」的
    身份；同一条视频出现在详情 / 推荐流 / 相关视频等不同 bridge 报文里时，
    首条 media 可能落在不同的清晰度档上 → md5sum 变 → 旧去重键全部落空。

★ 口径：
    一条视频可以有多个别名（feed 对象 ID / 内容哈希 / nonce / URL）。
    **任一别名命中已存在的记录，就视为同一条视频**，不再插新行；
    同时把本次新出现的别名补进该记录的别名集合（下次换清晰度也能认出来）。
    md5sum/URL 仍保留，用于老记录、无对象 ID 的报文和 CLI 场景的兜底。

★ 唯一实现：addon（本次会话内存去重）与 GUI（跨会话 records.json 去重）
    都调用本模块，避免两处各写一套判断逻辑（PITFALLS #P32 的教训）。
"""

# 别名前缀：不同来源的 ID 值可能碰巧相同，必须带命名空间
PREFIX_OID = "oid:"
PREFIX_MD5 = "md5:"
PREFIX_NONCE = "nonce:"
PREFIX_URL = "url:"

STAT_FIELDS = ("like_count", "forward_count", "recommend_count",
               "fav_count", "comment_count")


def _clean(v) -> str:
    try:
        return str(v or "").strip()
    except Exception:
        return ""


def identity_aliases(*, object_id="", video_id="", nonce_id="", url="") -> tuple:
    """按「对象 ID → 内容哈希 → nonce → URL」顺序返回带前缀的别名元组。

    别名只作为**索引键**；`seen` 里保存的仍是旧的 canonical key，
    以免破坏 release_seen / clear_seen / 既有测试的契约。
    """
    out = []
    for prefix, val in ((PREFIX_OID, _clean(object_id)),
                        (PREFIX_MD5, _clean(video_id)),
                        (PREFIX_NONCE, _clean(nonce_id)),
                        (PREFIX_URL, _clean(url))):
        if val:
            out.append(prefix + val)
    return tuple(out)


def aliases_of(obj) -> tuple:
    """从 VideoData / Record 取别名（两个模型字段名一致）。"""
    return identity_aliases(
        object_id=getattr(obj, "object_id", ""),
        video_id=getattr(obj, "video_id", ""),
        nonce_id=getattr(obj, "nonce_id", ""),
        url=getattr(obj, "url", ""),
    )


def legacy_key(obj):
    """旧版 canonical key（兼容 release_seen / 既有断言）：
    video_id 优先，否则 (url, decode_key)。"""
    vid = _clean(getattr(obj, "video_id", ""))
    if vid:
        return vid
    return (_clean(getattr(obj, "url", "")),
            _clean(getattr(obj, "decode_key", "")))


def is_known_in(state, vd) -> bool:
    """给定任意持有 `seen` / `_alias_owner` 的对象，判断这条视频是否已见。

    ★ 不调用 state 的任何方法：回放类回归测试会用 duck-typed 桩对象直接调
      `SphAddon._capture_anchor(stub, ...)`，桩上只有 seen，没有 addon 的方法。
      桩上缺别名索引时自动退回旧口径（只看 canonical key）。无锁，调用方自行
      决定是否加锁（addon 的候选缓存/锚点路径本来就只读 seen，无锁是既有行为）。
    """
    try:
        if legacy_key(vd) in getattr(state, "seen", ()):
            return True
    except Exception:
        return False
    owner_map = getattr(state, "_alias_owner", None)
    if not owner_map:
        return False
    return any(a in owner_map for a in aliases_of(vd))


def short_id(s, n=10) -> str:
    """日志里的短标识（不打印完整对象 ID / URL / token）。"""
    s = _clean(s)
    if not s:
        return "—"
    return s[:n] if len(s) <= n + 2 else s[:n] + "…"


def max_stats(old, new) -> dict:
    """同一条视频的统计数按「累计计数取最大」合并。

    点赞/转发/推荐/评论都是只增不减的累计数；不同 bridge 报文可能是
    不同时刻的快照（缓存旧值 / 局部口径），取最大比「保留第一条」更接近真值。
    """
    out = {}
    for f in STAT_FIELDS:
        try:
            a = int(getattr(old, f, 0) or 0)
        except Exception:
            a = 0
        try:
            b = int(getattr(new, f, 0) or 0)
        except Exception:
            b = 0
        out[f] = max(a, b)
    return out
