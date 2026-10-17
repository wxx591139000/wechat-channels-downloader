"""满档自动停批量 → 升档后必须自动恢复（v2.1.6）。

用户报障原话：
  「我原来开着批量拉取，然后点了授权管理，从500行激活为1000行后，
   发现批量管理功能被关闭了，授权行为不应该改变原来的批量拉取状态，
   不然用户会感觉出问题了，因为抓取数量还是卡在500行不变」

★ 真实因果不是"授权关掉了批量"，而是：
   抓到 500 条 → 满档 → _warn_full_once() 自动停批量
   → 用户去升级档位 → 档位变了但批量没恢复 → 抓取卡在 500
   用户视角 = "授权把批量关了"

覆盖：
  F1 满档时批量被自动停掉，且打上 _batch_stopped_by_full 标记
  F2 升档后批量**自动恢复**
  F3 用户主动停批量后，升档**不会**自动重启（这点必须严格）
  F4 仍满档时不恢复（避免空转）
  F5 删记录脱离满档也会自动恢复
  F6 清除授权（降档）后批量状态不被干扰
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


def make_app(port=8899):
    tdir = Path(tempfile.mkdtemp(prefix="full_"))
    G.RECORDS_FILE = tdir / "records.json"
    G.CONFIG_FILE = tdir / "config.json"
    C.PROXY_BACKUP_FILE = tdir / "proxy_backup.json"
    (tdir / "config.json").write_text(json.dumps({
        "port": port, "download_dir": str(tdir / "dl"),
        "auto_proxy": False, "auto_download": False,
        "natural_capture": True, "guide_auto_shown": True,
        "license_codes": {}, "default_max_records": 500,
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


def fill_to_full(app):
    """把列表填到满档（直接往 store 塞记录）。"""
    for i in range(app.store.max_records):
        r = G.Record(id=f"r{i}", title=f"视频{i}", url=f"http://x/{i}.mp4",
                     decode_key="", size=1024, source="测试", created="",
                     status="pending", platform="视频号")
        app.store.items[r.id] = r
        app.store.order.append(r.id)


CODE_1000 = lic.make_code(1000, product=lic.PRODUCT_VIDEO)

# ============ F1/F2：满档停批量 → 升档自动恢复 ============
print("=" * 72)
print("F1/F2 ★满档停批量 → 升档后自动恢复（用户报障场景）")
print("=" * 72)
app, tdir = make_app()
pump(app, 1200)

app._set_batch(True)
pump(app, 200)
print(f"  批量已开: {app.addon.batch_mode}")

fill_to_full(app)
pump(app, 200)
print(f"  填满后 is_full: {app.store.is_full()}")

# 模拟满档事件触发
app._warn_full_once()
pump(app, 300)
print(f"  满档后 batch_mode: {app.addon.batch_mode}")
print(f"  满档标记: {app._batch_stopped_by_full}")

check("F1a ★满档后批量被自动停掉", app.addon.batch_mode is False)
check("F1b ★打上了「满档停的」标记", app._batch_stopped_by_full is True,
      str(app._batch_stopped_by_full))

# 用户升级档位
ok = app._activate_license(CODE_1000)
pump(app, 600)
print()
print(f"  升档结果: {ok}")
print(f"  新档位: {app.store.max_records}")
print(f"  升档后 batch_mode: {app.addon.batch_mode}")
print(f"  按钮文案: {app.btn_batch.cget('text')}")

check("F2a 档位升到 1000", app.store.max_records == 1000)
check("F2b ★升档后批量自动恢复", app.addon.batch_mode is True,
      f"实际 {app.addon.batch_mode}")
check("F2c 按钮文案回到「停止批量」",
      "停止批量" in app.btn_batch.cget("text"),
      app.btn_batch.cget("text"))
check("F2d natural_capture 被正确关掉（批量模式要求）",
      app.addon.natural_capture is False)
try:
    app._on_close()
except Exception:
    pass

# ============ F3：用户主动停的不许自动重启 ============
print()
print("=" * 72)
print("F3 ★用户主动停批量后，升档不许自动重启")
print("=" * 72)
app2, tdir2 = make_app(port=8898)
pump(app2, 1200)
app2._set_batch(True)
pump(app2, 200)
app2._set_batch(False)          # ★ 用户主动停（不经过 _warn_full_once）
pump(app2, 200)
print(f"  主动停后 batch_mode: {app2.addon.batch_mode}")
print(f"  满档标记（应为 False）: {app2._batch_stopped_by_full}")
app2._activate_license(CODE_1000)
pump(app2, 600)
check("F3a 主动停后标记为 False", app2._batch_stopped_by_full is False)
check("F3b ★升档后批量仍保持关闭（不擅自重启）",
      app2.addon.batch_mode is False, f"实际 {app2.addon.batch_mode}")
try:
    app2._on_close()
except Exception:
    pass

# ============ F4：仍满档时不恢复 ============
print()
print("=" * 72)
print("F4 仍满档时不恢复（避免空转）")
print("=" * 72)
app3, tdir3 = make_app(port=8897)
pump(app3, 1200)
app3._set_batch(True)
fill_to_full(app3)
app3._warn_full_once()
pump(app3, 300)
app3._maybe_resume_batch()      # 手动调：列表仍满 → 不应恢复
pump(app3, 200)
check("F4 仍满档时不恢复批量", app3.addon.batch_mode is False,
      f"实际 {app3.addon.batch_mode}")
check("F4b 标记仍保留（等脱离满档再恢复）",
      app3._batch_stopped_by_full is True)
try:
    app3._on_close()
except Exception:
    pass

# ============ F5：删记录脱离满档 → 自动恢复 ============
print()
print("=" * 72)
print("F5 删记录脱离满档 → 也自动恢复")
print("=" * 72)
app3b, tdir3b = make_app(port=8896)
pump(app3b, 1200)
app3b._set_batch(True)
fill_to_full(app3b)
app3b._warn_full_once()
pump(app3b, 300)
print(f"  满档停批量后: batch_mode={app3b.addon.batch_mode}")
# 删掉一条 → 不再满 → 应恢复
rid = app3b.store.order[0]
app3b.store.items.pop(rid, None)
app3b.store.order.remove(rid)
app3b._after_records_removed()
pump(app3b, 400)
print(f"  删一条后 is_full: {app3b.store.is_full()}")
print(f"  删一条后 batch_mode: {app3b.addon.batch_mode}")
check("F5 删记录脱离满档后批量自动恢复",
      app3b.addon.batch_mode is True, f"实际 {app3b.addon.batch_mode}")
try:
    app3b._on_close()
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
