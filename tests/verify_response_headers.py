"""回归：改写响应体后 Content-Length 必须等于实际发送字节（P68）。

用法：
    python tests/verify_response_headers.py                # 单元 + 源码断言
    python tests/verify_response_headers.py --online 18900  # 真代理实测

背景：注入 JS 时若只把 Content-Length 写成明文长度、却没删 Content-Encoding，
mitmproxy 发送时会重新 gzip 压缩 → 「声明 9431 / 实发 3686」→ 客户端永久等
剩余字节 → DOM 不加载完 → 播放器不初始化 → 视频号完全播不了。
"""
import gzip
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mitmproxy import http                       # noqa: E402
from mitmproxy.test import tflow                 # noqa: E402
from sphdl.addon import SphAddon                # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[PASS] {name}  {detail}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


def gzip_flow(html: bytes):
    gz = gzip.compress(html)
    resp = http.Response.make(
        200, gz,
        {"Content-Type": "text/html; charset=utf-8",
         "Content-Encoding": "gzip",
         "Content-Length": str(len(gz))})
    f = tflow.tflow(resp=True)
    f.response = resp
    return f


def bare_addon():
    """不跑 __init__（要连 pipeline），只准备注入需要的属性。"""
    ad = object.__new__(SphAddon)
    ad.bridge_js = "<script>/* __sphdl_hooked */</script>"
    ad.bridge_hooked_pages = 0
    ad.injected = False
    return ad


def header_body_consistent(resp):
    """HTTP 层唯一判据：声明长度 == 实际发送（编码后）字节数。"""
    declared = resp.headers.get("content-length")
    actual = len(resp.raw_content or b"")
    return declared is not None and declared.isdigit() and int(declared) == actual


def test_units():
    html = (b'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
            b'</head><body>finder preview</body></html>')

    # ① 先证明「旧写法」确实坏 —— 否则测试可能是假绿
    f_old = gzip_flow(html)
    old = (f_old.response.content.decode("utf-8", "ignore")
           .replace("</head>", "<script>hook()</script></head>", 1))
    f_old.response.content = old.encode("utf-8")
    f_old.response.headers["Content-Length"] = str(
        len(f_old.response.content))
    check("对照：旧写法确实造成头体不一致（测试有效）",
          not header_body_consistent(f_old.response),
          f"声明={f_old.response.headers.get('content-length')} "
          f"实发={len(f_old.response.raw_content)} "
          f"encoding={f_old.response.headers.get('content-encoding')}")

    # ② bridge 钩子注入后必须自洽
    f1 = gzip_flow(html)
    ad = bare_addon()
    ad._inject_bridge_hook(f1)
    check("bridge 注入：Content-Length == 实际发送字节",
          header_body_consistent(f1.response),
          f"声明={f1.response.headers.get('content-length')} "
          f"实发={len(f1.response.raw_content)}")
    check("bridge 注入：已删除 Content-Encoding（不再二次压缩）",
          not f1.response.headers.get("content-encoding"))
    check("bridge 注入：注入的脚本确实在明文里",
          b"__sphdl_hooked" in f1.response.content)
    check("bridge 注入：注入计数 +1", ad.bridge_hooked_pages == 1)

    # ③ 无压缩响应也不能被写坏（回归：不能因为修 gzip 就破坏普通页面）
    resp2 = http.Response.make(
        200, html, {"Content-Type": "text/html",
                    "Content-Length": str(len(html))})
    f2 = tflow.tflow(resp=True)
    f2.response = resp2
    bare_addon()._inject_bridge_hook(f2)
    check("无压缩页面：头体仍一致且脚本已注入",
          header_body_consistent(f2.response)
          and b"__sphdl_hooked" in f2.response.content,
          f"声明={f2.response.headers.get('content-length')} "
          f"实发={len(f2.response.raw_content)}")

    # ④ 结构性断言：不许再出现手写 Content-Length 赋值（P65 同族）
    src = (ROOT / "sphdl" / "addon.py").read_text(encoding="utf-8")
    check("源码：两处注入都调用 _rewrite_plain_body",
          src.count("self._rewrite_plain_body(") == 2,
          f"调用次数={src.count('self._rewrite_plain_body(')}")
    # helper 里那一处是**正确**写法（已删 encoding、按明文算长度），
    # 只允许存在这一处；注入方法里再出现就是回退了。
    n_cl = src.count('headers["Content-Length"] = str(len(')
    check("源码：手写 Content-Length 只剩 helper 一处",
          n_cl == 1, f"出现次数={n_cl}")
    check("源码：_rewrite_plain_body 会删 Content-Encoding",
          '"Content-Encoding", "Transfer-Encoding"' in src)


def test_online(port):
    """真代理实测：带 Accept-Encoding: gzip 拉页面，比较声明长度与实际字节。"""
    url = "https://channels.weixin.qq.com/finder-preview/pages/sph?id=ARRALTExZ9"
    tmpdir = Path(tempfile.gettempdir())
    hdr, body = tmpdir / "_sphdl_vrh.h", tmpdir / "_sphdl_vrh.bin"
    hdr.unlink(missing_ok=True)
    body.unlink(missing_ok=True)
    cmd = ["curl", "-s", "-x", f"http://127.0.0.1:{port}", "-k", "--raw",
           "--max-time", "25", "-H", "Accept-Encoding: gzip",
           "-D", str(hdr), "-o", str(body), url]
    r = subprocess.run(cmd, capture_output=True, timeout=40)
    check("线上：curl 执行成功", r.returncode == 0,
          (r.stderr.decode("utf-8", "ignore") or "")[:120])
    if not hdr.exists() or not body.exists():
        check("线上：拿到响应", False, "没有输出文件")
        return
    declared, enc = "", ""
    for line in hdr.read_text("utf-8", "ignore").splitlines():
        low = line.lower()
        if low.startswith("content-length:"):
            declared = line.split(":", 1)[1].strip()
        if low.startswith("content-encoding:"):
            enc = line.split(":", 1)[1].strip()
    actual = body.stat().st_size
    check("线上：Content-Length == 实际收到字节",
          declared.isdigit() and int(declared) == actual,
          f"声明={declared} 实收={actual} encoding={enc or '(无)'}")


def main():
    test_units()
    if "--online" in sys.argv:
        i = sys.argv.index("--online")
        port = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 8899
        test_online(port)
    print(f"\n结果：{PASS} PASS / {FAIL} FAIL")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
