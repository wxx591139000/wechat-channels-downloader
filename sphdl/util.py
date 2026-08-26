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
