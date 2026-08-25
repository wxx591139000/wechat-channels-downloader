"""无头验证 GUI 引擎修复：复用 SniffEngine（gui.py 同一代码路径），
不起窗口、不动系统代理，确认 Master 能在事件循环内正常启动并监听 8899。"""
import socket
import sys
import time

BASE = r"C:\Users\Dancing\wechat-channels-downloader\sphdl"
sys.path.insert(0, BASE)

from sphdl.pipeline import Pipeline      # noqa: E402
from sphdl.addon import SphAddon         # noqa: E402
from sphdl.gui import SniffEngine        # noqa: E402  (import 不建窗口)


class MockApp:
    def __init__(self):
        self.addon = None

    def push_log(self, level, msg):
        print(f"[{level}] {msg}", flush=True)

    def push_event(self, item):
        pass


def port_open(port, timeout=1.0):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def main():
    PORT = 8899
    if port_open(PORT):
        print(f"端口 {PORT} 已被占用，无法测试", flush=True)
        sys.exit(2)

    app = MockApp()
    pipe = Pipeline(BASE + r"\downloads")
    app.addon = SphAddon(pipe)
    engine = SniffEngine(app)

    print("== 调用 engine.start(auto_proxy=False) ==", flush=True)
    ok = engine.start(PORT, auto_proxy=False)
    print(f"start() 返回: {ok}", flush=True)

    time.sleep(2)
    running = port_open(PORT)
    print(f"端口 {PORT} 监听中: {running}", flush=True)

    print("== 调用 engine.stop() ==", flush=True)
    engine.stop()
    time.sleep(2)
    closed = not port_open(PORT)
    print(f"停止后端口关闭: {closed}", flush=True)

    result = "PASS" if (ok and running and closed) else "FAIL"
    print(f"RESULT: {result}", flush=True)
    sys.exit(0 if result == "PASS" else 1)


if __name__ == "__main__":
    main()
