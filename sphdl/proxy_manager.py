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

    def setup(self, backup_path=None) -> bool:
        if self.system.lower() == "windows":
            # 接管前先把当前代理状态落盘（持久快照）：
            # 进程崩溃/被强杀后 atexit 不会执行，GUI「恢复网络代理」按钮
            # 靠这个文件仍能恢复到接管前的状态（2026-08-25 朋友电脑断网事故）。
            if backup_path is not None:
                try:
                    save_proxy_backup(backup_path)
                except Exception:
                    pass
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


# ---------------- 持久快照 / 应急恢复 ----------------

_PROXY_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"


def _broadcast_proxy_change():
    """通知系统代理设置已变更（让浏览器/微信立即生效）。"""
    try:
        import ctypes
        ctypes.windll.Wininet.InternetSetOptionW(0, 39, 0, 0)  # SETTINGS_CHANGED
        ctypes.windll.Wininet.InternetSetOptionW(0, 37, 0, 0)  # REFRESH
    except Exception:
        pass


def read_proxy_state() -> dict:
    """读取当前系统代理状态（只读，不修改）。
    返回 {"enabled": 0/1, "server": "host:port" 或 None}"""
    state = {"enabled": 0, "server": None}
    if platform.system().lower() != "windows":
        return state
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PROXY_REG_PATH, 0, winreg.KEY_READ)
        try:
            state["enabled"] = int(winreg.QueryValueEx(key, "ProxyEnable")[0])
        except OSError:
            pass
        try:
            state["server"] = winreg.QueryValueEx(key, "ProxyServer")[0]
        except OSError:
            pass
        winreg.CloseKey(key)
    except Exception:
        pass
    return state


def save_proxy_backup(path) -> dict:
    """把当前代理状态快照写入 JSON 文件（引擎接管代理前调用）。"""
    import json
    import datetime

    st = read_proxy_state()
    st["saved_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    Path(path).write_text(json.dumps(st, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return st


def restore_proxy(backup_path) -> tuple:
    """应急恢复网络代理（GUI「恢复网络代理」按钮）。

    - 有快照：恢复到接管前的状态（原来用 Clash 等 VPN → 恢复 VPN 端口；
      原来无代理 → 关闭代理）
    - 无快照（崩溃后/旧版本）：兜底直接关闭代理，并提示用户如用 VPN 自行重开
    返回 (ok, 说明文字)
    """
    import json

    if platform.system().lower() != "windows":
        return True, "非 Windows，无需恢复"
    backup = None
    try:
        backup = json.loads(Path(backup_path).read_text(encoding="utf-8"))
    except Exception:
        backup = None

    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PROXY_REG_PATH, 0,
                             winreg.KEY_ALL_ACCESS)
        if backup and "enabled" in backup:
            enabled = int(backup.get("enabled", 0))
            server = backup.get("server")
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, enabled)
            if server:
                winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, server)
            winreg.CloseKey(key)
            _broadcast_proxy_change()
            if enabled and server:
                return True, (f"已恢复到接管前状态：代理开启 {server}"
                              f"（{backup.get('saved_at', '未知时间')} 的快照）")
            return True, "已恢复到接管前状态：代理关闭"
        else:
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
            winreg.CloseKey(key)
            _broadcast_proxy_change()
            return True, ("无代理快照，已兜底关闭系统代理。"
                          "若你平时用 Clash/VPN 走系统代理，请到该工具里重新开启"
                          "（或设置→网络→代理→手动填写你的代理）")
    except Exception as e:
        return False, f"恢复失败: {e}（可手动：设置→网络→代理 关闭代理）"


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
