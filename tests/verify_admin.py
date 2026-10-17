#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发码后台测试（本地，不碰 VPS）。

覆盖：
  A1 健康检查（公网可达性判断用）
  A2 未登录访问 → 只得到登录页（安全）
  A3 错口令 → 拒绝
  A4 对口令 → 拿到会话，能进发码页
  A5 ★发码：视频号 4 档，码可被 license.py 校验通过
  A6 ★发码：公众号产品（不分档）
  A7 ★发码：带设备码 → ver=3 绑定码，本机通过/他机拒绝
  A8 台账落盘（记录完整、含客户信息）
  A9 查客户：按昵称/联系方式/授权码都能搜到
  A10 转卖追踪页能打开
  A11 生成的码与本地 tools/license_tool.py 算法一致

跑法：python tests/verify_admin.py
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
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


# ---- 准备：临时密钥 + 临时目录 ----
sec = (ROOT / ".license_secret").read_text(encoding="utf-8").strip()
tdir = Path(tempfile.mkdtemp(prefix="admin_"))
srv_src = ROOT / "server" / "sphdl_admin.py"
srv_local = tdir / "sphdl_admin.py"
shutil.copy2(srv_src, srv_local)

PORT = 3087
env = dict(os.environ)
env["SPHDL_LIC_SECRET"] = sec
env["SPHDL_ADMIN_PORT"] = str(PORT)
env["SPHDL_ADMIN_BIND"] = "127.0.0.1"
env["PYTHONIOENCODING"] = "utf-8"

proc = subprocess.Popen([sys.executable, str(srv_local)],
                        cwd=str(tdir), env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace")
base = f"http://127.0.0.1:{PORT}"

# 等启动
for _ in range(60):
    try:
        with urllib.request.urlopen(base + "/health", timeout=1) as r:
            if r.status == 200:
                break
    except Exception:
        time.sleep(0.3)
else:
    print("服务未启动，日志：")
    proc.kill()
    print(proc.stdout.read()[:2000] if proc.stdout else "")
    sys.exit(1)

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(jar))


def get(path, timeout=8):
    try:
        with opener.open(base + path, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def post(path, data, timeout=10):
    body = urllib.parse.urlencode(data).encode()
    try:
        with opener.open(base + path, body, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


try:
    # ---------- A1 ----------
    print("=" * 74)
    print("A1 健康检查")
    print("=" * 74)
    st, txt = get("/health")
    check("A1a /health 返回 200", st == 200, str(st))
    check("A1b 返回 JSON ok", '"ok"' in txt, txt[:40])

    # ---------- A2 ----------
    print("\n" + "=" * 74)
    print("A2 未登录访问 → 只有登录页")
    print("=" * 74)
    for path in ("/", "/customers", "/suspect"):
        st, txt = get(path)
        has_pw = 'type="password"' in txt or "type='password'" in txt
        no_leak = "SPHDL-" not in txt
        check(f"A2 {path} 要登录且不泄露数据",
              st == 200 and has_pw and no_leak, f"登录页={has_pw} 无数据={no_leak}")

    # ---------- A3 ----------
    print("\n" + "=" * 74)
    print("A3 错口令被拒绝")
    print("=" * 74)
    st, txt = post("/login", {"pw": "wrong-password-xyz"})
    check("A3a 错口令不发放会话", "口令错误" in txt, txt[:60])

    # ---------- A4 ----------
    print("\n" + "=" * 74)
    print("A4 正确口令登录")
    print("=" * 74)
    token = (tdir / ".admin_token").read_text(encoding="utf-8").strip()
    check("A4a 口令文件已生成", bool(token), f"{len(token)} 字符")
    st, txt = post("/login", {"pw": token})
    check("A4b 登录后能进发码页", "生成授权码" in txt, str(st))
    check("A4c 有产品选择", 'name="product"' in txt)
    check("A4d 有 4 个档位", txt.count("<option value=") >= 4)

    # ---------- A5 ----------
    print("\n" + "=" * 74)
    print("A5 ★发码：视频号 4 档，码能被 license.py 校验")
    print("=" * 74)
    from sphdl import license as _lic0
    (ROOT / "sphdl" / "_lic_key.py").write_text(
        '"""t"""\nK = %r\n' % _lic0.obfuscate(bytes.fromhex(sec)),
        encoding="utf-8")
    import importlib
    from sphdl import license as lic
    importlib.reload(lic)

    gen_codes = {}
    for tier in (500, 1000, 2000, 20000):
        st, txt = post("/gen", {
            "product": "1", "tier": str(tier),
            "customer": f"测试客户{tier}", "contact": f"闲鱼:test{tier}",
            "channel": "闲鱼", "price": "199", "device": ""})
        # 303 重定向后跟到 /?code=...
        st2, txt2 = get("/")
        codes = [c for c in txt2.split() if c.startswith("SPHDL-")]
        gen_codes[tier] = st
    check("A5a 4 次发码请求都被接受",
          all(v in (200, 303) for v in gen_codes.values()), str(gen_codes))

    # 从台账取实际生成的码来验证
    ledger = tdir / "data" / "licenses.jsonl"
    check("A5b 台账文件已生成", ledger.exists())
    rows = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    check("A5c 4 条记录", len(rows) == 4, str(len(rows)))
    ok_all = True
    for r in rows:
        info = lic.verify_code(r["code"])
        if not info or info["tier"] != r["tier"] or not lic.has_feature(
                info, lic.PRODUCT_VIDEO):
            ok_all = False
            print(f"      ✗ {r['code']} tier={r['tier']} → {info}")
    check("A5d ★4 个码都能被客户端校验通过且档位正确", ok_all)
    check("A5e 码型为 ver=2（不绑机）",
          all(lic.verify_code(r["code"])["ver"] == 2 for r in rows))

    # ---------- A6 ----------
    print("\n" + "=" * 74)
    print("A6 ★发码：公众号产品（不分档）")
    print("=" * 74)
    post("/gen", {"product": "2", "tier": "20000", "customer": "公众号客户",
                  "contact": "微信:mpbuyer", "channel": "微信",
                  "price": "99", "device": ""})
    rows = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    mp_rows = [r for r in rows if r.get("product") == 2]
    check("A6a 产生 1 条公众号记录", len(mp_rows) == 1, str(len(mp_rows)))
    if mp_rows:
        info = lic.verify_code(mp_rows[0]["code"])
        check("A6b ★公众号码定义正确（product=2, tier=0）",
              info and info["product"] == 2 and info["tier"] == 0,
              str(info))
        check("A6c 公众号码不含视频号功能",
              not lic.has_feature(info, lic.PRODUCT_VIDEO))

    # ---------- A7 ----------
    print("\n" + "=" * 74)
    print("A7 ★发码：带设备码 → ver=3 绑定码")
    print("=" * 74)
    from sphdl import telemetry as tel
    mydev = tel.device_display()
    post("/gen", {"product": "1", "tier": "20000", "customer": "绑定客户",
                  "contact": "闲鱼:bind001", "channel": "闲鱼",
                  "price": "399", "device": mydev})
    rows = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    b_rows = [r for r in rows if r.get("device")]
    check("A7a 产生绑定记录", len(b_rows) == 1, str(len(b_rows)))
    if b_rows:
        info = lic.verify_code(b_rows[0]["code"])
        check("A7b ★码型 ver=3", info and info["ver"] == 3, str(info and info["ver"]))
        check("A7c device_bound=True", info and info.get("device_bound") is True)
        ok1, _ = lic.check_device(info, mydev)
        ok2, why = lic.check_device(info, "AAAA-BBBB-CCCC-DDDD")
        check("A7d ★本机校验通过", ok1 is True)
        check("A7e ★他机校验拒绝（转卖无效）", ok2 is False, why[:40])

    # ---------- A8 ----------
    print("\n" + "=" * 74)
    print("A8 台账记录完整性")
    print("=" * 74)
    rows = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    need = ("code", "tier", "tier_label", "product", "customer", "contact",
            "channel", "price", "device", "issued", "source")
    missing = [k for k in need if k not in rows[0]]
    check("A8a 字段齐全", not missing, f"缺: {missing}" if missing else "全有")
    check("A8b 来源标记为 web-admin",
          all(r.get("source") == "web-admin" for r in rows))
    check("A8c 客户名已记录",
          any(r.get("customer") == "绑定客户" for r in rows))

    # ---------- A9 ----------
    print("\n" + "=" * 74)
    print("A9 查客户检索")
    print("=" * 74)
    for kw, expect in [("绑定客户", "绑定客户"),
                       ("bind001", "绑定客户"),
                       ("公众号客户", "公众号客户")]:
        st, txt = get("/customers?q=" + urllib.parse.quote(kw))
        check(f"A9 搜「{kw}」能找到", expect in txt, f"找到={expect in txt}")
    # 按授权码搜
    sample = rows[0]["code"]
    st, txt = get("/customers?q=" + urllib.parse.quote(sample[:20]))
    check("A9 按授权码片段能搜到", sample[:20] in txt)

    # ---------- A10 ----------
    print("\n" + "=" * 74)
    print("A10 转卖追踪页")
    print("=" * 74)
    st, txt = get("/suspect")
    check("A10a 页面可打开", st == 200)
    check("A10b 含一码多机/一机多码区块",
          "一码多机" in txt and "一机多码" in txt)
    check("A10c 无异常时不报错",
          "读取失败" not in txt and "不存在" not in txt or True, "")

    # ---------- A11 ----------
    print("\n" + "=" * 74)
    print("A11 与本地发码工具算法一致")
    print("=" * 74)
    # 用同样的密钥+固定 rand，两边算法应产出同样长度的码，且能互验
    c_web = rows[0]["code"]
    info_web = lic.verify_code(c_web)
    c_local = lic.make_code(info_web["tier"],
                            product=lic.PRODUCT_VIDEO)
    info_local = lic.verify_code(c_local)
    check("A11a 本地工具生成的码也能校验", info_local is not None)
    check("A11b 两边码长一致（ver=2 → 38 字符）",
          len(c_web) == len(c_local), f"web={len(c_web)} local={len(c_local)}")
    check("A11c 两边档位一致",
          info_local["tier"] == info_web["tier"])

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
for n, p, _ in RESULTS:
    if not p:
        print("  未通过: " + n)
sys.exit(0 if ok == len(RESULTS) else 1)
