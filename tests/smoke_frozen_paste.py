"""冻结 exe 冒烟测试：本地 HTTP 服务 + 加密样本 → 冻结版 --paste 全流程"""
import json
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sphdl.decryptor import _xor_head, ENC_LEN  # noqa: E402

PORT = 18888
KEY = 555
WORK = Path(__file__).resolve().parent / "_smoke"
WORK.mkdir(exist_ok=True)

# 1. 造"加密 mp4"样本（ftyp 头 + 伪载荷，XOR 前 ENC_LEN 字节）
plain = bytearray(b"\x00\x00\x00\x20ftypisom" + b"\x00\x00\x02\x00isomiso2avc1mp41")
plain += (bytes(range(256)) * 600)[:ENC_LEN]   # 总长 = 头 + ENC_LEN（须 ≥ 128KB）
enc = bytearray(plain)
_xor_head(enc, KEY)
enc_path = WORK / "sample.enc"
enc_path.write_bytes(bytes(enc))

# 2. 本地 HTTP 服务
class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WORK), **kw)
    def log_message(self, *a):
        pass

srv = HTTPServer(("127.0.0.1", PORT), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.5)

# 3. 造 object_desc JSON（camelCase，parser 已验证支持）
payload = {
    "description": "冻结版冒烟测试视频",
    "media": [{
        "url": f"http://127.0.0.1:{PORT}/sample.enc",
        "decodeKey": KEY,
        "fileSize": len(enc),
        "mediaType": 1,
    }],
}
jf = WORK / "object_desc.json"
jf.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

# 4. 跑冻结 exe
exe = Path(r"C:\Users\Dancing\wechat-channels-downloader\sphdl\dist\微信视频号下载器\微信视频号下载器.exe")
dl = WORK / "dl"
dl.mkdir(exist_ok=True)
print(f"运行: {exe.name} --paste {jf.name} -d {dl}")
t0 = time.time()
r = subprocess.run([str(exe), "--paste", str(jf), "-d", str(dl)],
                   capture_output=True, text=True, timeout=120)
dt = time.time() - t0
print(f"exit={r.returncode} 耗时={dt:.1f}s")
if r.stdout:
    print("stdout:", r.stdout.strip()[-800:])
if r.stderr:
    print("stderr:", r.stderr.strip()[-500:])

# 5. 验证产物
ok = False
for f in dl.iterdir():
    if f.suffix == ".mp4":
        head = f.read_bytes()[:16]
        print(f"产物: {f.name} {f.stat().st_size}B head={head.hex()}")
        ok = f.stat().st_size == len(plain) and head[4:8] == b"ftyp"
        if ok:
            # 完整比对
            ok = f.read_bytes() == bytes(plain)
            print("内容与原文完全一致" if ok else "!! 内容不一致")
srv.shutdown()
print("SMOKE_RESULT:", "PASS" if ok and r.returncode == 0 else "FAIL")
sys.exit(0 if (ok and r.returncode == 0) else 1)
