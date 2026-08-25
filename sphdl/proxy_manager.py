"""
Windows 系统代理管理（自动设置/清理 mitmproxy 代理）
改编自参考实现 ThinkerWen/wechat-downloader 的 proxy_manager.py
"""
import platform
from pathlib import Path


class ProxyManager:
    def __init__(self, host="127.0.0.1", port=8899):
        self.host = host
        self.port = port
        self.system = platform.system()
        self._was_enabled = None
        self._was_server = None   # 原 ProxyServer 值（用于精确恢复）
        self._touched = False     # 是否真正改过注册表

    def setup(self) -> bool:
        if self.system.lower() == "windows":
            return self._setup_windows()
        print(f"[提示] 请手动设置系统代理: {self.host}:{self.port}")
        return False

    def _setup_windows(self) -> bool:
        try:
            import winreg
            import ctypes

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
                0, winreg.KEY_ALL_ACCESS)
            try:
                enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
                self._was_enabled = bool(enabled)
            except OSError:
                self._was_enabled = False
            try:
                server, _ = winreg.QueryValueEx(key, "ProxyServer")
                self._was_server = server
            except OSError:
                self._was_server = None
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, f"{self.host}:{self.port}")
            self._touched = True
            winreg.CloseKey(key)

            try:
                ctypes.windll.Wininet.InternetSetOptionW(0, 39, 0, 0)  # SETTINGS_CHANGED
                ctypes.windll.Wininet.InternetSetOptionW(0, 37, 0, 0)  # REFRESH
            except Exception:
                pass
            return True
        except Exception as e:
            print(f"[错误] 设置系统代理失败: {e}")
            return False

    def cleanup(self) -> bool:
        if self.system.lower() != "windows":
            return True
        try:
            import winreg
            import ctypes
            if not self._touched:
                return True
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
                0, winreg.KEY_ALL_ACCESS)
            # 精确恢复到运行前的状态（原来开着用户自己的代理 → 原样还给用户）
            if self._was_enabled:
                winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
            else:
                winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
            if self._was_server is not None:
                winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, self._was_server)
            winreg.CloseKey(key)
            try:
                ctypes.windll.Wininet.InternetSetOptionW(0, 39, 0, 0)  # SETTINGS_CHANGED
                ctypes.windll.Wininet.InternetSetOptionW(0, 37, 0, 0)  # REFRESH
            except Exception:
                pass
            self._touched = False
            return True
        except Exception as e:
            print(f"[警告] 清理系统代理失败，请手动检查: {e}")
            return False


def get_mitmproxy_certs():
    """返回 mitmproxy 证书 (pem, p12) 路径"""
    home = Path.home()
    d = home / ".mitmproxy"
    return d / "mitmproxy-ca-cert.pem", d / "mitmproxy-ca-cert.p12"


def check_proxy_alive(port: int, timeout: float = 3.0) -> bool:
    """通过代理访问 mitm.it 验证代理是否可用"""
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    try:
        r = requests.get(
            "http://mitm.it",
            proxies={"http": f"http://127.0.0.1:{port}", "https": f"http://127.0.0.1:{port}"},
            timeout=timeout, verify=False)
        return r.status_code == 200
    except Exception:
        return False
