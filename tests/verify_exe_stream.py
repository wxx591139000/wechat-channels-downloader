"""用真实 exe 起代理，实测首字节 —— 证明 stream_large_bodies 进了打包产物。

不碰系统代理（--no-auto-proxy），端口用 18898，测完立刻结束进程。
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
VERSION = __import__("re").search(
    r'__version__\s*=\s*"([^"]+)"',
    (ROOT / "sphdl" / "__init__.py").read_text(encoding="utf-8")).group(1)
EXE = ROOT / "dist" / f"v{VERSION}" / "微信视频号下载器" / "微信视频号下载器.exe"
PORT = 18898
URL = "https://dldir1v6.qq.com/weixin/Windows/WeChatSetup.exe"


def wait_port(port, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        s = socket.socket()
        s.settimeout(0.3)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            time.sleep(0.2)
        finally:
            s.close()
    return False


def main():
    if not EXE.exists():
        raise SystemExit(f"没有这个 exe: {EXE}")
    tmp = tempfile.mkdtemp(prefix="sphdl-v341-")
    env = dict(os.environ, SPHDL_DATA_DIR=tmp, SPHDL_DEBUG="1")
    p = subprocess.Popen([str(EXE), "--no-auto-proxy", "-p", str(PORT)],
                         cwd=str(EXE.parent), env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_port(PORT):
            raise SystemExit("exe 代理没起来")
        print(f"exe 代理已监听 {PORT}")
        px = {"http": f"http://127.0.0.1:{PORT}",
              "https": f"http://127.0.0.1:{PORT}"}
        t0 = time.time()
        r = requests.get(URL, headers={"Range": "bytes=0-5000000"},
                         stream=True, proxies=px, verify=False, timeout=60)
        first = None
        got = 0
        for chunk in r.iter_content(65536):
            if first is None:
                first = time.time() - t0
            got += len(chunk)
        total = time.time() - t0
        print(f"状态={r.status_code}｜首字节 {first:.2f}s｜收完 {total:.2f}s"
              f"｜{got / 1048576:.1f}MB")
        # 判定：流式生效 → 首字节远小于总时间
        ok = first is not None and first < 2.0
        print(("✅ 流式生效" if ok else "❌ 仍是全缓冲") +
              f"（首字节 {first:.2f}s / 阈值 2.0s）")
        return 0 if ok else 1
    finally:
        p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()


if __name__ == "__main__":
    sys.exit(main())
