#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""授权上报服务端测试（本地，不碰 VPS）。

覆盖：
  S1 健康检查
  S2 上报写入 + 首次/重复识别
  S3 ★一码多机识别（转卖核心场景）
  S4 ★一机多码识别（收集码场景）
  S5 隐私最小化：存的是哈希不是明文；IP 只存 /24
  S6 管理接口鉴权（无令牌 403，有令牌 200）
  S7 容错：坏 JSON / 超长 body / 缺字段 → 一律 200，不炸
  S8 重复上报累加计数，不产生重复行

跑法：python tests/verify_license_server.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server" / "license_server.py"
PY = str(ROOT.parent / ".venv" / "Scripts" / "python.exe")
if not Path(PY).exists():
    PY = sys.executable

RESULTS = []
PORT = 30177          # 本地测试用非标准端口，避免冲突
TOKEN = "test-token-123"


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


def post(path, obj, raw=None):
    data = raw if raw is not None else json.dumps(obj).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data,
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")
    except Exception as e:  # noqa: BLE001
        return 0, {"err": str(e)}


def get(path, token=None):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}")
    if token:
        req.add_header("X-Auth", token)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")
    except Exception as e:  # noqa: BLE001
        return 0, {"err": str(e)}


tmp = Path(tempfile.mkdtemp(prefix="sphdl_srv_"))
db = tmp / "license.db"
env = dict(os.environ, SPHDL_DB=str(db), SPHDL_ADMIN_TOKEN=TOKEN,
           PYTHONIOENCODING="utf-8")
proc = subprocess.Popen([PY, str(SERVER), "--host", "127.0.0.1",
                         "--port", str(PORT)], env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace")

try:
    # 等服务起来
    up = False
    for _ in range(60):
        st, _b = get("/health")
        if st == 200:
            up = True
            break
        time.sleep(0.25)
    if not up:
        print("❌ 服务未能在 15s 内启动")
        print((proc.stdout.read() if proc.stdout else "")[:2000])
        sys.exit(1)

    # ---------- S1 ----------
    print("=" * 74)
    print("S1 健康检查")
    print("=" * 74)
    st, b = get("/health")
    check("S1a /health 返回 200", st == 200, f"status={st}")
    check("S1b 服务标识正确", b.get("service") == "sphdl-license", str(b))

    # ---------- S2 ----------
    print("\n" + "=" * 74)
    print("S2 上报写入 + 首次/重复识别")
    print("=" * 74)
    st, b = post("/api/report", {"code": "SPHDL-TEST-0001", "device": "DEV-AAAA",
                                 "version": "2.0.19", "ts": int(time.time())})
    check("S2a 上报返回 200", st == 200, f"status={st}")
    check("S2b 首次上报 first=True", b.get("first") is True, str(b))
    check("S2c 该码当前设备数=1", b.get("devices") == 1, str(b))

    st, b = post("/api/report", {"code": "SPHDL-TEST-0001", "device": "DEV-AAAA",
                                 "version": "2.0.19"})
    check("S2d 重复上报 first=False", b.get("first") is False, str(b))
    check("S2e 同机重复不增加设备数", b.get("devices") == 1, str(b))

    # ---------- S3 一码多机 ----------
    print("\n" + "=" * 74)
    print("S3 ★一码多机识别（转卖核心场景）")
    print("=" * 74)
    st, b = post("/api/report", {"code": "SPHDL-TEST-0001", "device": "DEV-BBBB"})
    check("S3a 第二台设备上报后 devices=2", b.get("devices") == 2, str(b))
    st, b = post("/api/report", {"code": "SPHDL-TEST-0001", "device": "DEV-CCCC"})
    check("S3b 第三台设备上报后 devices=3", b.get("devices") == 3, str(b))

    st, b = get("/api/suspect", TOKEN)
    susp = b.get("one_code_many_devices", [])
    hit = [x for x in susp if x.get("devices") == 3]
    check("S3c ★可疑清单标记出「一码三机」", len(hit) == 1, f"命中 {len(hit)} 条")
    check("S3d 记录含设备数 3", hit and hit[0].get("devices") == 3,
          str(hit[0] if hit else {}))

    # ---------- S4 一机多码 ----------
    print("\n" + "=" * 74)
    print("S4 ★一机多码识别（有人在收集码）")
    print("=" * 74)
    post("/api/report", {"code": "SPHDL-TEST-0002", "device": "DEV-XXXX"})
    post("/api/report", {"code": "SPHDL-TEST-0003", "device": "DEV-XXXX"})
    st, b = get("/api/suspect", TOKEN)
    mc = b.get("one_device_many_codes", [])
    hit2 = [x for x in mc if x.get("codes") == 2]
    check("S4a ★可疑清单标记出「一机两码」", len(hit2) >= 1, f"命中 {len(hit2)} 条")

    # ---------- S5 隐私最小化 ----------
    print("\n" + "=" * 74)
    print("S5 ★隐私最小化（PIPL 合规）")
    print("=" * 74)
    import sqlite3
    with sqlite3.connect(db) as c:
        rows = c.execute("SELECT * FROM reports").fetchall()
        cols = [d[0] for d in c.execute("SELECT * FROM reports LIMIT 1").description]
    blob = json.dumps(rows, ensure_ascii=False)
    check("S5a ★库内无明文授权码", "SPHDL-TEST-0001" not in blob)
    check("S5b ★库内无明文设备码", "DEV-AAAA" not in blob)
    check("S5c code_hash 为 16 位十六进制",
          all(len(r[1]) == 16 for r in rows), f"sample={rows[0][1] if rows else ''}")
    check("S5d 无 IP 明文列（只有 ip24）",
          "ip24" in cols and not any(c in cols for c in ["ip", "client_ip",
                                                         "remote_addr"]),
          f"cols={cols}")
    check("S5e 无 User-Agent 列", not any("agent" in c.lower() for c in cols),
          f"cols={cols}")
    check("S5f ip24 已截断（不含完整 IPv4）",
          all(("." not in r[4]) or r[4].endswith(".x") or "::" in r[4]
              for r in rows if r[4]),
          f"sample={[r[4] for r in rows[:2]]}")
    check("S5g 无邮箱/手机号字段", not any(
        k in c.lower() for c in cols for k in ["email", "phone", "mobile"]))

    # ---------- S6 鉴权 ----------
    print("\n" + "=" * 74)
    print("S6 管理接口鉴权")
    print("=" * 74)
    st, _ = get("/api/stats")
    check("S6a 无令牌访问 /api/stats → 403", st == 403, f"status={st}")
    st, _ = get("/api/suspect")
    check("S6b 无令牌访问 /api/suspect → 403", st == 403, f"status={st}")
    st, _ = get("/api/stats", "wrong-token")
    check("S6c 错令牌 → 403", st == 403, f"status={st}")
    st, b = get("/api/stats", TOKEN)
    check("S6d 正确令牌 → 200", st == 200, f"status={st}")
    check("S6e 统计字段完整",
          all(k in b for k in ["total_reports", "distinct_codes",
                               "distinct_devices", "codes_multi_device"]),
          str(b))
    check("S6f /health 无需鉴权", get("/health")[0] == 200)

    # ---------- S7 容错 ----------
    print("\n" + "=" * 74)
    print("S7 ★容错：任何异常都返回 200（绝不阻断客户端）")
    print("=" * 74)
    st, b = post("/api/report", None, raw=b"not json at all")
    check("S7a 坏 JSON → 200 且 ok=False", st == 200 and b.get("ok") is False, str(b))
    st, b = post("/api/report", {})
    check("S7b 缺 code 字段 → 200", st == 200, str(b))
    st, b = post("/api/report", {"code": "X"}, raw=b"x" * 5000)
    check("S7c 超长 body → 200（不炸）", st == 200, str(b))
    st, b = post("/api/nonexistent", {})
    check("S7d 未知路径 POST → 404（明确）", st == 404, f"status={st}")
    st, b = get("/api/nonexistent")
    check("S7e 未知路径 GET → 404", st == 404, f"status={st}")
    # 服务仍然存活
    check("S7f 上述异常后服务仍健康", get("/health")[0] == 200)

    # ---------- S8 计数累加 ----------
    print("\n" + "=" * 74)
    print("S8 重复上报累加计数，不产生重复行")
    print("=" * 74)
    for _ in range(5):
        post("/api/report", {"code": "SPHDL-CNT", "device": "DEV-CNT"})
    with sqlite3.connect(db) as c:
        n = c.execute("SELECT COUNT(*) FROM reports WHERE code_hash=?",
                      (__import__("hashlib").sha256(
                          "SPHDL-CNT".encode()).hexdigest()[:16],)).fetchone()[0]
        cnt = c.execute("SELECT seen_count FROM reports WHERE code_hash=?",
                        (__import__("hashlib").sha256(
                            "SPHDL-CNT".encode()).hexdigest()[:16],)).fetchone()[0]
    check("S8a 只有 1 行（不重复插入）", n == 1, f"rows={n}")
    check("S8b seen_count 累加到 5", cnt == 5, f"count={cnt}")

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
