"""验证「停止下载」：
1. MpdDownloader 下载中途 cancel → DownloadCancelled 快速抛出，tmp 清理
2. Pipeline.cancel 对排队任务 → cancelled 事件、不执行
3. cancel 后再次下载可正常完成
"""
import http.server
import os
import socketserver
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FILE_SIZE = 2 * 1024 * 1024
DATA = os.urandom(FILE_SIZE)
SPEED = 32 * 1024  # 每 0.1s 发 32KB → ~320KB/s，2MB 约 6.4s


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", str(FILE_SIZE))
        self.end_headers()

    def do_GET(self):
        start = 0
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
        end = FILE_SIZE
        body = DATA[start:end]
        self.send_response(206 if start else 200)
        if start:
            self.send_header("Content-Range", f"bytes {start}-{end - 1}/{FILE_SIZE}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        # 慢速发送
        step = 32 * 1024
        for i in range(0, len(body), step):
            self.wfile.write(body[i:i + step])
            self.wfile.flush()
            time.sleep(0.1)


def main():
    with socketserver.ThreadingTCPServer(("127.0.0.1", 18924), Handler) as httpd:
        httpd.daemon_threads = True
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        from sphdl.downloader import MpdDownloader, DownloadCancelled
        from sphdl.pipeline import Pipeline
        from sphdl.entities import VideoData

        url = "http://127.0.0.1:18924/v.mp4"
        d = os.path.dirname(__file__)
        out1 = os.path.join(d, "cancel_test1.mp4")
        out2 = os.path.join(d, "cancel_test2.mp4")
        for f in (out1, out2):
            if os.path.exists(f):
                os.remove(f)

        # --- 测试 1: 下载中途 cancel ---
        ev = threading.Event()
        dl = MpdDownloader(url, out1, expected_size=FILE_SIZE, stop_event=ev)
        t0 = time.time()

        def run():
            dl.start()

        th = threading.Thread(target=run, daemon=True)
        th.start()
        time.sleep(1.5)  # 让它下到 ~480KB
        dl.cancel()
        th.join(timeout=10)
        dt = time.time() - t0
        assert not th.is_alive(), "cancel 后下载线程未退出（>10s）"
        assert not os.path.exists(out1), "cancel 后仍有成品文件"
        assert not os.path.exists(out1 + ".tmp"), "cancel 后 tmp 未清理"
        print(f"PASS 1: 中途 cancel {dt:.1f}s 后退出，无残留文件（应 <3s）")

        # --- 测试 2: Pipeline 排队任务 cancel ---
        events = []
        pipe = Pipeline(d, on_event=lambda *a: events.append(a))
        vd_a = VideoData(url=url + "?a", size=FILE_SIZE, description="A")
        vd_b = VideoData(url=url + "?b", size=FILE_SIZE, description="B")
        pipe.submit(vd_a)
        time.sleep(0.5)          # A 开始下载
        pipe.submit(vd_b)        # B 排队
        pipe.cancel(vd_b.url)    # 停止排队中的 B
        pipe.cancel(vd_a.url)    # 停止下载中的 A（尽快收尾）
        time.sleep(4)
        kinds = [e[0] for e in events]
        b_started = any(e[0] == "start" and e[1].url == vd_b.url for e in events)
        assert kinds.count("cancelled") == 2, f"应收到 2 个 cancelled 事件: {kinds}"
        assert not b_started, "被 cancel 的排队任务竟然执行了"
        print(f"PASS 2: 下载中+排队中 均成功停止，事件序列: {kinds}")

        # --- 测试 3: cancel 后重新下载可完整完成 ---
        ok = MpdDownloader(url, out2, expected_size=FILE_SIZE).start()
        size = os.path.getsize(out2) if os.path.exists(out2) else 0
        assert ok and size == FILE_SIZE, f"重新下载失败 ok={ok} size={size}"
        print(f"PASS 3: cancel 后重新下载完整完成 ({size} B)")

        # 清理
        for f in (out2,):
            if os.path.exists(f):
                os.remove(f)
        print("ALL PASS")


if __name__ == "__main__":
    main()
