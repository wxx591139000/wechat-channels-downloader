"""验证 GUI 路径（SniffEngine）也吃到了流式策略。

用户是在 GUI 里点「启动嗅探」，走的是 SniffEngine，不是 CLI 的 run_sniff。
这里直接起 SniffEngine（不改系统代理、独立端口），再用 curl 同款请求测首字节。
"""
import queue
import socket
import sys
import time
from pathlib import Path

import requests
import urllib3

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
urllib3.disable_warnings()

PORT = 18897
URL = "https://dldir1v6.qq.com/weixin/Windows/WeChatSetup.exe"


class FakeApp:
    def __init__(self):
        self.ui_q = queue.Queue()
        from sphdl.addon import SphAddon
        self.addon = SphAddon(_Pipe(), auto_download=False)
        self.logs = []

    def push_log(self, level, msg):
        self.logs.append((level, msg))
        print(f"  [{level}] {msg}")

    def push_event(self, ev):
        self.ui_q.put(ev)


class _Pipe:
    def submit(self, *a, **k):
        pass


def main():
    from sphdl import gui

    app = FakeApp()
    eng = gui.SniffEngine(app)
    print("启动 SniffEngine（auto_proxy=False）…")
    ok = eng.start(PORT, auto_proxy=False)
    if not ok:
        print("引擎启动失败")
        for lv, m in app.logs[-8:]:
            print(" ", lv, m)
        return 1
    try:
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
        print(f"\n状态={r.status_code}｜首字节 {first:.2f}s｜收完 {total:.2f}s"
              f"｜{got / 1048576:.1f}MB")
        hit = any("流式转发" in m for _, m in app.logs)
        print("引擎日志里出现『流式转发』:", hit)
        good = first < 2.0 and hit
        print(("✅ GUI 路径流式生效" if good else "❌ GUI 路径仍不对"))
        return 0 if good else 1
    finally:
        eng.stop()
        time.sleep(0.5)


if __name__ == "__main__":
    sys.exit(main())
