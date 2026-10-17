"""未授权下载门禁测试（v2.1.7）。

用户要求：
  「视频号下载默认行数改为 0，只有授权后才能下载」
  并澄清效果为：未授权可以捕获、可以看列表，但**不能下载**。

覆盖：
  G1 默认档位 = 0（未授权）
  G2 0 档时 is_full() 恒为 False（**不限制捕获**，这是刻意的）
  G3 0 档时未授权记录能正常入列表（可捕获）
  G4 ★未授权点「下载选中」被拦（_has_download_license=False）
  G5 ★未授权 _submit_record 拒收（自动下载/重试等所有路径）
  G6 ★授权后能正常下载
  G7 公众号产品码不算视频号授权（不能拿公众号码下视频号）
  G8 label_of(0) 显示"未授权"而不是"0 条"
  G9 绑定版本（LOCKED_MAX_RECORDS）视为已授权
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


def make_app(port):
    tdir = Path(tempfile.mkdtemp(prefix="lic_"))
    G.RECORDS_FILE = tdir / "records.json"
    G.CONFIG_FILE = tdir / "config.json"
    C.PROXY_BACKUP_FILE = tdir / "proxy_backup.json"
    (tdir / "config.json").write_text(json.dumps({
        "port": port, "download_dir": str(tdir / "dl"),
        "auto_proxy": False, "auto_download": False,
        "natural_capture": True, "guide_auto_shown": True,
        "license_codes": {},
    }), encoding="utf-8")
    a = G.App()
    a.root.deiconify()
    return a, tdir


def pump(app, ms):
    end = time.time() + ms / 1000
    while time.time() < end:
        try:
            app.root.update()
        except Exception:
            return
        time.sleep(0.01)


# ============ G1/G2/G3/G8：默认 0 档 = 可捕获 ============
print("=" * 72)
print("G1~G3 ★默认 0 档：可捕获、列表不限、但不能下载")
print("=" * 72)
app, tdir = make_app(8881)
pump(app, 1200)

print(f"  config.DEFAULT_MAX_RECORDS = {C.DEFAULT_MAX_RECORDS}")
print(f"  app 默认档位 = {app.cfg.default_max_records}")
print(f"  实际生效档位 = {app.store.max_records}")

check("G1 默认档位 = 0", app.cfg.default_max_records == 0,
      str(app.cfg.default_max_records))
check("G2a 0 档时 is_full() = False（不限制捕获）",
      app.store.is_full() is False)
check("G8 label_of(0) 显示「未授权」",
      "未授权" in C.label_of(0), C.label_of(0))

# 塞 20 条，验证都能进来
for i in range(20):
    rec = G.Record(id=f"r{i}", title=f"视频{i}", url=f"http://x/{i}.mp4",
                   decode_key="", size=1024, source="测试", created="",
                   status="waiting", platform="视频号")
    app.store.add(rec)
pump(app, 200)
print(f"  塞 20 条后列表总数 = {len(app.store.order)}")
check("G3 ★0 档时记录能正常入库（可捕获）",
      len(app.store.order) >= 20, str(len(app.store.order)))

# ============ G4/G5：未授权不能下载 ============
print()
print("=" * 72)
print("G4/G5 ★未授权：下载被拦")
print("=" * 72)
print(f"  _has_download_license() = {app._has_download_license()}")
check("G4a 未授权 _has_download_license() = False",
      app._has_download_license() is False)

before = app.store.items["r0"].status
ok_submit = app._submit_record("r0")
pump(app, 200)
after = app.store.items["r0"].status
print(f"  _submit_record('r0') 返回 {ok_submit}，状态 {before} → {after}")
check("G5 ★未授权 _submit_record 拒收", ok_submit is False)
check("G5b 记录状态未被改成 pending", after != "pending", after)

# ============ G7：公众号码不算视频号授权 ============
print()
print("=" * 72)
print("G7 公众号码不能解锁视频号下载")
print("=" * 72)
mp_code = lic.make_code(0, product=lic.PRODUCT_MP_ARTICLE)
app._activate_license(mp_code)
pump(app, 400)
print(f"  激活公众号码后 _has_download_license() = {app._has_download_license()}")
check("G7 ★公众号码不算视频号授权",
      app._has_download_license() is False,
      "公众号码只解锁公众号功能")
try:
    app._on_close()
except Exception:
    pass

# ============ G6：授权后能下载 ============
print()
print("=" * 72)
print("G6 ★激活视频号授权后能正常下载")
print("=" * 72)
app2, tdir2 = make_app(8882)
pump(app2, 1200)
vc_code = lic.make_code(1000, product=lic.PRODUCT_VIDEO)
app2._activate_license(vc_code)
pump(app2, 500)
print(f"  激活视频号码后档位 = {app2.store.max_records}")
print(f"  _has_download_license() = {app2._has_download_license()}")

rec = G.Record(id="t1", title="测试视频", url="http://x/t.mp4",
               decode_key="", size=1024, source="测试", created="",
               status="waiting", platform="视频号")
app2.store.add(rec)
pump(app2, 200)
check("G6a 授权后 _has_download_license() = True",
      app2._has_download_license() is True)
check("G6b 授权后档位 = 1000", app2.store.max_records == 1000,
      str(app2.store.max_records))
check("G6c 列表上限文案是「标准」",
      "标准" in C.label_of(app2.store.max_records),
      C.label_of(app2.store.max_records))
try:
    app2._on_close()
except Exception:
    pass

# ============ G9：绑定版本视为已授权 ============
print()
print("=" * 72)
print("G9 绑定版本（LOCKED_MAX_RECORDS）视为已授权")
print("=" * 72)
app3, tdir3 = make_app(8883)
pump(app3, 1200)
app3.cfg.locked_max_records = 2000      # 模拟预置锁档版本
check("G9 绑定版本 _has_download_license() = True",
      app3._has_download_license() is True)
try:
    app3._on_close()
except Exception:
    pass

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
(ROOT / "sphdl" / "_lic_key.py").unlink(missing_ok=True)
sys.exit(0 if ok_n == len(R) else 1)
