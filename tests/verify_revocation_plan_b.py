"""授权码吊销校验（方案 B）端到端验证（v2.3.0）。

═══ 背景 ═══
客户问：「发码平台作废授权码后，用户正在下载的任务会立刻停吗？」
实测原答案是「不会，而且毫无提示」—— 客户端纯本地 HMAC 校验，从不联网。

客户决定上方案 B（云端吊销名单）。本测试验证整条链路：

  服务器 /api/revoked  →  [{code, reason}]
              ↓
  客户端 启动 + 每 30 分钟拉一次
              ↓
  命中 → 降档 + 停所有下载 + 红色大字弹窗

覆盖：
  R1 服务器 /api/revoked 无需登录即可访问（客户端没有会话）
  R2 只返回被作废的码，正常的码不在名单里
  R3 不泄露客户名/价格等敏感信息（只有 code + 通用 reason）
  R4 恢复授权后该码从名单消失
  R5 客户端 fetch_revoked 能正确命中
  R6 客户端把结果缓存到本地（断网可用）
  R7 服务器不可达 → 用缓存命中（7 天宽限）
  R8 服务器不可达且无缓存 → 放行（不误伤正版客户）
  R9 命中后 _has_download_license() 立即变 False
  R10 命中后 _revoked_reason 有值，弹窗文案指向"已作废"
  R11 恢复后 _revoked_reason 清空，权限恢复
  R12 作废不影响已下载文件（只停任务，不删文件）
"""
import json
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


# ══════════ 起一个真实服务器（隔离在临时目录） ══════════
print("=" * 74)
print("准备：起一个真实发码后台（临时目录）")
print("=" * 74)

srv_mod = ROOT / "server" / "sphdl_admin.py"
src = srv_mod.read_text(encoding="utf-8")

tdir = Path(tempfile.mkdtemp(prefix="revoke_srv_"))
# 把 DATA 指到临时目录（实际写法是 `DATA = BASE / "data"`）
tmp_srv = tdir / "sphdl_admin.py"
patched = src.replace('DATA = BASE / "data"',
                      f'DATA = Path(r"{tdir}") / "data"')
assert patched != src, "未能替换 DATA 定义"
tmp_srv.write_text(patched, encoding="utf-8")
(tdir / "data").mkdir(parents=True, exist_ok=True)
print(f"  已把 DATA 指向: {tdir / 'data'}")

# 造台账：两个码，一个作废一个正常
from sphdl import license as lic
lic_file = ROOT / "sphdl" / "_lic_key.py"
if not lic_file.exists():
    secret = bytes.fromhex((ROOT / ".license_secret").read_text().strip())
    lic_file.write_text("K = %r\n" % lic.obfuscate(secret), encoding="utf-8")

code_void = lic.make_code(2000, product=lic.PRODUCT_VIDEO)
code_ok = lic.make_code(1000, product=lic.PRODUCT_VIDEO)
ledger = tdir / "data" / "licenses.jsonl"
ledger.write_text(
    json.dumps({"code": code_void, "tier": 2000, "customer": "客户甲",
                "price": "199", "issued": "2026-10-20 10:00:00"},
               ensure_ascii=False) + "\n"
    + json.dumps({"code": code_ok, "tier": 1000, "customer": "客户乙",
                  "price": "99", "issued": "2026-10-21 10:00:00"},
                 ensure_ascii=False) + "\n"
    + json.dumps({"event": "voided", "code": code_void,
                  "issued": "2026-10-26 09:00:00"}, ensure_ascii=False) + "\n",
    encoding="utf-8")
print(f"  台账: {ledger}  (2 个码，其中 1 个作废)")

# 启动服务器（随机端口）
import threading
import importlib.util

spec = importlib.util.spec_from_file_location("tmp_admin", tmp_srv)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# 关掉密码认证对测试的影响（只测 /api/revoked，它本来就不需要登录）
PORT = 18877
from http.server import ThreadingHTTPServer
httpd = ThreadingHTTPServer(("127.0.0.1", PORT), mod.H)
th = threading.Thread(target=httpd.serve_forever, daemon=True)
th.start()
time.sleep(0.6)
print(f"  服务器: http://127.0.0.1:{PORT}")

API = f"http://127.0.0.1:{PORT}/api/revoked"


def call_api(payload=None, url=API):
    """★ 用 GET + ?codes= （POST 会被服务器的会话校验拦成登录页）。"""
    import urllib.parse
    codes = (payload or {}).get("codes") or []
    u = url
    if codes:
        u = url + "?codes=" + urllib.parse.quote(",".join(codes))
    req = urllib.request.Request(
        u, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            raw = r.read()
        txt = raw.decode("utf-8", "replace").strip()
        if not txt.startswith("{"):
            return {"_err": "非 JSON（可能是登录页）: " + txt[:80]}
        return json.loads(txt)
    except Exception as e:
        return {"_err": f"{type(e).__name__}: {e}"}


print()
print("=" * 74)
print("R1-R4 服务器端点")
print("=" * 74)
resp = call_api({"codes": [code_void, code_ok]})
print(f"  响应: ok={resp.get('ok')} count={resp.get('count')}")
check("R1 无需登录即可访问（客户端没有会话）", resp.get("ok") is True,
      str(resp.get("_err") or ""))

rev = resp.get("revoked") or []
codes_in = [x.get("code") for x in rev]
print(f"  名单里的码: {[c[:16] + '…' for c in codes_in]}")
check("R2a 作废码在名单里", code_void in codes_in)
check("R2b 正常码不在名单里", code_ok not in codes_in)
check("R2c 只返回 1 条", len(rev) == 1, str(len(rev)))

item = rev[0] if rev else {}
print(f"  条目字段: {list(item.keys())}")
sensitive = ("customer", "price", "渠道", "备注", "note", "channel")
leaked = [k for k in item if any(s in k.lower() for s in sensitive)]
print(f"  每项字段只有: {list(item.keys())}")
check("R3a 不泄露客户名/价格", not leaked, str(leaked))
check("R3b reason 是通用文案",
      "作废" in str(item.get("reason", "")), str(item.get("reason")))
# 全响应体里也不能出现客户名
whole = json.dumps(resp, ensure_ascii=False)
check("R3c 整个响应不含客户名/价格",
      "客户甲" not in whole and "客户乙" not in whole and "199" not in whole,
      "含敏感信息" if ("客户甲" in whole or "199" in whole) else "干净")

# R4 恢复授权
with ledger.open("a", encoding="utf-8") as f:
    f.write(json.dumps({"event": "restored", "code": code_void,
                        "issued": "2026-10-26 09:30:00"},
                       ensure_ascii=False) + "\n")
resp2 = call_api({"codes": [code_void]})
rev2 = resp2.get("revoked") or []
print(f"  恢复后名单: {[x.get('code', '')[:16] + '…' for x in rev2]}")
check("R4 恢复后该码从名单消失", code_void not in [x.get("code") for x in rev2])

# 再加回去（后面客户端测试要用）
with ledger.open("a", encoding="utf-8") as f:
    f.write(json.dumps({"event": "voided", "code": code_void,
                        "issued": "2026-10-26 10:00:00"},
                       ensure_ascii=False) + "\n")

print()
print("=" * 74)
print("R5-R8 客户端 revocation 模块")
print("=" * 74)
from sphdl import revocation as rv

# 让缓存写到临时目录
cachedir = tdir / "client"
cachedir.mkdir(parents=True, exist_ok=True)
import os
os.environ["LOCALAPPDATA"] = str(tdir)
# 重新计算缓存路径
print(f"  缓存路径: {rv.cache_path()}")

hit, src_ = rv.fetch_revoked([code_void, code_ok], endpoint=API, timeout=6)
print(f"  fetch_revoked → 命中={list(hit.keys())[:1]}  来源={src_}")
check("R5a 命中作废码", code_void in hit, str(src_))
check("R5b 正常码未命中", code_ok not in hit)
check("R5c 来源=net（联网成功）", src_ == "net", src_)

check("R6 已写本地缓存", rv.cache_path().exists(),
      str(rv.cache_path()) if rv.cache_path().exists() else "无缓存文件")
cache_d = json.loads(rv.cache_path().read_text(encoding="utf-8")) \
    if rv.cache_path().exists() else {}
print(f"  缓存条数: {len(cache_d.get('codes') or {})}")
check("R6b 缓存里有作废码", code_void in (cache_d.get("codes") or {}))

# R7 服务器不可达 → 用缓存
hit2, src2 = rv.fetch_revoked([code_void],
                              endpoint=f"http://127.0.0.1:{PORT+1}/api/revoked",
                              timeout=3)
print(f"  服务器不可达 → 命中={bool(hit2)} 来源={src2}")
check("R7a 断网仍用缓存命中", code_void in hit2, str(src2))
check("R7b 来源=cache", src2 == "cache", src2)

# R8 无缓存 + 不可达 → 放行
rv.cache_path().unlink(missing_ok=True)
hit3, src3 = rv.fetch_revoked([code_void],
                              endpoint=f"http://127.0.0.1:{PORT+1}/api/revoked",
                              timeout=3)
print(f"  无缓存且不可达 → 命中={bool(hit3)} 来源={src3}")
check("R8 拿不到名单时放行（不误伤正版）", not hit3 and src3 == "none",
      f"{hit3} / {src3}")

print()
print("=" * 74)
print("R9-R12 GUI 行为")
print("=" * 74)
import sphdl.gui as G
import tkinter as tk

G.CONFIG_FILE = cachedir / "config.json"
G.RECORDS_FILE = cachedir / "records.json"
G.CONFIG_FILE.write_text("{}", encoding="utf-8")
G.RECORDS_FILE.write_text("[]", encoding="utf-8")

app = G.App()
app.root.withdraw()
app.root.update()

# 先激活一个正常码
app._activate_license(code_ok)
app.root.update()
print(f"  激活正常码后权限: {app._has_download_license()}")
check("R9a 基线：有权限", app._has_download_license() is True)

# 模拟「该码被作废」
app.cfg.set_code(lic.PRODUCT_VIDEO, code_void)
app.cfg.save()
app.root.update()

# 放一条正在下载的记录，验证会被停
rec = G.Record(id="dl1", title="正在下载的视频", url="http://example.com/a.mp4",
               decode_key="", size=1024, source="t",
               created="10-26 11:00:00", status="downloading", seq=1)
app.store.add(rec)
app._add_row(rec)
app.root.update()

# 触发吊销结果
app._on_revoke_result({code_void: "授权码已作废"}, "net")
for _ in range(8):
    app.root.update()
    time.sleep(0.06)

print(f"  _revoked_reason: {app._revoked_reason!r}")
print(f"  下载权限       : {app._has_download_license()}")
print(f"  正在下载的记录 : {app.store.items['dl1'].status}")
check("R9b ★作废后下载权限立即变 False",
      app._has_download_license() is False)
check("R10 有作废原因", bool(app._revoked_reason),
      app._revoked_reason)
check("R10b 原因文案指向作废",
      "作废" in app._revoked_reason, app._revoked_reason)
check("R11a 正在下载的任务被停止",
      app.store.items["dl1"].status in ("stopped", "failed", "waiting"),
      app.store.items["dl1"].status)

# 弹窗是否出现
tops = [w for w in app.root.winfo_children()
        if isinstance(w, tk.Toplevel) and "失效" in str(w.title())]
print(f"  失效弹窗数量: {len(tops)}")
check("R10c 弹出「授权码已失效」提示", len(tops) >= 1, str(len(tops)))
if tops:
    texts = []
    def walk(x):
        for c in x.winfo_children():
            if c.winfo_class() == "Text":
                try:
                    texts.append(c.get("1.0", "end"))
                except Exception:
                    pass
            else:
                try:
                    if c.cget("text"):
                        texts.append(str(c.cget("text")))
                except Exception:
                    pass
            walk(c)
    walk(tops[0])
    joined = " ".join(texts)
    print(f"  弹窗含「已下载的视频文件不受影响」: {'已下载的视频文件' in joined}")
    check("R12 弹窗说明已下载文件不受影响", "已下载的视频文件" in joined)
    for w in tops:
        w.destroy()

# R11 恢复
app._on_revoke_result({}, "net")
for _ in range(6):
    app.root.update()
    time.sleep(0.06)
print(f"  恢复后 _revoked_reason: {app._revoked_reason!r}")
print(f"  恢复后下载权限       : {app._has_download_license()}")
check("R11b 恢复后 reason 清空", app._revoked_reason == "")
check("R11c 恢复后权限回来", app._has_download_license() is True)

app.root.destroy()
httpd.shutdown()

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
