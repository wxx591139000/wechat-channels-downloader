#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查客户页显示实际激活设备（v2.1.9）。

用户报障（2026-10-19）：
  「当前在发码平台里查客户，为何看不到激活用户的设备码？」

根因：两套数据源各查各的
  · 查客户页读 licenses.jsonl（device 字段是发码时手填的，基本留空）
  · 客户实际激活的设备在 license.db（上报库）
  → 从不关联，所以什么都不显示

修复：_devices_of_code() 用 h16(码) 去上报库查设备，页面显示"已激活 N/M 台" + 明细。

覆盖：
  D1 h16() 与 license_server.h16() 算法一致（关键：不一致就永远查不到）
  D2 有上报时 → 页面显示设备码
  D3 显示「已激活 1/1 台」标签
  D4 超过台数时显示红色告警
  D5 无上报时 → 显示"尚未激活"
  D6 ★同一设备重复上报不重复计数
  D7 上报库不存在时 → 页面不报错（返回空列表）
  D8 管理页也显示设备信息
"""
import importlib.util
import json
import sqlite3
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(r"E:/myHermesWorkSpace/wechat-channels-downloader/sphdl")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

from sphdl import license as lic
sec = (ROOT / ".license_secret").read_text(encoding="utf-8").strip()
(ROOT / "sphdl" / "_lic_key.py").write_text(
    '"""t"""\nK = %r\n' % lic.obfuscate(bytes.fromhex(sec)), encoding="utf-8")
import importlib
importlib.reload(lic)

import license_server as LSV
import sphdl_admin as ADM
importlib.reload(ADM)

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


tdir = Path(tempfile.mkdtemp(prefix="devshow_"))
TOKEN = "test-token-for-device-display"
ADM.LEDGER = tdir / "licenses.jsonl"
ADM.TOKEN_FILE = tdir / ".admin_token"
ADM.TOKEN_FILE.write_text(TOKEN, encoding="utf-8")
ADM.LIC_DB = tdir / "license.db"
LIC_DB = ADM.LIC_DB

# ---------- D1 h16 一致性 ----------
print("=" * 74)
print("D1 h16() 必须与 license_server 一致（不一致就永远查不到上报）")
print("=" * 74)
for s in ("SPHDL-0G1G-0081-TEST-AAAA-BBBB-CCCC-DD",
          "sphdl-0g1g-0081-test-aaaa-bbbb-cccc-dd",   # 小写
          "  SPHDL-0G1G-0081-TEST-AAAA-BBBB-CCCC-DD  "):  # 带空格
    a, b = ADM.h16(s), LSV.h16(s)
    print(f"  {s[:30]:<34} admin={a} server={b}")
    check(f"D1 h16 一致: {s[:16]}…", a == b)

# ---------- 造上报数据 ----------
print()
print("=" * 74)
print("D2~D6 造上报数据，验证页面显示")
print("=" * 74)
LSV.DB_PATH = LIC_DB
LSV.db_init()

CODE1 = lic.make_code(2000, product=lic.PRODUCT_VIDEO, max_devices=1)
CODE2 = lic.make_code(1000, product=lic.PRODUCT_VIDEO)          # 不限台
CODE3 = lic.make_code(500, product=lic.PRODUCT_VIDEO)           # 未激活

# 台账
for code, cust, md, tier in ((CODE1, "客户甲", 1, 2000),
                             (CODE2, "客户乙", None, 1000),
                             (CODE3, "客户丙", None, 500)):
    ADM.ledger_add({"code": code, "customer": cust, "tier": tier,
                    "tier_label": lic.label_of_tier(tier), "max_devices": md,
                    "issued": "2026-10-19 10:00:00", "channel": "闲鱼",
                    "contact": "wx:test", "price": "99"})

# 客户甲：1 台设备
LSV.record(CODE1, "DEVAAAAA-1111-2222-3333", "2.1.8", "1.1.1.1", max_devices=1)
# 客户乙：3 台设备（不限台，应全显示）
for i in range(1, 4):
    LSV.record(CODE2, f"DEVBBBBB-000{i}-2222-3333", "2.1.8", "2.2.2.2")
# 客户甲同一设备重复上报 3 次
for _ in range(3):
    LSV.record(CODE1, "DEVAAAAA-1111-2222-3333", "2.1.8", "1.1.1.1", max_devices=1)

print(f"  客户甲 {CODE1[:24]}… → 1 台设备（重复上报 4 次）")
print(f"  客户乙 {CODE2[:24]}… → 3 台设备（不限台）")
print(f"  客户丙 {CODE3[:24]}… → 未激活")

# 直接查函数
d1 = ADM._devices_of_code(CODE1)
d2 = ADM._devices_of_code(CODE2)
d3 = ADM._devices_of_code(CODE3)
print()
print(f"  _devices_of_code(客户甲) = {len(d1)} 台  {[x['device_hash'] for x in d1]}")
print(f"  _devices_of_code(客户乙) = {len(d2)} 台")
print(f"  _devices_of_code(客户丙) = {len(d3)} 台")
check("D2 客户甲查到 1 台设备", len(d1) == 1, str(len(d1)))
check("D6 ★同一设备重复上报不重复计数", len(d1) == 1,
      f"上报了 4 次但只有 {len(d1)} 台设备")
check("D2b 客户乙查到 3 台设备", len(d2) == 3, str(len(d2)))
check("D5 未激活的客户查到 0 台", len(d3) == 0)

# ---------- 起服务测页面 ----------
print()
print("=" * 74)
print("D3~D4/D8 页面渲染验证")
print("=" * 74)
import threading
from http.server import ThreadingHTTPServer

ADM.PORT = 3199
srv = ThreadingHTTPServer(("127.0.0.1", ADM.PORT), ADM.H)
th = threading.Thread(target=srv.serve_forever, daemon=True)
th.start()
time.sleep(0.5)

BASE = f"http://127.0.0.1:{ADM.PORT}"


def get(path):
    req = urllib.request.Request(BASE + path)
    req.add_header("Cookie", f"sid={SID}")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read().decode("utf-8", errors="replace")


# 登录
req = urllib.request.Request(BASE + "/login",
                             data=urllib.parse.urlencode({"pw": TOKEN}).encode(),
                             method="POST")
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
with opener.open(req, timeout=10) as r:
    pass
# 取 cookie
import http.cookiejar
SID = ""
for c in opener.handlers:
    if isinstance(c, urllib.request.HTTPCookieProcessor):
        for ck in c.cookiejar:
            if ck.name == "sid":
                SID = ck.value
print(f"  登录 SID: {SID[:12]}…")
check("D0 登录成功", bool(SID))

st, html_txt = get("/customers")
print(f"  /customers → HTTP {st}, {len(html_txt)} 字符")
check("D3a 页面含「已激活 1/1 台」", "已激活 1/1 台" in html_txt)
check("D3b 页面含客户甲的设备码", "DEVAAAAA-1111-2222-3333" in html_txt
      or ADM.h16("DEVAAAAA-1111-2222-3333")[:8] in html_txt)
check("D3c 页面含「已激活 3 台」（客户乙不限台）", "已激活 3 台" in html_txt)
check("D5b 未激活客户显示「尚未激活」", "尚未激活" in html_txt)

# 管理页
from urllib.parse import quote
st2, html2 = get(f"/upgrade?code={quote(CODE2)}")
print(f"  /upgrade → HTTP {st2}, {len(html2)} 字符")
check("D8a 管理页显示「实际激活设备（3 台）」", "实际激活设备（3 台）" in html2)
check("D8b 管理页含设备明细", "└" in html2)

# ---------- D4 超限告警 ----------
print()
print("=" * 74)
print("D4 超出台数时显示告警")
print("=" * 74)
CODE4 = lic.make_code(2000, product=lic.PRODUCT_VIDEO, max_devices=1)
ADM.ledger_add({"code": CODE4, "customer": "超额客户", "tier": 2000,
                "tier_label": lic.label_of_tier(2000), "max_devices": 1,
                "issued": "2026-10-19 11:00:00"})
for i in range(3):
    LSV.record(CODE4, f"DEVCCCCC-000{i}-2222-3333", "2.1.8", "3.3.3.3",
               max_devices=1)
st3, html3 = get(f"/customers?q={quote('超额客户')}")
print(f"  已激活 3 台 / 允许 1 台")
check("D4a 页面显示「已激活 3/1 台」", "已激活 3/1 台" in html3)
st4, html4 = get(f"/upgrade?code={quote(CODE4)}")
check("D4b 管理页显示超限告警", "超过允许的 1 台" in html4,
      "红色告警")

# ---------- D7 上报库不存在 ----------
print()
print("=" * 74)
print("D7 上报库不存在时不能报错")
print("=" * 74)
ADM.LIC_DB = tdir / "not_exist.db"
r = ADM._devices_of_code(CODE1)
check("D7a 库不存在 → 返回空列表不抛异常", r == [], str(r))
st5, html5 = get("/customers")
check("D7b 页面仍正常渲染（不整页报错）", st5 == 200 and "查客户" in html5,
      f"HTTP {st5}")

srv.shutdown()

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
(ROOT / "sphdl" / "_lic_key.py").unlink(missing_ok=True)
sys.exit(0 if ok_n == len(R) else 1)
