#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""改档位（升级/降级/续期）测试 —— v2.1.4。

用户场景：朋友用 2000 行激活，想升级成 2 万行。
        要求：不用重填客户信息，在「查客户」处直接改档位拿到新码。

覆盖：
  U1 查客户页有「改档位」入口
  U2 改档位页带出原客户信息（免重填）
  U3 ★升级 2000 → 20000：新码档位正确
  U4 ★客户信息自动沿用（昵称/联系方式/渠道）
  U5 新码可被客户端校验
  U6 旧码标记 superseded（历史链完整）
  U7 降级也能用
  U8 台数设置能改
  U9 绑定版改档位时沿用原绑定
  U10 台账保留全部历史（不删旧记录）

跑法：python tests/verify_upgrade.py
"""
import hashlib
import http.cookiejar
import json
import os
import shutil
import socket
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


tdir = Path(tempfile.mkdtemp(prefix="upg_"))
srv = tdir / "sphdl_admin.py"
shutil.copy2(ROOT / "server" / "sphdl_admin.py", srv)
PORT = 3086
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
    print(proc.stdout.read()[:1500] if proc.stdout else "")
    (ROOT / "sphdl" / "_lic_key.py").unlink(missing_ok=True)
    sys.exit(1)

jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def get(path, timeout=8):
    try:
        with op.open(base + path, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def post(path, data, timeout=10):
    body = urllib.parse.urlencode(data).encode()
    try:
        with op.open(base + path, body, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


try:
    token = (tdir / ".admin_token").read_text(encoding="utf-8").strip()
    post("/login", {"pw": token})
    ledger = tdir / "data" / "licenses.jsonl"

    # ---------- 准备：发一个 2000 的码 ----------
    print("=" * 74)
    print("准备：发一个专业版(2000) 给「朋友小王」")
    print("=" * 74)
    post("/gen", {"product": "1", "tier": "2000", "customer": "朋友小王",
                  "contact": "微信:xiaowang", "channel": "朋友",
                  "price": "0", "device": "", "max_devices": ""})
    rows = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    old = rows[-1]
    old_code = old["code"]
    print(f"  旧码: {old_code}  档位: {old['tier_label']}")
    check("准备 a 旧码已生成", old["tier"] == 2000, old["tier_label"])
    check("准备 b 旧码可校验", lic.verify_code(old_code) is not None)

    # ---------- U1 查客户页有入口 ----------
    print("\n" + "=" * 74)
    print("U1 查客户页有「改档位」入口")
    print("=" * 74)
    st, txt = get("/customers?q=" + urllib.parse.quote("小王"))
    check("U1a 能看到客户「朋友小王」", "朋友小王" in txt)
    check("U1b 有「改档位」链接", "/upgrade?code=" in txt, "有入口")
    check("U1c 链接带上了旧码", urllib.parse.quote(old_code) in txt
          or old_code in txt)

    # ---------- U2 改档位页带出原信息 ----------
    print("\n" + "=" * 74)
    print("U2 改档位页带出原客户信息（免重填）")
    print("=" * 74)
    st, txt = get("/upgrade?code=" + urllib.parse.quote(old_code))
    check("U2a 页面可打开", st == 200)
    check("U2b 显示客户名", "朋友小王" in txt)
    check("U2c 显示原档位", "专业版" in txt)
    check("U2d 有档位下拉", 'name="tier"' in txt)
    check("U2e 有台数下拉", 'name="max_devices"' in txt)
    check("U2f 原档位被预选", "value='2000' selected" in txt
          or 'value="2000" selected' in txt)

    # ---------- U3 升级 2000 → 20000 ----------
    print("\n" + "=" * 74)
    print("U3 ★升级：2000 → 20000")
    print("=" * 74)
    st, txt = post("/upgrade", {"code": old_code, "tier": "20000",
                                "max_devices": "", "price": "100",
                                "note": "补差价升级"})
    rows2 = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines()
             if l.strip()]
    news = [r for r in rows2 if r.get("renewed_from") == old_code]
    check("U3a 产生了新记录", len(news) == 1, str(len(news)))
    if news:
        new = news[0]
        new_code = new["code"]
        print(f"  新码: {new_code}")
        info = lic.verify_code(new_code)
        check("U3b ★新码档位 = 20000", info and info["tier"] == 20000,
              str(info and info["tier"]))
        check("U3c 新码可校验（客户端能用）", info is not None)

        # ---------- U4 客户信息沿用 ----------
        print("\n" + "=" * 74)
        print("U4 ★客户信息自动沿用")
        print("=" * 74)
        check("U4a 昵称沿用", new.get("customer") == "朋友小王", new.get("customer"))
        check("U4b 联系方式沿用", new.get("contact") == "微信:xiaowang",
              new.get("contact"))
        check("U4c 渠道沿用", new.get("channel") == "朋友", new.get("channel"))
        check("U4d 记录了原档位", new.get("old_tier_label") == "专业版（2000 条）",
              new.get("old_tier_label"))
        check("U4e 记录了来源", new.get("source") == "web-admin-upgrade",
              new.get("source"))

        # ---------- U6 旧码标记 ----------
        print("\n" + "=" * 74)
        print("U6 旧码标记 superseded（历史链）")
        print("=" * 74)
        sup = [r for r in rows2 if r.get("event") == "superseded"]
        check("U6a 有 superseded 记录", len(sup) == 1, str(len(sup)))
        if sup:
            check("U6b 指向旧码", sup[0]["code"] == old_code)
            check("U6c 记录新码", sup[0]["superseded_by"] == new_code)

        # ---------- U10 历史完整 ----------
        print("\n" + "=" * 74)
        print("U10 台账保留全部历史（append-only）")
        print("=" * 74)
        still = [r for r in rows2 if r.get("code") == old_code
                 and r.get("event") is None]
        check("U10a 旧码原记录仍在", len(still) == 1, str(len(still)))

    # ---------- U7 降级 ----------
    print("\n" + "=" * 74)
    print("U7 降级：20000 → 500")
    print("=" * 74)
    cur = [r for r in rows2 if r.get("renewed_from") == old_code][0]
    post("/upgrade", {"code": cur["code"], "tier": "500",
                      "max_devices": "", "price": "0", "note": "降级测试"})
    rows3 = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines()
             if l.strip()]
    down = [r for r in rows3 if r.get("renewed_from") == cur["code"]]
    check("U7a 降级成功", len(down) == 1)
    if down:
        inf = lic.verify_code(down[0]["code"])
        check("U7b 档位 = 500", inf and inf["tier"] == 500, str(inf and inf["tier"]))

    # ---------- U8 台数能改 ----------
    print("\n" + "=" * 74)
    print("U8 改档位时能设定台数")
    print("=" * 74)
    post("/upgrade", {"code": old_code, "tier": "2000", "max_devices": "3",
                      "price": "0", "note": "加台数"})
    rows4 = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines()
             if l.strip()]
    withmd = [r for r in rows4 if r.get("max_devices") == 3]
    check("U8a 生成了限 3 台的码", len(withmd) >= 1, str(len(withmd)))
    if withmd:
        inf = lic.verify_code(withmd[-1]["code"])
        check("U8b 码内 max_devices=3",
              inf and inf["max_devices"] == 3, str(inf and inf["max_devices"]))

    # ---------- U9 绑定版沿用绑定 ----------
    print("\n" + "=" * 74)
    print("U9 绑定版改档位时沿用原绑定")
    print("=" * 74)
    from sphdl import telemetry as tel
    comps = tel.device_components()
    tok = tel.bind_token(comps)
    post("/gen", {"product": "1", "tier": "500", "customer": "绑定客户",
                  "contact": "闲鱼:bind9", "channel": "闲鱼",
                  "price": "99", "device": tok, "max_devices": ""})
    rows5 = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines()
             if l.strip()]
    bound = [r for r in rows5 if r.get("customer") == "绑定客户"]
    check("U9a 绑定码已生成", len(bound) == 1)
    if bound:
        bc = bound[0]["code"]
        post("/upgrade", {"code": bc, "tier": "20000", "max_devices": "",
                          "price": "0", "note": "绑定版升级"})
        rows6 = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines()
                 if l.strip()]
        up = [r for r in rows6 if r.get("renewed_from") == bc]
        check("U9b 升级成功", len(up) == 1)
        if up:
            inf = lic.verify_code(up[0]["code"])
            check("U9c ★新码仍是绑定版", inf and inf.get("device_bound") is True,
                  str(inf and inf.get("device_bound")))
            check("U9d 新码档位 20000", inf and inf["tier"] == 20000)
            ok, _ = lic.check_device(inf, components=comps)
            check("U9e 本机可通过绑定校验", ok is True)

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
