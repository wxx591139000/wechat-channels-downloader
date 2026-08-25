"""后台监控器：盯 8899 端口 / downloads 新文件 / 系统代理，事件写 monitor.log。
用于跟进真实微信流量测试。工具退出(代理掉线)或 15 分钟上限时自动结束。"""
import time
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent
DL = BASE / "downloads"
LOG = BASE / "monitor.log"

CAP_SECONDS = 900  # 15 min 上限


def port_up() -> bool:
    try:
        out = subprocess.check_output("netstat -ano", shell=True, text=True, timeout=10)
        for line in out.splitlines():
            if ":8899" in line and "LISTENING" in line.upper():
                return True
        return False
    except Exception:
        return False


def proxy_enable():
    try:
        import winreg
        k = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0, winreg.KEY_READ)
        v, _ = winreg.QueryValueEx(k, "ProxyEnable")
        winreg.CloseKey(k)
        return v
    except Exception:
        return None


def dl_files():
    if not DL.exists():
        return []
    return sorted((f.name, f.stat().st_size) for f in DL.iterdir() if f.is_file())


def main():
    was_up = False
    last_names = set()
    start = time.time()
    with open(LOG, "a", encoding="utf-8") as log:
        def emit(msg):
            line = f"[{time.strftime('%H:%M:%S')}] {msg}"
            print(line, flush=True)
            log.write(line + "\n")
            log.flush()

        emit("=== monitor started ===")
        while time.time() - start < CAP_SECONDS:
            up = port_up()
            pe = proxy_enable()
            files = dl_files()
            names = {n for n, _ in files}

            if up and not was_up:
                emit(f"PROXY UP  (8899 listening, ProxyEnable={pe})  -> 开始捕获中")
                was_up = True
            if up:
                new = names - last_names
                if new and last_names:  # 跳过启动时已存在的
                    for n in sorted(new):
                        sz = dict(files).get(n, 0)
                        emit(f"NEW FILE  {n}  ({sz/1024/1024:.2f} MB)")
                last_names = names
            if was_up and not up:
                emit("PROXY DOWN  (工具已退出/代理掉线)")
                time.sleep(5)
                break
            time.sleep(3)
        emit("=== monitor ended ===")


if __name__ == "__main__":
    main()
