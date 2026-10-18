"""配置备份/恢复测试（v2.1.8）—— 修「重启后授权丢失」。

背景：客户报「在 2.1.5 做完授权，重启软件后授权丢失了」。
实测确认：正常流程不丢（L1 通过），但两种情况会丢且**无任何提示**：
  · config.json 损坏（写入时断电/磁盘满 → JSON 截断）
  · config.json 被杀软/清理工具删除

修复：save() 时同步写 config.json.bak；_load() 失败时从 .bak 恢复，
      并记录 _config_load_error 供界面提示。

覆盖：
  P1 正常：激活 → save 后 .bak 存在
  P2 ★config.json 损坏 → 从 .bak 恢复，授权仍在
  P3 ★config.json 被删 → 从 .bak 恢复，授权仍在
  P4 恢复时记录了 _config_load_error（供界面提示）
  P5 主文件和 .bak 都坏 → 回默认档，且明确报错
  P6 首次运行（无主文件无备份）→ 不报错，用默认
  P7 .bak 与主文件内容一致
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
import tkinter.messagebox as _mb
# ★ 测试环境无头：把弹窗换成静默（否则会卡在等用户点确定）
_mb.showwarning = lambda *a, **k: None
_mb.showinfo = lambda *a, **k: None
_mb.showerror = lambda *a, **k: None
_mb.askyesno = lambda *a, **k: False
rt = tk.Tk(); rt.withdraw(); rt.destroy()
import sphdl.gui as G
import sphdl.config as C

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


def pump(ms):
    end = time.time() + ms / 1000
    while time.time() < end:
        try:
            app.root.update()
        except Exception:
            return
        time.sleep(0.01)


def set_paths(tdir):
    G.RECORDS_FILE = tdir / "records.json"
    G.CONFIG_FILE = tdir / "config.json"
    C.PROXY_BACKUP_FILE = tdir / "proxy_backup.json"


def new_app(tdir):
    set_paths(tdir)
    a = G.App()
    a.root.deiconify()
    return a


CODE = lic.make_code(2000, product=lic.PRODUCT_VIDEO)

# ============ P1 正常保存 + 备份 ============
print("=" * 74)
print("P1 正常：激活后 config.json.bak 自动生成")
print("=" * 74)
tdir = Path(tempfile.mkdtemp(prefix="bak_"))
set_paths(tdir)
(tdir / "config.json").write_text(json.dumps({
    "port": 8899, "download_dir": str(tdir / "dl"),
    "auto_proxy": False, "auto_download": False,
    "natural_capture": True, "guide_auto_shown": True,
    "license_codes": {},
}), encoding="utf-8")

app = new_app(tdir)
pump(1200)
app._activate_license(CODE)
pump(400)
bak = tdir / "config.json.bak"
print(f"  config.json:      {(tdir/'config.json').exists()}")
print(f"  config.json.bak:  {bak.exists()}")
check("P1a 主文件存在", (tdir / "config.json").exists())
check("P1b ★.bak 已自动生成", bak.exists())

# ============ P7 内容一致 ============
if bak.exists():
    main_txt = (tdir / "config.json").read_text(encoding="utf-8")
    bak_txt = bak.read_text(encoding="utf-8")
    check("P7 .bak 与主文件内容一致", main_txt == bak_txt,
          f"{len(main_txt)} vs {len(bak_txt)} 字节")
try:
    app._on_close()
except Exception:
    pass

# ============ P2 config.json 损坏 ============
print()
print("=" * 74)
print("P2 ★config.json 损坏 → 从 .bak 恢复，授权仍在")
print("=" * 74)
(tdir / "config.json").write_text('{"port": 8899, "license_codes": {', encoding="utf-8")
app2 = new_app(tdir)
pump(1200)
print(f"  恢复后档位: {app2.store.max_records}")
print(f"  错误提示: {app2.cfg._config_load_error or '(无)'}")
check("P2a ★损坏后授权仍恢复（档位 2000）", app2.store.max_records == 2000,
      str(app2.store.max_records))
check("P2b 仍可下载", app2._has_download_license() is True)
check("P4a ★记录了错误原因（供界面提示）",
      bool(app2.cfg._config_load_error),
      app2.cfg._config_load_error[:40])
try:
    app2._on_close()
except Exception:
    pass

# ============ P3 config.json 被删 ============
print()
print("=" * 74)
print("P3 ★config.json 被删 → 从 .bak 恢复")
print("=" * 74)
(tdir / "config.json").unlink()
app3 = new_app(tdir)
pump(1200)
print(f"  恢复后档位: {app3.store.max_records}")
print(f"  错误提示: {app3.cfg._config_load_error or '(无)'}")
check("P3a ★删除后授权仍恢复", app3.store.max_records == 2000,
      str(app3.store.max_records))
check("P4b 记录了丢失原因", "丢失" in (app3.cfg._config_load_error or ""),
      (app3.cfg._config_load_error or "")[:30])
try:
    app3._on_close()
except Exception:
    pass

# ============ P5 双坏 ============
print()
print("=" * 74)
print("P5 主文件和 .bak 都坏 → 回默认 + 明确报错")
print("=" * 74)
tdir5 = Path(tempfile.mkdtemp(prefix="both_bad_"))
set_paths(tdir5)
(tdir5 / "config.json").write_text("{坏", encoding="utf-8")
(tdir5 / "config.json.bak").write_text("{也坏", encoding="utf-8")
app5 = new_app(tdir5)
pump(1200)
print(f"  档位: {app5.store.max_records}")
print(f"  错误提示: {app5.cfg._config_load_error or '(无)'}")
check("P5a 双坏时回默认档", app5.store.max_records == 0,
      str(app5.store.max_records))
check("P5b ★明确说明需要重新激活",
      "重新激活" in (app5.cfg._config_load_error or ""),
      (app5.cfg._config_load_error or "")[:40])
try:
    app5._on_close()
except Exception:
    pass

# ============ P6 首次运行 ============
print()
print("=" * 74)
print("P6 首次运行（无主文件无备份）→ 不报错")
print("=" * 74)
tdir6 = Path(tempfile.mkdtemp(prefix="first_"))
set_paths(tdir6)
app6 = new_app(tdir6)
pump(1200)
print(f"  档位: {app6.store.max_records}")
print(f"  错误提示: {app6.cfg._config_load_error or '(无)'}")
check("P6 首次运行无错误提示",
      not app6.cfg._config_load_error,
      app6.cfg._config_load_error or "无")
try:
    app6._on_close()
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
