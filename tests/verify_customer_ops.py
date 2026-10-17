#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""客户名编辑 + 作废/恢复测试 —— v2.1.5。

用户要求：
  · 查用户时，用户名称要可以编辑
  · 改档位的按钮处还要支持把码作废
  · 作废后还能恢复

覆盖：
  E1 ★改客户名 → 查客户页显示新名
  E2 ★改联系方式/渠道
  E3 改名后升级，新码沿用新名（不是旧名）
  E4 ★作废 → 状态变为已作废 + 查客户页有标记
  E5 ★恢复 → 状态回到正常
  E6 作废/恢复可反复切换（最后一次生效）
  E7 作废不影响台账原有记录（append-only）
  E8 作废的码仍可被客户端校验（符合"只记账不拦截"的设计）
  E9 事件行不会出现在查客户列表里（不污染）
  E10 改档位后改名，历史链仍完整

跑法：python tests/verify_customer_ops.py
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


tdir = Path(tempfile.mkdtemp(prefix="cops_"))
srv = tdir / "sphdl_admin.py"
shutil.copy2(ROOT / "server" / "sphdl_admin.py", srv)
PORT = 3085
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

    # ---------- 准备 ----------
    post("/gen", {"product": "1", "tier": "2000", "customer": "老名字",
                  "contact": "闲鱼:old", "channel": "闲鱼",
                  "price": "88", "device": "", "max_devices": "1"})
    code = rows()[-1]["code"]
    print(f"准备：客户「老名字」 code={code}")

    # ---------- E1 改客户名 ----------
    print("\n" + "=" * 74)
    print("E1 ★改客户名")
    print("=" * 74)
    st, _ = post("/edit_customer", {"code": code, "customer": "新名字",
                                    "contact": "闲鱼:new", "channel": "微信"})
    r2 = rows()
    edits = [r for r in r2 if r.get("event") == "customer_edit"]
    check("E1a 产生了编辑事件", len(edits) == 1, str(len(edits)))
    check("E1b 事件里是新名字", edits and edits[0]["customer"] == "新名字")
    st, txt = get("/customers?q=" + urllib.parse.quote("新名字"))
    check("E1c ★查客户页显示新名字", "新名字" in txt)
    st, txt2 = get("/customers?q=" + urllib.parse.quote("老名字"))
    # ⚠️ 注意：搜索框会回显搜索词，所以不能直接判断"老名字"是否出现。
    #    要看的是「结果条目里」有没有旧名 —— 用 class="item" 区块判断。
    n_items_old = txt2.count('class="item"')
    check("E1d 用旧名字搜不到任何客户", n_items_old == 0,
          f"搜到 {n_items_old} 条")

    # ---------- E2 联系方式/渠道 ----------
    print("\n" + "=" * 74)
    print("E2 ★改联系方式与渠道")
    print("=" * 74)
    st, txt = get("/customers?q=" + urllib.parse.quote("新名字"))
    check("E2a 显示新联系方式", "闲鱼:new" in txt)
    check("E2b 显示新渠道", "微信" in txt)
    check("E2c 不显示旧联系方式", "闲鱼:old" not in txt)

    # ---------- E7 原有记录不变 ----------
    print("\n" + "=" * 74)
    print("E7 台账原记录不变（append-only）")
    print("=" * 74)
    orig = [r for r in r2 if r.get("code") == code and r.get("event") is None]
    check("E7a 原记录仍存在", len(orig) == 1)
    check("E7b 原记录的客户名未改（历史留痕）",
          orig and orig[0].get("customer") == "老名字",
          orig[0].get("customer") if orig else "-")

    # ---------- E3 改名后升级沿用新名 ----------
    print("\n" + "=" * 74)
    print("E3 改名后升级，新码沿用新名字")
    print("=" * 74)
    post("/upgrade", {"code": code, "tier": "20000", "max_devices": "1",
                      "price": "100", "note": "升级"})
    r3 = rows()
    news = [r for r in r3 if r.get("renewed_from") == code]
    check("E3a 生成新码", len(news) == 1)
    check("E3b ★新码客户名 = 新名字",
          news and news[0].get("customer") == "新名字",
          news[0].get("customer") if news else "-")
    check("E3c 新码联系方式也是新的",
          news and news[0].get("contact") == "闲鱼:new")

    # ---------- E4 作废 ----------
    print("\n" + "=" * 74)
    print("E4 ★作废授权码")
    print("=" * 74)
    post("/void", {"code": code})
    st, txt = get("/upgrade?code=" + urllib.parse.quote(code))
    check("E4a 页面显示已作废", "已作废" in txt)
    check("E4b 有恢复按钮", "/restore" in txt)
    st, txt2 = get("/customers?q=" + urllib.parse.quote("新名字"))
    check("E4c 查客户页有作废标记", "已作废" in txt2)

    # ---------- E8 作废的码仍可校验 ----------
    print("\n" + "=" * 74)
    print("E8 作废只记账，码本身仍有效（符合设计）")
    print("=" * 74)
    check("E8a 客户端仍能校验通过", lic.verify_code(code) is not None)

    # ---------- E5 恢复 ----------
    print("\n" + "=" * 74)
    print("E5 ★恢复授权码")
    print("=" * 74)
    post("/restore", {"code": code})
    st, txt = get("/upgrade?code=" + urllib.parse.quote(code))
    check("E5a 页面回到正常", "未作废" in txt or "正常" in txt)
    check("E5b 有作废按钮（不再是恢复）", "/void" in txt)
    st, txt2 = get("/customers?q=" + urllib.parse.quote("新名字"))
    check("E5c 查客户页无作废标记", "🚫 已作废" not in txt2)

    # ---------- E6 反复切换 ----------
    print("\n" + "=" * 74)
    print("E6 作废/恢复可反复切换（最后一次生效）")
    print("=" * 74)
    post("/void", {"code": code})
    post("/restore", {"code": code})
    post("/void", {"code": code})
    st, txt = get("/upgrade?code=" + urllib.parse.quote(code))
    check("E6a 最终是已作废", "已作废" in txt)
    post("/restore", {"code": code})
    st, txt = get("/upgrade?code=" + urllib.parse.quote(code))
    check("E6b 再恢复回正常", ("未作废" in txt) or ("正常" in txt))

    # ---------- E9 事件行不污染列表 ----------
    print("\n" + "=" * 74)
    print("E9 事件行不出现在查客户列表")
    print("=" * 74)
    allr = rows()
    events = [r for r in allr if r.get("event")]
    st, txt = get("/customers")
    # 列表里的条目数应等于非事件行且不同的码数
    data_rows = [r for r in allr if not r.get("event") and r.get("code")]
    item_cls = 'class="item"'
    n_items = txt.count(item_cls)
    check("E9a 台账里有事件行", len(events) >= 3, f"{len(events)} 条事件")
    check("E9b 列表条目数 == 数据行数",
          n_items == len(data_rows),
          f"列表 {n_items} vs 数据 {len(data_rows)}")

    # ---------- E10 历史链完整 ----------
    print("\n" + "=" * 74)
    print("E10 升级 + 改名 + 作废后历史链完整")
    print("=" * 74)
    final = rows()
    has_sup = any(r.get("event") == "superseded" and r.get("code") == code
                  for r in final)
    has_edit = any(r.get("event") == "customer_edit" and r.get("code") == code
                   for r in final)
    has_chain = any(r.get("renewed_from") == code for r in final)
    check("E10a 有 superseded 事件", has_sup)
    check("E10b 有 customer_edit 事件", has_edit)
    check("E10c 有 renewed_from 新码", has_chain)
    check("E10d 旧码原记录未丢",
          any(r.get("code") == code and r.get("event") is None for r in final))

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
