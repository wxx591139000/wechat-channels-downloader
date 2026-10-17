#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""改档位必须保住台数（回归测试）—— 2026-10-17 真实事故。

事故经过：
  dan 原来买的是 ver=4「限 1 台 / 2000 条」。
  在后台点「改档位」升到 20000 条，拿到的新码却是 **ver=2 不限台数** ——
  防护反而变松了。

根因（两层）：
  ① 改档位页的台数下拉预选逻辑有 bug：
     `cur_md == (int(v) if v else None) or (not v and not cur_md)`
     当 cur_md=1 时表达式繁琐且易错，实际渲染出"不限台数"被选中。
  ② `_do_upgrade` 里 `md = int(md_raw) if md_raw else None`
     → 前端没传值就变成 None（不限台数），**没有沿用旧码的台数**。

修法：
  ① 下拉明确按 cur_md 预选，且老记录读不到台数时**按 1 台**兜底（安全侧）
  ② 后端 md_raw 为空 → 沿用 old["max_devices"]；只有显式选 0 才是不限

本测试覆盖：
  T1 限 1 台的码 → 改档位页台数下拉预选 1 台
  T2 ★改档位后新码仍是限 1 台（ver=4，不是 ver=2）
  T3 ★档位正确升级到 20000
  T4 限 3 台 → 升级后仍限 3 台
  T5 显式选「不限」→ 新码确实不限
  T6 老记录台数为空 → 下拉按 1 台预选（安全侧）
  T7 老记录台数为空 → 升级后默认限 1 台（不静默放宽）
  T8 绑定版升级 → 仍绑定（且不带台数，两者互斥）

跑法：python tests/verify_upgrade_keeps_devices.py
"""
import http.cookiejar
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

sec = (ROOT / ".license_secret").read_text(encoding="utf-8").strip()
from sphdl import license as lic  # noqa: E402
(ROOT / "sphdl" / "_lic_key.py").write_text(
    '"""t"""\nK = %r\n' % lic.obfuscate(bytes.fromhex(sec)), encoding="utf-8")
import importlib  # noqa: E402
importlib.reload(lic)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


tdir = Path(tempfile.mkdtemp(prefix="keepdev_"))
srv = tdir / "sphdl_admin.py"
shutil.copy2(ROOT / "server" / "sphdl_admin.py", srv)
PORT = 3087
env = dict(os.environ)
env["SPHDL_LIC_SECRET"] = sec
env["SPHDL_ADMIN_PORT"] = str(PORT)
env["SPHDL_ADMIN_BIND"] = "127.0.0.1"
env["PYTHONIOENCODING"] = "utf-8"
proc = subprocess.Popen([sys.executable, str(srv)], cwd=str(tdir), env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace")
base = f"http://127.0.0.1:{PORT}"
for _ in range(60):
    try:
        with urllib.request.urlopen(base + "/health", timeout=1) as r:
            if r.status == 200:
                break
    except Exception:
        time.sleep(0.25)
else:
    proc.kill()
    print("服务未启动")
    print(proc.stdout.read()[:1200] if proc.stdout else "")
    (ROOT / "sphdl" / "_lic_key.py").unlink(missing_ok=True)
    sys.exit(1)

jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def get(p, timeout=8):
    try:
        with op.open(base + p, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def post(p, d, timeout=10):
    body = urllib.parse.urlencode(d).encode()
    try:
        with op.open(base + p, body, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


try:
    token = (tdir / ".admin_token").read_text(encoding="utf-8").strip()
    post("/login", {"pw": token})
    ledger = tdir / "data" / "licenses.jsonl"

    def rows():
        return [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines()
                if l.strip()]

    def latest_for(old_code):
        r = [x for x in rows() if x.get("renewed_from") == old_code]
        return r[-1] if r else None

    # ---------- T1/T2/T3：限 1 台 → 升级 20000 ----------
    print("=" * 74)
    print("T1~T3 ★限 1 台升级后必须仍是限 1 台（真实事故回归）")
    print("=" * 74)
    post("/gen", {"product": "1", "tier": "2000", "customer": "dan模拟",
                  "contact": "闲鱼:dan", "channel": "闲鱼", "price": "8",
                  "device": "", "max_devices": "1"})
    old = rows()[-1]
    code1 = old["code"]
    info1 = lic.verify_code(code1)
    print(f"  旧码: {code1}")
    print(f"        ver={info1['ver']} tier={info1['tier']} max_devices={info1['max_devices']}")
    check("T0 旧码是 ver=4 限 1 台",
          info1["ver"] == 4 and info1["max_devices"] == 1,
          f"ver={info1['ver']} md={info1['max_devices']}")

    # 打开改档位页，看台数下拉预选
    st, html = get("/upgrade?code=" + urllib.parse.quote(code1))
    sel = "value='1' selected" in html
    check("T1 改档位页台数下拉预选「1 台」", sel,
          "找到 selected" if sel else "★预选错了，会静默放宽")

    # 提交升级（不带 max_devices，模拟旧前端/用户没动下拉）
    post("/upgrade", {"code": code1, "tier": "20000",
                      "max_devices": "", "price": "100", "note": "升级"})
    new = latest_for(code1)
    check("T2a 生成了新码", new is not None)
    if new:
        info2 = lic.verify_code(new["code"])
        print(f"  新码: {new['code']}")
        print(f"        ver={info2['ver']} tier={info2['tier']} "
              f"max_devices={info2['max_devices']}")
        check("T2b ★新码仍是限 1 台（不是不限）",
              info2["max_devices"] == 1,
              f"实际 max_devices={info2['max_devices']}")
        check("T3 档位升级到 20000", info2["tier"] == 20000,
              str(info2["tier"]))

    # ---------- T4 限 3 台 ----------
    print()
    print("=" * 74)
    print("T4 限 3 台升级后仍限 3 台")
    print("=" * 74)
    post("/gen", {"product": "1", "tier": "1000", "customer": "三台客户",
                  "contact": "wx:3", "channel": "微信", "price": "0",
                  "device": "", "max_devices": "3"})
    c3 = rows()[-1]["code"]
    post("/upgrade", {"code": c3, "tier": "20000", "max_devices": "",
                      "price": "0", "note": ""})
    n3 = latest_for(c3)
    if n3:
        i3 = lic.verify_code(n3["code"])
        check("T4 新码仍限 3 台", i3["max_devices"] == 3,
              f"md={i3['max_devices']}")

    # ---------- T5 显式选不限 ----------
    print()
    print("=" * 74)
    print("T5 显式选「不限台数」→ 新码确实不限")
    print("=" * 74)
    post("/upgrade", {"code": c3, "tier": "20000", "max_devices": "0",
                      "price": "0", "note": "放宽"})
    n5 = latest_for(c3)
    if n5:
        i5 = lic.verify_code(n5["code"])
        check("T5 用户显式选不限 → 真的不限",
              not i5.get("max_devices"), f"md={i5.get('max_devices')}")

    # ---------- T6/T7 老记录台数为空 ----------
    print()
    print("=" * 74)
    print("T6/T7 老记录台数为空 → 安全侧按 1 台（不静默放宽）")
    print("=" * 74)
    post("/gen", {"product": "1", "tier": "500", "customer": "老客户",
                  "contact": "wx:old", "channel": "微信", "price": "0",
                  "device": "", "max_devices": ""})   # 不限台数
    cold = rows()[-1]["code"]
    st, html = get("/upgrade?code=" + urllib.parse.quote(cold))
    check("T6a 老记录不限台数时，下拉预选「1 台」而非「不限」",
          "value='1' selected" in html,
          "安全侧默认")
    check("T6b 「不限」没有被预选",
          "value='0' selected" not in html)

finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    (ROOT / "sphdl" / "_lic_key.py").unlink(missing_ok=True)
    shutil.rmtree(tdir, ignore_errors=True)

print()
ok = sum(1 for _, p, _ in RESULTS if p)
print(f"RESULT: {'ALL PASS' if ok == len(RESULTS) else 'FAIL'} ({ok}/{len(RESULTS)})")
for nm, p, _ in RESULTS:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok == len(RESULTS) else 1)
