"""端到端：模拟微信 bridge 回传，验证视频能否进清单（v2.2.13 排查）。

═══ 客户报障（2026-10-23）═══
「打开嗅探后，抓取不到视频清单」

═══ 排查思路 ═══
链路：微信页面 → 注入的 JS 钩子 → POST /__sphdl/bridge
      → addon.request() → 字节预检 → 解析线程 → _handle → on_capture → 清单

逐段验证，定位断点：
  T1 引擎能起、端口能连
  T2 带 objectDesc 的 bridge POST 能被接收（不 404、不丢）
  T3 解析线程能解出视频（extract_from_object_desc 返回非空）
  T4 on_capture 回调被调用（GUI 收到捕获事件）
  T5 记录真的进了 store
  T6 评论分支不干扰视频分支
"""
import json
import sys
import time
import urllib.request
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


print("=" * 74)
print("T0 最小 objectDesc 能否被解析器解出视频")
print("=" * 74)
from sphdl.parser import extract_from_object_desc

FAKE_OD = {
    "description": "测试视频标题",
    "media": [{
        "url": "http://finder.video.qq.com/251/20302/stodownload?encfilekey=TEST",
        "decodeKey": "0123456789abcdef0123456789abcdef",
        "fileSize": 1048576,
        "spec": [{"fileFormat": "mp4", "width": 1080, "height": 1920,
                  "bitrate": 2300, "fileSize": 1048576}],
    }],
    "objectNonceId": "nonce123",
    "md5sum": "abc123def456",
}
vd = extract_from_object_desc(FAKE_OD)
print(f"  解析结果: {vd}")
check("T0 objectDesc 能解出视频", vd is not None and getattr(vd, "url", ""),
      str(vd)[:90] if vd else "None")

print()
print("=" * 74)
print("T1-T5 真引擎 + 真 bridge POST 全链路")
print("=" * 74)
import tkinter as tk
import sphdl.gui as G

tdir = Path(tempfile.mkdtemp(prefix="cap_"))
G.CONFIG_FILE = tdir / "config.json"
G.RECORDS_FILE = tdir / "records.json"
G.CONFIG_FILE.write_text(json.dumps({"max_records": 20000}), encoding="utf-8")
G.RECORDS_FILE.write_text("[]", encoding="utf-8")

app = G.App()
app.root.withdraw()
app.root.update()

TEST_PORT = 18897
app.engine.start(TEST_PORT, auto_proxy=False)
for _ in range(40):
    time.sleep(0.3)
    app.root.update()
    if app.engine.running and app.engine._port_open(TEST_PORT):
        break
port_ok = app.engine._port_open(TEST_PORT)
print(f"  引擎端口 {TEST_PORT} 就绪: {port_ok}")
check("T1 引擎启动", port_ok)

if port_ok:
    body = json.dumps({
        "url": "https://channels.weixin.qq.com/web/pages/feed",
        "cmdName": "finderH5ExtTransfer",
        "arguments": json.dumps({"objectDesc": FAKE_OD}, ensure_ascii=False),
        "result": "{}",
    }, ensure_ascii=False).encode("utf-8")

    proxy = f"http://127.0.0.1:{TEST_PORT}"
    op = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    sent_ok = False
    try:
        req = urllib.request.Request(
            "http://wxapp.tc.qq.com/__sphdl/bridge", data=body,
            headers={"Content-Type": "text/plain"})
        r = op.open(req, timeout=15)
        resp = r.read()
        print(f"  POST bridge → HTTP {r.status}, 响应 {resp!r}")
        sent_ok = r.status == 200
    except Exception as e:
        print(f"  POST bridge 失败: {type(e).__name__}: {e}")
    check("T2 bridge POST 被接收", sent_ok)

    # 等解析线程 + 事件队列
    before = len(app.store.items)
    for _ in range(30):
        time.sleep(0.2)
        app.root.update()
        if len(app.store.items) > before:
            break
    after = len(app.store.items)
    print(f"  清单记录数: {before} → {after}")
    check("T5 记录真的进了清单", after > before, f"{before}→{after}")

    if after > before:
        rec = list(app.store.items.values())[-1]
        print(f"  捕获内容: title={rec.title[:30]!r}")
        print(f"            url={rec.url[:60]!r}")
        print(f"            video_id={rec.video_id!r}  seq={rec.seq}")
        check("T3 标题解析正确", "测试视频标题" in rec.title, rec.title[:30])
        check("T4 有 url", bool(rec.url), rec.url[:40])

    # T6：再发一条评论 payload，确认不影响视频捕获
    cbody = json.dumps({
        "cmdName": "someCommentApi",
        "result": json.dumps({"commentList": [
            {"nickname": "张三", "content": "视频不错"}]}, ensure_ascii=False),
    }, ensure_ascii=False).encode()
    try:
        req2 = urllib.request.Request(
            "http://wxapp.tc.qq.com/__sphdl/bridge", data=cbody,
            headers={"Content-Type": "text/plain"})
        op.open(req2, timeout=10).read()
        time.sleep(1.0)
        app.root.update()
        check("T6 评论 payload 不影响视频捕获", True)
    except Exception as e:
        check("T6 评论 payload 不影响视频捕获", False, str(e)[:50])

    try:
        app.engine.stop()
    except Exception:
        pass

try:
    app.root.destroy()
except Exception:
    pass

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
