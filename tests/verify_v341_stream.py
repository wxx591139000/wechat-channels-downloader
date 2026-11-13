"""v3.4.1 回归：大响应流式转发（修「打开嗅探就播不了视频」）。

分三组：
  A 结构性 —— 源码里必须有 stream_large_bodies，且位置在 default_addons 之后
  B 单元   —— 流式响应不能让 addon 崩，也不该被误当「空 body」
  C 实测   —— 同一 5MB Range 请求：不流式 vs 流式 的首字节时间（需联网）
"""
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[PASS] {name}" + (f"   [{detail}]" if detail else ""))
    else:
        FAIL += 1
        print(f"[FAIL] {name}" + (f"   [{detail}]" if detail else ""))


# ===================== A 结构性 =====================
def test_source():
    src = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8")
    check("策略集中在 proxy_manager.STREAM_LARGE_BODIES",
          'STREAM_LARGE_BODIES = "1m"' in
          (ROOT / "sphdl" / "proxy_manager.py").read_text(encoding="utf-8"))
    check("GUI 调用了 apply_stream_policy",
          "apply_stream_policy(master)" in src)
    i_add = src.find("master.addons.add(*addons.default_addons())")
    i_opt = src.find("apply_stream_policy(master)")
    check("设置在 default_addons 之后（否则选项未注册会抛异常）",
          -1 < i_add < i_opt,
          f"addons@{i_add} < option@{i_opt}")

    # ★ 这条是 v3.4.1 的教训：GUI 与 CLI 两套启动代码，只改一处等于没修
    m = (ROOT / "main.py").read_text(encoding="utf-8")
    check("CLI（run_sniff）也调用了 apply_stream_policy",
          "apply_stream_policy(master)" in m)
    # ★ 入口脚本不能用相对导入（exe 实测才暴露：attempted relative import）
    check("main.py 用绝对导入（相对导入在入口脚本必失败）",
          "from sphdl.proxy_manager import apply_stream_policy" in m)

    a = (ROOT / "sphdl" / "addon.py").read_text(encoding="utf-8")
    check("addon 有 responseheaders 钩子",
          "def responseheaders(" in a)
    check("responseheaders 只对 wxamedia 关流式",
          'host.endswith("wxamedia.com")' in a)

    # 所有读 body 的入口都要有流式保护
    for fn in ("_inject_js", "_inject_bridge_hook", "_save_miniapp_media"):
        i = a.find(f"def {fn}(")
        seg = a[i:i + 1200]
        check(f"{fn} 有流式保护",
              'getattr(flow.response, "stream", False)' in seg
              or 'getattr(resp, "stream", False)' in seg)


# ===================== B 单元 =====================
class _Pipe:
    def submit(self, *a, **k):
        pass


def test_unit():
    from mitmproxy.test import tflow
    from sphdl import addon as A

    ad = A.SphAddon(_Pipe(), auto_download=False)
    # ① 流式响应：response() 不能抛
    f = tflow.tflow(resp=True)
    f.response.stream = True
    f.response.headers["content-type"] = "video/mp4"
    f.request.path = "/x/stodownload"
    try:
        ad.response(f)
        ok = True
        err = ""
    except Exception as e:                    # noqa: BLE001
        ok, err = False, f"{type(e).__name__}: {e}"
    check("流式响应不崩 response()", ok, err)

    # ② 流式响应不该被当成「空 body」去解析 JSON
    ad._save_miniapp_media_safe = True
    f2 = tflow.tflow(resp=True)
    f2.response.stream = True
    f2.request.host = "a.vodpreview.wxamedia.com"
    f2.request.path = "/abcdefabcdefabcdefab/f0.mp4"
    try:
        ad._save_miniapp_media(f2)
        check("_save_miniapp_media 遇流式直接返回", True)
    except Exception as e:                    # noqa: BLE001
        check("_save_miniapp_media 遇流式直接返回", False,
              f"{type(e).__name__}: {e}")

    # ③ responseheaders 能把 wxamedia 改回缓冲
    f3 = tflow.tflow(resp=True)
    f3.response.stream = True
    f3.response.headers["content-length"] = str(5 * 1024 * 1024)
    f3.request.host = "a.vodpreview.wxamedia.com"
    ad.responseheaders(f3)
    check("responseheaders 把 wxamedia 改回缓冲",
          f3.response.stream is False)

    # ④ 超大文件不硬扛
    f4 = tflow.tflow(resp=True)
    f4.response.stream = True
    f4.response.headers["content-length"] = str(200 * 1024 * 1024)
    f4.request.host = "a.vodpreview.wxamedia.com"
    ad.responseheaders(f4)
    check("200MB 小程序媒体保持流式（不赌内存）",
          f4.response.stream is True)

    # ⑤ 其它域名不受影响
    f5 = tflow.tflow(resp=True)
    f5.response.stream = True
    f5.request.host = "finder.video.qq.com"
    ad.responseheaders(f5)
    check("视频号 CDN 保持流式", f5.response.stream is True)


# ===================== C 实测 =====================
def _wait(port, timeout=20):
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


def test_live(online: bool):
    if not online:
        print("[SKIP] 联网实测（加 --online 开启）")
        return
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "probe_stream_fix.py")],
        capture_output=True, text=True, timeout=240, cwd=str(ROOT))
    out = r.stdout + r.stderr
    print(out.strip()[-800:])
    import re
    m = re.search(r"A 现状\(不流式\)\s+首字节\s+([\d.]+)s", out)
    n = re.search(r"B 流式 1m\s+首字节\s+([\d.]+)s", out)
    if not (m and n):
        check("实测首字节对比", False, "解析不到输出")
        return
    a, b = float(m.group(1)), float(n.group(1))
    check("流式把首字节从数秒降到 1 秒内", b < 1.5, f"{a:.2f}s → {b:.2f}s")
    check("确实存在全缓冲（对照组首字节 > 3s）", a > 3, f"现状 {a:.2f}s")
    check("改善倍数 ≥ 5x", a / max(b, 0.01) >= 5, f"{a / max(b, .01):.1f}x")


def main():
    online = "--online" in sys.argv
    test_source()
    test_unit()
    test_live(online)
    print(f"\n合计 {PASS} 通过 / {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
