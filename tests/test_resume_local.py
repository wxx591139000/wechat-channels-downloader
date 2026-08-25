"""本地模拟 CDN：支持 Range，但每个连接只给 ~400KB 就掐断（模拟间歇服务）。
验证 MpdDownloader 的断点续传 + 单连接顺序下载。"""
import http.server
import socketserver
import threading
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FILE_SIZE = 3 * 1024 * 1024  # 3MB
DATA = os.urandom(FILE_SIZE)
MAX_PER_CONN = 400 * 1024    # 每连接最多给 400KB 就掐断
CONNS = {"n": 0}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", str(FILE_SIZE))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_GET(self):
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            end = min(FILE_SIZE, start + MAX_PER_CONN)
            body = DATA[start:end]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end - 1}/{FILE_SIZE}")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            self.wfile.write(body)
            if end < FILE_SIZE:
                # 掐断连接（模拟 CDN 晾住后断开）
                try:
                    self.connection.close()
                except Exception:
                    pass
                CONNS["n"] += 1
                return
        else:
            body = DATA[:MAX_PER_CONN]
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            try:
                self.connection.close()
            except Exception:
                pass
            CONNS["n"] += 1


def main():
    with socketserver.ThreadingTCPServer(("127.0.0.1", 18923), Handler) as httpd:
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        from sphdl.downloader import download_file
        out = os.path.join(os.path.dirname(__file__), "resume_test.mp4")
        if os.path.exists(out):
            os.remove(out)
        ok = download_file("http://127.0.0.1:18923/video.mp4", out,
                           expected_size=FILE_SIZE)
        size = os.path.getsize(out) if os.path.exists(out) else 0
        print(f"result ok={ok} size={size} expected={FILE_SIZE} conn_drops={CONNS['n']}")
        assert ok and size == FILE_SIZE, "FAIL: resume did not complete"
        print("PASS: 单连接 + 断点续传 正确完成 3MB 下载（每连接被掐 400KB）")


if __name__ == "__main__":
    main()
