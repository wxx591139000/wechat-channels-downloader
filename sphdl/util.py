"""小工具（日志脱敏等）"""

# 日志中需要打码的查询参数：视频号 CDN URL 的 encfilekey 等同下载凭证
_SENSITIVE_QUERY_KEYS = {
    "encfilekey", "key", "token", "sign", "sig", "vkey",
    "decode_key", "decodekey", "urlkey", "auth_key", "ckey",
}


def mask_url(url: str, limit: int = 80) -> str:
    """日志用 URL 脱敏：保留 scheme/host/路径，查询串中的敏感参数值
    替换为 ***（仅用于日志，不影响真实下载地址）。"""
    if not url:
        return url
    if "?" not in url:
        return url[:limit]
    head, qs = url.split("?", 1)
    masked = []
    for item in qs.split("&"):
        if "=" in item:
            k, _, v = item.partition("=")
            if k.lower() in _SENSITIVE_QUERY_KEYS and v:
                masked.append(f"{k}=***")
                continue
        masked.append(item)
    out = head + "?" + "&".join(masked)
    return out[:limit]


def describe_capture(vd) -> str:
    """捕获结构的脱敏摘要（日志用）：media 条数、各条 file_size/
    full_file_size/分辨率、是否含原始 fullUrl、spec 档数。不含 URL/token。"""
    try:
        raw = getattr(vd, "raw", None)
        if not isinstance(raw, dict):
            return "objectDesc=非字典"
        media = raw.get("media") or raw.get("media_list") or []
        if not isinstance(media, list):
            return f"media类型={type(media).__name__}"
        parts = [f"media条目={len(media)}"]
        for i, m in enumerate(media[:3]):
            if not isinstance(m, dict):
                continue
            fs = m.get("file_size") or m.get("fileSize") or 0
            ffs = m.get("full_file_size") or m.get("fullFileSize") or 0
            w, h = m.get("width") or 0, m.get("height") or 0
            fw = m.get("full_width") or m.get("fullWidth") or 0
            fh = m.get("full_height") or m.get("fullHeight") or 0
            has_full = bool(m.get("full_url") or m.get("fullUrl"))
            specs = m.get("spec")
            spec_n = len(specs) if isinstance(specs, list) else 0
            parts.append(
                f"[{i}] size={fs} full_size={ffs} {w}x{h} "
                f"full={fw}x{fh}{'+fullUrl' if has_full else ''} spec档={spec_n}")
        return " | ".join(parts)
    except Exception:
        return "describe_capture失败"
