"""验证：给 mitmproxy 设 stream_large_bodies 后，首字节时间是否恢复。

A：默认（=工具现状）
B：stream_large_bodies="1m"
C：stream_large_bodies="512k"
"""
import asyncio
import socket
import sys
import threading
import time

import requests
import urllib3
from mitmproxy import addons as maddons
from mitmproxy import options
from mitmproxy.master import Master

urllib3.disable_warnings()

URL = "https://dldir1v6.qq.com/weixin/Windows/WeChatSetup.exe"
RANGE = {"Range": "bytes=0-5000000"}      # 与 curl 那组一致：5MB


def wait_port(port, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        s = socket.socket()
        s.settimeout(0.3)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            time.sleep(0.1)
        finally:
            s.close()
    return False


def start(port, stream):
    opts = options.Options(listen_host="127.0.0.1", listen_port=port,
                           ssl_insecure=True)

    async def amain():
        m = Master(opts)
        m.addons.add(*maddons.default_addons())
        if stream:
            m.options.update(stream_large_bodies=stream)
        await m.run()

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        orig = socket.getaddrinfo

        def v4(host, p, family=0, *a, **kw):
            r = orig(host, p, family, *a, **kw)
            return [x for x in r if x[0] != socket.AF_INET6] or r

        socket.getaddrinfo = v4
        try:
            loop.run_until_complete(amain())
        except Exception as e:                 # noqa: BLE001
            sys.stderr.write(f"master: {e}\n")

    threading.Thread(target=run, daemon=True).start()
    if not wait_port(port):
        raise SystemExit(f"{port} 没起来")


def measure(port, label):
    px = {"http": f"http://127.0.0.1:{port}", "https": f"http://127.0.0.1:{port}"}
    t0 = time.time()
    r = requests.get(URL, headers=RANGE, stream=True, proxies=px,
                     verify=False, timeout=60)
    first = None
    got = 0
    for chunk in r.iter_content(65536):
        if first is None:
            first = time.time() - t0
        got += len(chunk)
    total = time.time() - t0
    r.close()
    print(f"{label:22} 首字节 {first:6.2f}s ｜ 收完 {total:6.2f}s ｜ "
          f"{got / 1048576:.1f}MB ｜ {got / max(total, .01) / 1048576:.2f}MB/s")
    return first, total


def main():
    print("URL:", URL, "| Range:", RANGE["Range"])
    start(18899, None)
    a = measure(18899, "A 现状(不流式)")
    start(18898, "1m")
    b = measure(18898, "B 流式 1m")
    start(18897, "512k")
    c = measure(18897, "C 流式 512k")
    print("\n=== 结论 ===")
    for name, v in (("A 现状", a), ("B 1m", b), ("C 512k", c)):
        print(f"{name:8} 首字节 {v[0]:.2f}s")
    if b[0] < 1.5 and a[0] > 5:
        print("→ stream_large_bodies 有效：首字节恢复流式（<1.5s）")


if __name__ == "__main__":
    main()
