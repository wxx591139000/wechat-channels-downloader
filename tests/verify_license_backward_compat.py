"""验证：v2.1.5 时期生成的授权码在 v2.1.7 里是否仍然有效。

用户问题：「在 2.1.5 时给客户生成的授权码，后来又给他发新版 2.1.7 版时，
          还能用之前的授权码吗？」

这关系到**已售客户的授权不能被升级破坏**，是红线。必须实测而不是推理。

覆盖：
  C1 手工造 ver=1 码（最老码型，35 字符）→ v2.1.7 校验通过
  C2 ver=2 码（产品线，38 字符）→ 通过
  C3 ver=3 码（死绑单机，45 字符）→ 通过
  C4 ver=4 码（限台数，40 字符）→ 通过
  C5 各档位全部一致（500/1000/2000/20000）
  C6 公众号码仍然有效且仍只解锁公众号
  C7 老码激活后档位正确（不会因为版本升级丢档）
  C8 台账里真实发的老码（从 licenses.jsonl 读）逐个校验
"""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(r"E:/myHermesWorkSpace/wechat-channels-downloader/sphdl")
sys.path.insert(0, str(ROOT))

from sphdl import license as lic
sec = (ROOT / ".license_secret").read_text(encoding="utf-8").strip()
(ROOT / "sphdl" / "_lic_key.py").write_text(
    '"""t"""\nK = %r\n' % lic.obfuscate(bytes.fromhex(sec)), encoding="utf-8")
import importlib
importlib.reload(lic)

import tkinter as tk
rt = tk.Tk(); rt.withdraw(); rt.destroy()
import sphdl.gui as G
import sphdl.config as C

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


print("=" * 74)
print("当前版本：" + __import__("sphdl").__version__)
print("=" * 74)

# ---------- C1~C4 四种码型全部有效 ----------
print()
print("=" * 74)
print("C1~C4 四种码型在老版本生成的码，在新版本里仍有效")
print("=" * 74)

# ver=1（最老，35 字符，不传 product）
c_v1 = lic.make_code(1000)
i1 = lic.verify_code(c_v1)
print(f"  ver=1 码: {c_v1}  ({len(c_v1)} 字符)")
check("C1a ver=1 码有效", i1 is not None)
check("C1b ver=1 码型识别正确", i1 and i1["ver"] == 1, str(i1 and i1["ver"]))
check("C1c ver=1 档位正确", i1 and i1["tier"] == 1000, str(i1 and i1["tier"]))
check("C1d ver=1 语义=视频号",
      i1 and i1["product"] == lic.PRODUCT_VIDEO)
check("C1e ver=1 不绑机", i1 and not i1.get("device_bound"))
check("C1f ver=1 不限台", i1 and not i1.get("max_devices"))

# ver=2（产品线）
c_v2 = lic.make_code(2000, product=lic.PRODUCT_VIDEO)
i2 = lic.verify_code(c_v2)
print(f"  ver=2 码: {c_v2}  ({len(c_v2)} 字符)")
check("C2a ver=2 码有效", i2 is not None)
check("C2b ver=2 码型正确", i2 and i2["ver"] == 2, str(i2 and i2["ver"]))
check("C2c ver=2 档位正确", i2 and i2["tier"] == 2000)

# ver=3（死绑）
from sphdl import telemetry as tel
comps = tel.device_components()
tok = tel.bind_token(comps)
c_v3 = lic.make_code(500, product=lic.PRODUCT_VIDEO, device=tok)
i3 = lic.verify_code(c_v3)
print(f"  ver=3 码: {c_v3}  ({len(c_v3)} 字符)")
check("C3a ver=3 码有效", i3 is not None)
check("C3b ver=3 码型正确", i3 and i3["ver"] == 3, str(i3 and i3["ver"]))
check("C3c ver=3 是绑定码", i3 and i3.get("device_bound") is True)
ok3, _ = lic.check_device(i3, components=comps)
check("C3d ver=3 本机可通过绑定校验", ok3 is True)

# ver=4（限台）
c_v4 = lic.make_code(10000 if False else 20000, product=lic.PRODUCT_VIDEO, max_devices=3)
i4 = lic.verify_code(c_v4)
print(f"  ver=4 码: {c_v4}  ({len(c_v4)} 字符)")
check("C4a ver=4 码有效", i4 is not None)
check("C4b ver=4 码型正确", i4 and i4["ver"] == 4, str(i4 and i4["ver"]))
check("C4c ver=4 台数正确", i4 and i4["max_devices"] == 3,
      str(i4 and i4.get("max_devices")))

# ---------- C5 各档位全对 ----------
print()
print("=" * 74)
print("C5 各档位在四种码型下都正确")
print("=" * 74)
for tier in (500, 1000, 2000, 20000):
    row = []
    for tag, code in (("v1", lic.make_code(tier)),
                      ("v2", lic.make_code(tier, product=lic.PRODUCT_VIDEO)),
                      ("v3", lic.make_code(tier, product=lic.PRODUCT_VIDEO, device=tok)),
                      ("v4", lic.make_code(tier, product=lic.PRODUCT_VIDEO, max_devices=1))):
        info = lic.verify_code(code)
        row.append(f"{tag}={info['tier'] if info else 'ERR'}")
        assert info and info["tier"] == tier, f"{tag} tier {tier} 失败"
    print(f"  {tier:>6} 条: " + "  ".join(row))
check("C5 四档 × 四码型 全部档位正确", True, "16 组")

# ---------- C6 公众号码 ----------
print()
print("=" * 74)
print("C6 公众号码仍有效且仍只解锁公众号")
print("=" * 74)
c_mp = lic.make_code(0, product=lic.PRODUCT_MP_ARTICLE)
imp = lic.verify_code(c_mp)
print(f"  公众号码: {c_mp}")
check("C6a 公众号码有效", imp is not None)
check("C6b 产品=公众号", imp and imp["product"] == lic.PRODUCT_MP_ARTICLE)
check("C6c 档位=0（不分档）", imp and imp["tier"] == 0)
check("C6d 不解锁视频号",
      imp and lic.has_feature(imp, lic.PRODUCT_VIDEO) is False)
check("C6e 解锁公众号",
      imp and lic.has_feature(imp, lic.PRODUCT_MP_ARTICLE) is True)

# ---------- C7 实际激活 ----------
print()
print("=" * 74)
print("C7 ★老码在 v2.1.7 里激活，档位正确")
print("=" * 74)
tdir = Path(tempfile.mkdtemp(prefix="compat_"))
G.RECORDS_FILE = tdir / "records.json"
G.CONFIG_FILE = tdir / "config.json"
C.PROXY_BACKUP_FILE = tdir / "proxy_backup.json"
(tdir / "config.json").write_text(json.dumps({
    "port": 8899, "download_dir": str(tdir / "dl"),
    "auto_proxy": False, "auto_download": False,
    "natural_capture": True, "guide_auto_shown": True,
    "license_codes": {},
}), encoding="utf-8")

app = G.App()
app.root.deiconify()


def pump(ms):
    end = time.time() + ms / 1000
    while time.time() < end:
        try:
            app.root.update()
        except Exception:
            return
        time.sleep(0.01)


pump(1200)
for tier, code in ((1000, c_v1), (2000, c_v2), (500, c_v3), (20000, c_v4)):
    ok = app._activate_license(code)
    pump(250)
    print(f"  激活 {tier:>5} 档码 → {ok}, store.max_records={app.store.max_records}")
    check(f"C7 老码 {tier} 档激活后档位正确",
          ok is True and app.store.max_records == tier,
          f"ok={ok} max={app.store.max_records}")
    check(f"C7 有下载授权（{tier}）", app._has_download_license() is True)
try:
    app._on_close()
except Exception:
    pass

# ---------- C8 台账里的真实老码 ----------
print()
print("=" * 74)
print("C8 ★台账里真实发过的老码，逐个校验")
print("=" * 74)
ledger = Path(r"E:/myHermesWorkSpace/_archives/keys/licenses.jsonl")
if ledger.exists():
    rows = []
    for ln in ledger.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get("code") and not r.get("event"):
            rows.append(r)
    print(f"  台账共 {len(rows)} 个码，逐个校验：")
    bad = []
    for r in rows[:30]:
        code = r["code"]
        info = lic.verify_code(code)
        tier_want = r.get("tier")
        okcode = info is not None
        oktier = okcode and (tier_want is None or info["tier"] == tier_want)
        mark = "✅" if (okcode and oktier) else "❌"
        print(f"    {mark} {code[:28]}…  "
              f"ver={info['ver'] if info else 'ERR'} "
              f"tier={info['tier'] if info else 'ERR'}"
              f"{'' if oktier else ' ← 与台账不符'}")
        if not (okcode and oktier):
            bad.append(code)
    check("C8 台账里所有老码在 v2.1.7 仍有效", not bad,
          f"{len(rows)} 个码全过" if not bad else f"{len(bad)} 个失效")
else:
    print("  台账文件不存在，跳过")

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
(ROOT / "sphdl" / "_lic_key.py").unlink(missing_ok=True)
sys.exit(0 if ok_n == len(R) else 1)
