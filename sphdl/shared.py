"""进程内共享状态：addon（代理侧）捕获到的微信 webview UA，
供 downloader 复用——CDN 对带 MicroMessenger 的 UA 服务更快。
"""
_WECHAT_UA: str = ""


def set_wechat_ua(ua: str):
    global _WECHAT_UA
    if ua and ua != _WECHAT_UA:
        _WECHAT_UA = ua


def get_wechat_ua() -> str:
    return _WECHAT_UA
