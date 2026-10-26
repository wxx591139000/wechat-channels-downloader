"""吊销判定安全性验证（v2.3.1）。

═══ 客户红线（2026-10-26）═══
「工具查到服务器授权码作废时才会降档，工具跟服务器失联时不能降档。」

即：**只能"证明作废"才降档；"拿不到证据"一律不降档。**

═══ 必须逐条验证的边界 ═══

| 场景                              | 必须的结果        |
|-----------------------------------|-------------------|
| 服务器明确返回作废                | ✅ 降档           |
| 服务器明确返回未作废              | ✅ 不降档         |
| 服务器 404 / 500                  | ❌ 不降档         |
| 服务器返回 HTML（被拦成登录页）   | ❌ 不降档         |
| 服务器返回 ok:false               | ❌ 不降档         |
| 服务器返回 ok:true 但没 revoked 字段 | ❌ 不降档      |
| 服务器返回 ok:true、revoked=[]    | ✅ 不降档（真的没作废）|
| 网络超时 / 连接拒绝               | ❌ 不降档         |
| 断网 + 有缓存（缓存里是这个码）   | ✅ 降档（有证据） |
| 断网 + 有缓存（缓存里没这个码）   | ❌ 不降档         |
| 断网 + 缓存过期（>7 天）          | ❌ 不降档         |
| 断网 + 无缓存                     | ❌ 不降档         |
| 缓存文件损坏（非法 JSON）         | ❌ 不降档         |
| 缓存被篡改成空                     | ❌ 不降档         |

★ 另有一条容易漏的：
**「降档」必须可逆** —— 服务器恢复该码后，下一次校验要自动恢复档位，
不能因为一次误报让客户永久降档。
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


# ══════════ 隔离环境 ══════════
tdir = Path(tempfile.mkdtemp(prefix="revoke_safe_"))
os.environ["LOCALAPPDATA"] = str(tdir)

from sphdl import revocation as rv

CODE = "SPHDL-TEST-AAA-BBB-CCC-DDD-EEE-FFF"
OTHER = "SPHDL-TEST-XXX-YYY-ZZZ-111-222-333"


def fresh_cache(content=None):
    """重置缓存文件。content=None 表示删掉。"""
    p = rv.cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if content is None:
        p.unlink(missing_ok=True)
    else:
        p.write_text(content, encoding="utf-8")


def fake_server(payload, status=200, raw=None):
    """起一个只回指定内容的 HTTP 服务，返回 URL。"""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = raw if raw is not None else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}/api/revoked", httpd


def dead_url():
    """一个必然连不上的地址。"""
    return "http://127.0.0.1:1/api/revoked"


print("=" * 74)
print("一、服务器明确表态的场景")
print("=" * 74)

# ① 明确作废 → 降档
fresh_cache(None)
u, s = fake_server({"ok": True, "revoked": [{"code": CODE, "reason": "已作废"}]})
hit, src = rv.fetch_revoked([CODE], endpoint=u, timeout=4)
print(f"  ① 明确作废 → 命中={list(hit.keys())[:1]} 来源={src}")
check("1 明确作废 → 命中", CODE in hit, str(src))
s.shutdown()

# ② 明确未作废 → 不降档
fresh_cache(None)
u, s = fake_server({"ok": True, "revoked": [], "count": 0})
hit, src = rv.fetch_revoked([CODE], endpoint=u, timeout=4)
print(f"  ② 明确未作废 → 命中={hit} 来源={src}")
check("2 明确未作废 → 不命中", not hit, str(src))
check("2b 来源=net（确认过服务器）", src == "net", src)
s.shutdown()

print()
print("=" * 74)
print("二、★服务器异常/畸形响应 → 一律不降档")
print("=" * 74)

# ③ 404
fresh_cache(None)
u, s = fake_server({"err": "not found"}, status=404)
hit, src = rv.fetch_revoked([CODE], endpoint=u, timeout=4)
print(f"  ③ 404 → 命中={bool(hit)} 来源={src}")
check("3 服务器 404 → 不降档", not hit, str(src))
s.shutdown()

# ④ 500
fresh_cache(None)
u, s = fake_server({"err": "boom"}, status=500)
hit, src = rv.fetch_revoked([CODE], endpoint=u, timeout=4)
print(f"  ④ 500 → 命中={bool(hit)} 来源={src}")
check("4 服务器 500 → 不降档", not hit, str(src))
s.shutdown()

# ⑤ 返回 HTML（被拦成登录页）—— 这正是我们踩过的坑
fresh_cache(None)
u, s = fake_server(None, raw=b"<!DOCTYPE html><html><body>login</body></html>")
hit, src = rv.fetch_revoked([CODE], endpoint=u, timeout=4)
print(f"  ⑤ 返回 HTML → 命中={bool(hit)} 来源={src}")
check("5 返回 HTML（登录页）→ 不降档", not hit, str(src))
s.shutdown()

# ⑥ ok:false
fresh_cache(None)
u, s = fake_server({"ok": False, "err": "ledger unreadable"})
hit, src = rv.fetch_revoked([CODE], endpoint=u, timeout=4)
print(f"  ⑥ ok:false → 命中={bool(hit)} 来源={src}")
check("6 服务器 ok:false → 不降档", not hit, str(src))
s.shutdown()

# ⑦ ok:true 但没有 revoked 字段
fresh_cache(None)
u, s = fake_server({"ok": True})
hit, src = rv.fetch_revoked([CODE], endpoint=u, timeout=4)
print(f"  ⑦ ok:true 无 revoked 字段 → 命中={bool(hit)} 来源={src}")
check("7 缺 revoked 字段 → 不降档", not hit, str(src))
s.shutdown()

# ⑧ revoked 是畸形结构（字符串而不是列表）
fresh_cache(None)
u, s = fake_server({"ok": True, "revoked": "oops"})
hit, src = rv.fetch_revoked([CODE], endpoint=u, timeout=4)
print(f"  ⑧ revoked 畸形 → 命中={bool(hit)} 来源={src}")
check("8 revoked 畸形（字符串）→ 不降档", not hit, str(src))
s.shutdown()

print()
print("=" * 74)
print("三、★网络失联 → 一律不降档（客户红线）")
print("=" * 74)

# ⑨ 连接拒绝 + 无缓存
fresh_cache(None)
hit, src = rv.fetch_revoked([CODE], endpoint=dead_url(), timeout=3)
print(f"  ⑨ 连接拒绝+无缓存 → 命中={bool(hit)} 来源={src}")
check("9 ★失联+无缓存 → 不降档", not hit and src == "none", f"{hit}/{src}")

# ⑩ 超时 + 无缓存
fresh_cache(None)
hit, src = rv.fetch_revoked([CODE],
                            endpoint="http://10.255.255.1/api/revoked",
                            timeout=2)
print(f"  ⑩ 超时+无缓存 → 命中={bool(hit)} 来源={src}")
check("10 ★超时+无缓存 → 不降档", not hit, f"{hit}/{src}")

print()
print("=" * 74)
print("四、缓存兜底的边界（缓存只能证明，不能瞎猜）")
print("=" * 74)

# ⑪ 断网 + 缓存里有这个码 → 降档（有证据）
fresh_cache(json.dumps({"ts": int(time.time()), "verified": True,
                        "codes": {CODE: "已作废"}}, ensure_ascii=False))
hit, src = rv.fetch_revoked([CODE], endpoint=dead_url(), timeout=3)
print(f"  ⑪ 断网+缓存命中 → 命中={bool(hit)} 来源={src}")
check("11 断网但缓存证明作废 → 降档", CODE in hit and src == "cache",
      f"{hit}/{src}")

# ⑫ 断网 + 缓存里没这个码 → 不降档
fresh_cache(json.dumps({"ts": int(time.time()), "verified": True,
                        "codes": {OTHER: "已作废"}}, ensure_ascii=False))
hit, src = rv.fetch_revoked([CODE], endpoint=dead_url(), timeout=3)
print(f"  ⑫ 断网+缓存无此码 → 命中={bool(hit)} 来源={src}")
check("12 ★断网但缓存没这个码 → 不降档", not hit, f"{hit}/{src}")

# ⑬ 断网 + 缓存过期（8 天前）→ 不降档
old_ts = int(time.time()) - 8 * 24 * 3600
fresh_cache(json.dumps({"ts": old_ts, "verified": True,
                        "codes": {CODE: "已作废"}}, ensure_ascii=False))
hit, src = rv.fetch_revoked([CODE], endpoint=dead_url(), timeout=3)
print(f"  ⑬ 缓存过期(8天) → 命中={bool(hit)} 来源={src}")
check("13 ★缓存过期 → 不降档", not hit, f"{hit}/{src}")

# ⑭ 缓存文件损坏
fresh_cache("{ this is not json")
hit, src = rv.fetch_revoked([CODE], endpoint=dead_url(), timeout=3)
print(f"  ⑭ 缓存损坏 → 命中={bool(hit)} 来源={src}")
check("14 ★缓存损坏 → 不降档", not hit, f"{hit}/{src}")

# ⑮ 缓存被篡改成奇怪结构
fresh_cache(json.dumps({"codes": "not a dict"}))
hit, src = rv.fetch_revoked([CODE], endpoint=dead_url(), timeout=3)
print(f"  ⑮ 缓存结构畸形 → 命中={bool(hit)} 来源={src}")
check("15 ★缓存结构畸形 → 不降档", not hit, f"{hit}/{src}")

# ⑯ 缓存为空字典
fresh_cache(json.dumps({"ts": int(time.time()), "verified": True,
                        "codes": {}}))
hit, src = rv.fetch_revoked([CODE], endpoint=dead_url(), timeout=3)
print(f"  ⑯ 缓存为空 → 命中={bool(hit)} 来源={src}")
check("16 ★缓存为空 → 不降档", not hit, f"{hit}/{src}")

# ⑯b 缓存没有 verified 标记（来源不明）→ 不降档
fresh_cache(json.dumps({"ts": int(time.time()),
                        "codes": {CODE: "已作废"}}, ensure_ascii=False))
hit, src = rv.fetch_revoked([CODE], endpoint=dead_url(), timeout=3)
print(f"  ⑯b 缓存无 verified 标记 → 命中={bool(hit)} 来源={src}")
check("16b ★缓存来源不明（无 verified）→ 不降档", not hit, f"{hit}/{src}")

# ⑯c 缓存 verified=False（伪造/半截）→ 不降档
fresh_cache(json.dumps({"ts": int(time.time()), "verified": False,
                        "codes": {CODE: "已作废"}}, ensure_ascii=False))
hit, src = rv.fetch_revoked([CODE], endpoint=dead_url(), timeout=3)
print(f"  ⑯c 缓存 verified=False → 命中={bool(hit)} 来源={src}")
check("16c ★缓存 verified=False → 不降档", not hit, f"{hit}/{src}")

# ⑯d 缓存超过 6 小时（但不到 7 天）→ 不降档（新窗口）
fresh_cache(json.dumps({"ts": int(time.time()) - 7 * 3600, "verified": True,
                        "codes": {CODE: "已作废"}}, ensure_ascii=False))
hit, src = rv.fetch_revoked([CODE], endpoint=dead_url(), timeout=3)
print(f"  ⑯d 缓存 7 小时前 → 命中={bool(hit)} 来源={src}")
check("16d ★缓存超 6 小时 → 不降档", not hit, f"{hit}/{src}")

# ⑯e 缓存 5 小时前（窗口内）→ 降档
fresh_cache(json.dumps({"ts": int(time.time()) - 5 * 3600, "verified": True,
                        "codes": {CODE: "已作废"}}, ensure_ascii=False))
hit, src = rv.fetch_revoked([CODE], endpoint=dead_url(), timeout=3)
print(f"  ⑯e 缓存 5 小时前 → 命中={bool(hit)} 来源={src}")
check("16e 缓存 5 小时内 → 降档（仍有效证据）", CODE in hit, f"{hit}/{src}")

print()
print("=" * 74)
print("五、★不传 codes 时服务器返回全量，也不能乱命中")
print("=" * 74)
fresh_cache(None)
u, s = fake_server({"ok": True, "revoked": [
    {"code": CODE, "reason": "已作废"}]})
# 客户端问的是 OTHER，服务器（不规范实现）回了全量含 CODE
hit, src = rv.fetch_revoked([OTHER], endpoint=u, timeout=4)
print(f"  问 OTHER，服务器回含 CODE 的全量 → 命中={list(hit.keys())} 来源={src}")
check("17 只命中自己问的码（不串）", CODE not in hit, str(list(hit.keys())))
s.shutdown()

print()
print("=" * 74)
print("六、GUI 层：失联时绝不降档")
print("=" * 74)
import sphdl.gui as G
import sphdl.license as lic

G.CONFIG_FILE = tdir / "config.json"
G.RECORDS_FILE = tdir / "records.json"
G.CONFIG_FILE.write_text("{}", encoding="utf-8")
G.RECORDS_FILE.write_text("[]", encoding="utf-8")

lic_file = ROOT / "sphdl" / "_lic_key.py"
if not lic_file.exists():
    secret = bytes.fromhex((ROOT / ".license_secret").read_text().strip())
    lic_file.write_text("K = %r\n" % lic.obfuscate(secret), encoding="utf-8")

good_code = lic.make_code(2000, product=lic.PRODUCT_VIDEO)
app = G.App()
app.root.withdraw()
app.root.update()
app._activate_license(good_code)
app.root.update()

before = app._has_download_license()
print(f"  激活后权限: {before}")
check("18 基线有权限", before is True)

# 模拟三种"失联"结果喂给 GUI
for label, hit, src in (
        ("无缓存失联", {}, "none"),
        ("缓存未命中", {}, "cache"),
        ("来源不可信但 hit 非空", {good_code: "作废"}, "none"),
):
    app._on_revoke_result(hit, src)
    app.root.update()
    print(f"  {label} → _revoked_reason={app._revoked_reason!r} "
          f"权限={app._has_download_license()}")
    check(f"18 {label} 不降档",
          app._has_download_license() is True and not app._revoked_reason,
          f"{app._revoked_reason}/{app._has_download_license()}")

print()
print("=" * 74)
print("七、★降档可逆：恢复后能回来")
print("=" * 74)
app._on_revoke_result({good_code: "授权码已作废"}, "net")
app.root.update()
print(f"  命中作废后权限: {app._has_download_license()}  "
      f"reason={app._revoked_reason!r}")
check("19 作废后降档", app._has_download_license() is False)

app._on_revoke_result({}, "net")
app.root.update()
print(f"  服务器恢复后权限: {app._has_download_license()}  "
      f"reason={app._revoked_reason!r}")
check("19 ★恢复后权限自动回来", app._has_download_license() is True)
check("19b 恢复后 reason 清空", app._revoked_reason == "")

app.root.destroy()

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
