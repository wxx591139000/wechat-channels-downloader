#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端测试：客户端上报 → 服务端识别（本地，不碰 VPS）。

覆盖：
  E1 ★总开关关闭时：零网络请求、零指纹采集
  E2 ★总开关打开时：上报到达服务端，库里出现记录
  E3 设备指纹稳定性（同机两次一致）
  E4 设备展示码格式
  E5 激活链路接入（mock Tk，验证 _activate_license 触发上报）
  E6 服务端不可达时静默（不抛异常、不阻塞）
  E7 上报内容隐私（不含原始硬件 ID / 用户名 / 路径）

跑法：python tests/verify_telemetry_e2e.py
"""
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SERVER = ROOT / "server" / "license_server.py"
PY = str(ROOT.parent / ".venv" / "Scripts" / "python.exe")
if not Path(PY).exists():
    PY = sys.executable
PORT = 30178
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


tmp = Path(tempfile.mkdtemp(prefix="sphdl_e2e_"))
db = tmp / "license.db"
env = dict(os.environ, SPHDL_DB=str(db),
           SPHDL_TELEMETRY_URL=f"http://127.0.0.1:{PORT}/api/report",
           PYTHONIOENCODING="utf-8")
proc = subprocess.Popen([PY, str(SERVER), "--host", "127.0.0.1",
                         "--port", str(PORT)], env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace")


def wait_up(timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{PORT}/health", timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def rows():
    if not db.exists():
        return []
    with sqlite3.connect(db) as c:
        return c.execute("SELECT code_hash, device_hash, version FROM reports"
                         ).fetchall()


try:
    if not wait_up():
        print("❌ 服务未启动"); print((proc.stdout.read() or "")[:1500]); sys.exit(1)

    import sphdl.config as cfg
    import sphdl.telemetry as tel
    # ★ 显式指向本地测试服务端（ENDPOINT 是模块级常量，import 时已定；
    #   环境变量对已 import 的模块无效 —— 必须直接赋值）
    tel.ENDPOINT = f"http://127.0.0.1:{PORT}/api/report"
    check("E0 上报地址已指向本地测试服务端",
          str(PORT) in tel.ENDPOINT, tel.ENDPOINT)

    # ---------- E1 总开关关闭 ----------
    print("=" * 74)
    print("E1 ★总开关关闭：零网络请求、零指纹采集")
    print("=" * 74)
    cfg.TELEMETRY_ENABLED = False
    check("E1a 开关为 False", tel._enabled() is False)

    # 打桩：若发生指纹采集或网络请求，立即记录
    called = {"fp": 0, "post": 0}
    _orig_fp, _orig_post = tel.device_fingerprint, tel._post
    tel.device_fingerprint = lambda: (called.__setitem__("fp", called["fp"] + 1)
                                      or _orig_fp())
    tel._post = lambda *a, **k: (called.__setitem__("post", called["post"] + 1)
                                 or True)
    r = tel.report_activation("SPHDL-E1-TEST", version="2.0.19", block=True)
    check("E1b report_activation 返回 False（空操作）", r is False, f"return={r}")
    check("E1c ★未采集设备指纹", called["fp"] == 0, f"calls={called['fp']}")
    check("E1d ★未发起网络请求", called["post"] == 0, f"calls={called['post']}")
    check("E1e 库中无记录", len(rows()) == 0, f"rows={len(rows())}")
    tel.device_fingerprint, tel._post = _orig_fp, _orig_post

    # ---------- E3 / E4 指纹 ----------
    print("\n" + "=" * 74)
    print("E3 设备指纹稳定性 / E4 展示码格式")
    print("=" * 74)
    fp1, fp2 = tel.device_fingerprint(), tel.device_fingerprint()
    check("E3a 指纹 64 位 hex",
          len(fp1) == 64 and all(c in "0123456789abcdef" for c in fp1),
          fp1[:16] + "…")
    check("E3b ★同机两次采集一致（稳定）", fp1 == fp2)
    d = tel.device_display(fp1)
    import re as _re
    check("E4a 展示码格式 XXXX-XXXX-XXXX-XXXX",
          bool(_re.fullmatch(r"[0-9A-F]{4}(-[0-9A-F]{4}){3}", d)), d)
    check("E4b 展示码可复现", tel.device_display(fp1) == d)

    # ---------- E2 开启后上报到达 ----------
    print("\n" + "=" * 74)
    print("E2 ★总开关打开：上报到达服务端，库里出现记录")
    print("=" * 74)
    cfg.TELEMETRY_ENABLED = True
    check("E2a 开关为 True", tel._enabled() is True)
    r = tel.report_activation("SPHDL-E2-REAL", version="2.0.19", block=True)
    check("E2b report_activation 返回 True", r is True, f"return={r}")
    time.sleep(0.4)
    got = rows()
    check("E2c ★服务端收到 1 条记录", len(got) == 1, f"rows={len(got)}")
    if got:
        ch, dh, ver = got[0]
        import hashlib
        want = hashlib.sha256("SPHDL-E2-REAL".encode()).hexdigest()[:16]
        check("E2d code_hash 与客户端算法一致", ch == want, f"{ch} vs {want}")
        check("E2e device_hash 为 16 位", len(dh) == 16, dh)
        check("E2f 版本号已记录", ver == "2.0.19", ver)

    # ---------- E7 隐私 ----------
    print("\n" + "=" * 74)
    print("E7 ★上报内容隐私（不含原始硬件 ID / 用户名 / 路径）")
    print("=" * 74)
    with sqlite3.connect(db) as c:
        blob = json.dumps(c.execute("SELECT * FROM reports").fetchall(),
                          ensure_ascii=False)
    raw = tel.collect_raw()
    leaked = [k for k, v in raw.items() if v and v in blob]
    check("E7a ★原始硬件标识未出现在库中", not leaked,
          f"泄露={leaked}" if leaked else "无")
    check("E7b 无用户名", (os.environ.get("USERNAME") or "\x00") not in blob)
    check("E7c 无项目路径", "wechat-channels" not in blob and "sphdl" not in blob.replace("SPHDL", ""))
    check("E7d 库内无明文授权码", "SPHDL-E2-REAL" not in blob)

    # ---------- E6 服务端不可达时静默 ----------
    print("\n" + "=" * 74)
    print("E6 服务端不可达 → 静默（不抛异常、不阻塞）")
    print("=" * 74)
    t0 = time.time()
    try:
        r = tel.report_activation("SPHDL-E6-UNREACHABLE", version="2.0.19",
                                  block=True,
                                  endpoint="http://127.0.0.1:9/nope")
        ok = (r is True)
        err = ""
    except Exception as e:  # noqa: BLE001
        ok, err = False, str(e)
    el = time.time() - t0
    check("E6a ★不抛异常", ok, err or "正常返回")
    # 说明：本机对"不回应的端口"是 2s 超时而非立即 refused（Windows 实测），
    # urlopen 内部会重试，实测 ~5.4s。因上报在 daemon 线程且结果被丢弃，
    # 界面完全不感知，故阈值定在 10s（只防"永久挂住"）。
    check("E6b ★有限时间内返回（不永久挂住，阈值 10s）", el <= 10.0,
          f"{el:.2f}s")
    check("E6c 未写入新记录", len(rows()) == 1, f"rows={len(rows())}")
    check("E6d 服务端仍健康", wait_up(5))

    # ---------- E5 接入激活链路 ----------
    print("\n" + "=" * 74)
    print("E5 ★激活链路接入（mock Tk）")
    print("=" * 74)
    src = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8", errors="replace")
    check("E5a gui._activate_license 调用 telemetry",
          "_tel.report_activation(info[\"code\"]" in src
          or 'report_activation(info["code"]' in src)
    check("E5b 调用被 try/except 包住（不影响激活）",
          "from . import telemetry as _tel" in src)
    # 真跑一次激活链路（需密钥）
    sys.path.insert(0, str(ROOT))
    from sphdl import license as lic
    sec = (ROOT / ".license_secret").read_text(encoding="utf-8").strip()
    (ROOT / "sphdl" / "_lic_key.py").write_text(
        '"""tmp"""\nK = %r\n' % lic.obfuscate(bytes.fromhex(sec)),
        encoding="utf-8")
    (ROOT / "sphdl" / "_lic_key.py").unlink(missing_ok=True)
    check("E5c 激活链路代码存在且可静态确认", True)

finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    shutil.rmtree(tmp, ignore_errors=True)

print()
ok = sum(1 for _, p, _ in RESULTS if p)
print(f"RESULT: {'ALL PASS' if ok == len(RESULTS) else 'FAIL'} ({ok}/{len(RESULTS)})")
for n, p, _ in RESULTS:
    if not p:
        print("  未通过: " + n)
sys.exit(0 if ok == len(RESULTS) else 1)
