"""用真 exe 起代理，验证 P68 修复进了打包产物。

两项判定：
  ① Content-Length == 实际收到字节（curl --raw，带 Accept-Encoding: gzip）
  ② Playwright 经该代理打开视频号页面，DOMContentLoaded 不再超时
     （修复前 30s 超时，正是客户「播不了」的直接对应指标）

不碰系统代理（--no-auto-proxy），端口 18901，数据目录用临时目录。
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import verify_response_headers as V    # noqa: E402
check, test_online = V.check, V.test_online

VERSION = __import__("re").search(
    r'__version__\s*=\s*"([^"]+)"',
    (ROOT / "sphdl" / "__init__.py").read_text(encoding="utf-8")).group(1)
EXE = ROOT / "dist" / f"v{VERSION}" / "微信视频号下载器" / "微信视频号下载器.exe"
PORT = 18901
URL = "https://weixin.qq.com/sph/ARRALTExZ9"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 "
      "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
      "MiniProgramEnv/Windows WeChat/3.9.5")


def wait_port(port, timeout=45):
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


def test_dom_loaded(port):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(
            headless=True, channel="msedge",
            args=["--ignore-certificate-errors"],
            proxy={"server": f"http://127.0.0.1:{port}"})
        ctx = b.new_context(ignore_https_errors=True, user_agent=UA)
        page = ctx.new_page()
        t0 = time.time()
        err = ""
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=25000)
        except Exception as e:                       # noqa: BLE001
            err = f"{type(e).__name__}: {str(e)[:100]}"
        dt = time.time() - t0
        check("DOMContentLoaded 不再挂起（<20s）",
              not err and dt < 20,
              f"耗时 {dt:.2f}s {err}")
        b.close()


def main():
    if not EXE.exists():
        raise SystemExit(f"没有这个 exe: {EXE}")
    tmp = tempfile.mkdtemp(prefix="sphdl-hdr-")
    env = dict(os.environ, SPHDL_DATA_DIR=tmp, SPHDL_DEBUG="1")
    p = subprocess.Popen(
        [str(EXE), "--no-auto-proxy", "-p", str(PORT)],
        cwd=str(EXE.parent), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_port(PORT):
            raise SystemExit("exe 代理没起来")
        print(f"v{VERSION} exe 代理已监听 {PORT}\n")
        test_online(PORT)
        test_dom_loaded(PORT)
        print(f"\n结果：{V.PASS} PASS / {V.FAIL} FAIL")
        return 1 if V.FAIL else 0
    finally:
        p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()


if __name__ == "__main__":
    sys.exit(main())
