"""IPv4 优先 / IPv6 不通时自动跳过（netfix）。

═══ 为什么需要这个（2026-10-23 真实故障）═══

客户报障：v2.2.3 启动嗅探后打开视频号，微信提示
「暂无法观看此视频，可尝试切换网络或检查是否配置了代理。」

诊断过程：
  · 系统代理 127.0.0.1:8899 正常，mitmproxy 在跑
  · 微信容器 WeChatAppEx.exe 确实连上了代理
  · 但代理里**一条微信域名的 flow 都没有**
  · 同一代理访问 open.workbuddy.cn（**只有 IPv4**）→ 200，0.26s
  · 同一代理访问 mp.weixin.qq.com（**有 IPv4 + IPv6**）→ 超时
  · 本机 IPv6 连通性：全部 TimeoutError
  · 直连 mp.weixin.qq.com → 200，0.5s（curl 有 Happy Eyeballs 会回退 IPv4）

→ **根因：本机 IPv6 不通，但域名有 AAAA 记录，mitmproxy 连 IPv6 就卡死。**
  微信所有域名（channels/finder/mp.weixin）都有 AAAA，所以全军覆没。
  curl/浏览器能自动回退，mitmproxy 不能 → 只有它出问题。

═══ 做法 ═══

启动时探测 IPv6 是否可用（连公共 IPv6 DNS 的 443）：
  · 可用 → 不动（保持双栈，未来 IPv6 正常时不影响）
  · 不可用 → 给 socket.getaddrinfo 装一层过滤，丢掉 AF_INET6 结果

★ 这个补丁是**进程内**的，不影响系统设置，不改注册表，
  只作用于本工具（含内嵌的 mitmproxy、requests 下载、公众号解析）。
"""

import socket
import time

_ORIG_GETADDRINFO = None
_INSTALLED = False
_IPV6_OK = None

# 用于探测的公共 IPv6（阿里 / 腾讯 DNS 的 IPv6 地址）
_PROBE_V6 = ("2400:3200::1", "2402:4e00::")

_MARKER = "netfix"


def ipv6_available(timeout: float = 1.5) -> bool:
    """探测本机 IPv6 是否真的能出网（有地址 ≠ 能连）。"""
    global _IPV6_OK
    if _IPV6_OK is not None:
        return _IPV6_OK
    for ip in _PROBE_V6:
        s = None
        try:
            s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            s.settimeout(timeout)
            t0 = time.time()
            s.connect((ip, 443))
            _IPV6_OK = True
            return True
        except Exception:
            continue
        finally:
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass
    _IPV6_OK = False
    return False


def install_prefer_ipv4(force: bool = False) -> bool:
    """IPv6 不通时，让 getaddrinfo 只返回 IPv4。

    返回 True = 已装补丁（或本来就有 IPv6，无需装）。

    force=True 时无条件跳过 IPv6（给用户手动兜底用）。
    """
    global _ORIG_GETADDRINFO, _INSTALLED
    if _INSTALLED:
        return True
    if not force and ipv6_available():
        return False

    _ORIG_GETADDRINFO = socket.getaddrinfo

    def _ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
        # 调用方明确要 IPv6 时不拦截（否则会把合法请求弄坏）
        if family == socket.AF_INET6:
            return _ORIG_GETADDRINFO(host, port, family, type, proto, flags)
        res = _ORIG_GETADDRINFO(host, port, family, type, proto, flags)
        out = [r for r in res if r[0] != socket.AF_INET6]
        # 只有 IPv6 记录的域名（极罕见）→ 原样返回，别把请求弄死
        return out or res

    _ipv4_only.__name__ = "getaddrinfo_ipv4_preferred"
    socket.getaddrinfo = _ipv4_only
    _INSTALLED = True
    return True


def status() -> dict:
    return {
        "ipv6_ok": _IPV6_OK,
        "patched": _INSTALLED,
    }


def reset_for_tests():
    """仅供测试：还原补丁 + 清缓存。"""
    global _INSTALLED, _ORIG_GETADDRINFO, _IPV6_OK
    if _INSTALLED and _ORIG_GETADDRINFO is not None:
        socket.getaddrinfo = _ORIG_GETADDRINFO
    _INSTALLED = False
    _ORIG_GETADDRINFO = None
    _IPV6_OK = None
