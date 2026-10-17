#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设备数上限端到端测试（v2.0.19）。

验证「客户买 3 台，第 4 台激活失败」的完整链路：
  L1 ver=4 码生成与解析
  L2 服务器：第1~3台 allowed=True，第4台 allowed=False
  L3 同一设备重复上报不算新设备
  L4 不限台数码永远 allowed=True
  L5 客户端 check_device_limit 正确解读服务器响应
  L6 服务器不可达 → 放行（不误伤正版客户）

跑法：python tests/verify_device_limit.py
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sphdl import license as lic  # noqa: E402

sec = (ROOT / ".license_secret").read_text(encoding="utf-8").strip()
(ROOT / "sphdl" / "_lic_key.py").write_text(
    '"""t"""\nK = %r\n' % lic.obfuscate(bytes.fromhex(sec)), encoding="utf-8")
import importlib  # noqa: E402
importlib.reload(lic)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


# ---------- L1 ver=4 生成/解析 ----------
print("=" * 74)
print("L1 ver=4 码生成与解析")
print("=" * 74)
c3 = lic.make_code(20000, product=lic.PRODUCT_VIDEO, max_devices=3)
i3 = lic.verify_code(c3)
check("L1a 码可校验", i3 is not None, c3)
check("L1b ver=4", i3 and i3["ver"] == 4, str(i3 and i3["ver"]))
check("L1c max_devices=3", i3 and i3["max_devices"] == 3)
check("L1d device_limited=True", i3 and i3["device_limited"] is True)
check("L1e 码长 40", len(c3) == 40, str(len(c3)))
c_free = lic.make_code(20000, product=lic.PRODUCT_VIDEO)
i_free = lic.verify_code(c_free)
check("L1f 不限台数走 ver=2", i_free["ver"] == 2 and not i_free["device_limited"])

# ---------- 起服务端 ----------
print("\n" + "=" * 74)
print("启动测试用服务端")
print("=" * 74)
tdir = Path(tempfile.mkdtemp(prefix="lim_"))
srv = tdir / "license_server.py"
shutil.copy2(ROOT / "server" / "license_server.py", srv)
db = tdir / "data" / "license.db"
db.parent.mkdir(parents=True, exist_ok=True)
PORT = 3088
env = dict(os.environ)
env["SPHDL_ADMIN_TOKEN"] = "test-token-lim"
env["PYTHONIOENCODING"] = "utf-8"
proc = subprocess.Popen(
    [sys.executable, str(srv), "--port", str(PORT)],
    cwd=str(tdir), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
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
    print("服务未启动")
    proc.kill()
    print(proc.stdout.read()[:1500] if proc.stdout else "")
    (ROOT / "sphdl" / "_lic_key.py").unlink(missing_ok=True)
    sys.exit(1)
print(f"  服务端已启动 :{PORT}")


def report(code, device, max_devices=None):
    p = {"code": code, "device": device, "version": "2.0.19"}
    if max_devices:
        p["max_devices"] = max_devices
    req = urllib.request.Request(
        base + "/api/report", data=json.dumps(p).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read().decode())


try:
    # ---------- L2 核心：3 台允许，第 4 台拒绝 ----------
    print("\n" + "=" * 74)
    print("L2 ★核心：买 3 台，第 4 台激活失败")
    print("=" * 74)
    for i in (1, 2, 3):
        r = report(c3, f"DEVICE-{i}", max_devices=3)
        check(f"L2 第{i}台 allowed=True", r.get("allowed") is True,
              f"已用 {r.get('devices')}/3 剩余 {r.get('remaining')}")
    r4 = report(c3, "DEVICE-4", max_devices=3)
    check("L2 第4台 ★allowed=False（激活失败）", r4.get("allowed") is False,
          f"已用 {r4.get('devices')}/3")
    check("L2 第4台 devices=4", r4.get("devices") == 4, str(r4.get("devices")))
    check("L2 第4台 remaining=0", r4.get("remaining") == 0, str(r4.get("remaining")))

    # ---------- L3 同设备重复不算新设备 ----------
    print("\n" + "=" * 74)
    print("L3 同一设备重复上报不算新设备")
    print("=" * 74)
    r = report(c3, "DEVICE-1", max_devices=3)
    check("L3a 仍是 4 台（不增加）", r.get("devices") == 4, str(r.get("devices")))
    check("L3b first=False（已见过）", r.get("first") is False)

    # ---------- L4 不限台数 ----------
    print("\n" + "=" * 74)
    print("L4 不限台数码永远放行")
    print("=" * 74)
    allok = True
    for i in range(1, 8):
        r = report(c_free, f"FREE-{i}")
        if r.get("allowed") is not True:
            allok = False
    check("L4 7 台设备全部 allowed=True", allok)

    # ---------- L5 客户端解读服务器响应 ----------
    print("\n" + "=" * 74)
    print("L5 客户端 check_device_limit 解读响应")
    print("=" * 74)
    from sphdl import telemetry as tel
    importlib.reload(tel)
    tel.ENDPOINT = base + "/api/report"
    tel._COMPS_CACHE["v"] = None

    # 已满的码 + 新设备 → 拒绝
    ok, msg, info = tel.check_device_limit(c3, 3)
    check("L5a ★已超限 → 客户端拒绝", ok is False, msg.split("\n")[0][:44])
    # 提示里必须同时出现「允许台数」和「实际已用台数」，客户才知道情况
    import re as _re
    _nums = _re.findall(r"(\d+)\s*台", msg)
    check("L5b 提示含台数与已用数", len(_nums) >= 2,
          f"提取到 {_nums}")

    # 不限台数 → 不查，直接放行
    ok2, _, _ = tel.check_device_limit(c_free, None)
    check("L5c 不限台数 → 直接放行", ok2 is True)

    # ---------- L6 服务器不可达 → 放行 ----------
    print("\n" + "=" * 74)
    print("L6 服务器不可达 → 放行（不误伤正版客户）")
    print("=" * 74)
    tel.ENDPOINT = "http://127.0.0.1:9/api/report"   # 不可达端口
    t0 = time.time()
    ok3, _, _ = tel.check_device_limit(c3, 3)
    dt = time.time() - t0
    check("L6a ★不可达时放行", ok3 is True)
    check("L6b 不会卡太久（<10s）", dt < 10, f"{dt:.1f}s")

    # ---------- L7 数据库落盘 ----------
    print("\n" + "=" * 74)
    print("L7 上报数据已落盘（可追溯）")
    print("=" * 74)
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    n = c.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
    c.close()
    check("L7a 数据库有记录", n > 0, f"{n} 条")

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
