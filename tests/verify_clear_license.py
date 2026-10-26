"""端到端验证「清除授权」按钮是否真的清干净（v2.2.24 排查）。

═══ 客户疑问（2026-10-26）═══
「『清除授权』按钮功能确实可以完整清除授权不」

不能靠读代码下结论 —— 历史上就出过
「点了清除授权但实际还有效」（v2.0.19 只清 license_code 漏了 license_codes）。

必须实测五件事：
  C1 激活后确实处于已授权状态（有权限、档位上去）
  C2 点「清除授权」后 4 个字段**全部**归零
  C3 records.json 里**不会**残留授权码
  C4 重启程序后仍是未授权（不是内存里清了、磁盘还在）
  C5 未授权后下载真的被拦（权限判定生效，不是只改了显示）
  C6 只清单条产品线时，另一条不受影响
  C7 清除后 config.json 全文不含任何 SPHDL- 授权码字符串
"""
import io
import contextlib
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


# ── 用临时目录隔离，绝不碰客户真实配置 ──
tdir = Path(tempfile.mkdtemp(prefix="clearlic_"))
import sphdl.gui as G
import sphdl.license as lic
from sphdl import config as C

G.CONFIG_FILE = tdir / "config.json"
G.RECORDS_FILE = tdir / "records.json"
G.CONFIG_FILE.write_text("{}", encoding="utf-8")
G.RECORDS_FILE.write_text("[]", encoding="utf-8")

# 生成两个真实有效的授权码（一个视频号档、一个公众号）
import importlib
lic_file = ROOT / "sphdl" / "_lic_key.py"
if not lic_file.exists():
    secret = bytes.fromhex((ROOT / ".license_secret").read_text().strip())
    lic_file.write_text("K = %r\n" % lic.obfuscate(secret), encoding="utf-8")
importlib.reload(lic)

code_video = lic.make_code(2000, product=lic.PRODUCT_VIDEO)
code_mp = lic.make_code(0, product=lic.PRODUCT_MP_ARTICLE)
print(f"测试码（视频号 2000 档）: {code_video[:20]}…")
print(f"测试码（公众号）        : {code_mp[:20]}…")
print()

# ══════════════════ C1 激活后已授权 ══════════════════
print("=" * 74)
print("C1 激活后处于已授权状态")
print("=" * 74)
app = G.App()
app.root.withdraw()
app.root.update()

ok_v = app._activate_license(code_video)
print(f"  激活视频号码: ok={ok_v}")

cfg = app.cfg
print(f"  license_codes      : {list((cfg.license_codes or {}).keys())}")
print(f"  license_code(旧字段): {cfg.license_code[:18]!r}")
print(f"  _has_download_license: {app._has_download_license()}")
check("C1a 授权码已写入", bool(cfg.license_codes), str(len(cfg.license_codes)))
check("C1b 下载权限已开启", app._has_download_license() is True)

# 再激活公众号码，测双产品线
ok_m = app._activate_license(code_mp)
print(f"  激活公众号码: ok={ok_m}")
print(f"  激活公众号后 license_codes: {list((cfg.license_codes or {}).keys())}")

# 落盘一次（模拟运行中保存）
app.cfg.save()
saved = json.loads(G.CONFIG_FILE.read_text(encoding="utf-8"))
print(f"  磁盘上有的码: {list((saved.get('license_codes') or {}).keys())}")
check("C1c 磁盘 config 里也有码", bool(saved.get("license_codes")))

# ══════════════════ C2 清除后字段全清 ══════════════════
print()
print("=" * 74)
print("C2 清除授权后 4 个字段全部归零")
print("=" * 74)
had = app._clear_license()
app.root.update()
print(f"  _clear_license() 返回: {had}")
print(f"  license_codes         : {app.cfg.license_codes}")
print(f"  license_activated_at  : {app.cfg.license_activated_at}")
print(f"  license_code          : {app.cfg.license_code!r}")
print(f"  license_activated_at_legacy: {getattr(app.cfg, 'license_activated_at_legacy', '?')}")
check("C2a license_codes 已空", not app.cfg.license_codes, str(app.cfg.license_codes))
check("C2b license_activated_at 已空", not app.cfg.license_activated_at)
check("C2c license_code 已空", app.cfg.license_code == "", repr(app.cfg.license_code))
check("C2d legacy 时间戳归零",
      float(getattr(app.cfg, "license_activated_at_legacy", 0) or 0) == 0.0)
check("C2e 下载权限已关闭", app._has_download_license() is False)

# ══════════════════ C5 未授权后下载被拦 ══════════════════
print()
print("=" * 74)
print("C5 未授权后点下载真的被拦")
print("=" * 74)
rec = G.Record(id="lic_t", title="未授权拦截测试",
               url="http://example.com/t.mp4", decode_key="", size=1024,
               source="test", created="10-26 11:00:00", status="waiting", seq=1)
app.store.add(rec)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    try:
        app._submit_record("lic_t")
    except Exception as e:
        print("EXC", e)
st = app.store.items["lic_t"].status
print(f"  提交后状态: {st}")
check("C5 下载被拦（状态仍 waiting）", st == "waiting", st)

# ══════════════════ C3/C7 磁盘无残留 ══════════════════
print()
print("=" * 74)
print("C3/C7 磁盘配置里没有授权残留")
print("=" * 74)
app.cfg.save()
disk = G.CONFIG_FILE.read_text(encoding="utf-8")
d = json.loads(disk)
print(f"  config.json 大小: {len(disk)} 字符")
print(f"  license_codes: {d.get('license_codes')}")
print(f"  records.json 里有码吗: {'SPHDL-' in G.RECORDS_FILE.read_text(encoding='utf-8')}")
check("C3a 磁盘 license_codes 为空", not d.get("license_codes"), str(d.get("license_codes")))
check("C3b records.json 无授权码",
      "SPHDL-" not in G.RECORDS_FILE.read_text(encoding="utf-8"))
check("C7 config.json 全文无 SPHDL- 授权码", "SPHDL-" not in disk,
      "命中" if "SPHDL-" in disk else "干净")

# ══════════════════ C4 重启后仍未授权 ══════════════════
print()
print("=" * 74)
print("C4 重启后仍是未授权（不是只清了内存）")
print("=" * 74)
app.root.destroy()
app2 = G.App()
app2.root.withdraw()
app2.root.update()
print(f"  重启后 license_codes: {app2.cfg.license_codes}")
print(f"  重启后 下载权限     : {app2._has_download_license()}")
check("C4a 重启后无授权码", not app2.cfg.license_codes)
check("C4b 重启后仍无下载权限", app2._has_download_license() is False)
app2.root.destroy()

# ══════════════════ C6 单产品线清除 ══════════════════
print()
print("=" * 74)
print("C6 只清单条产品线时，另一条不受影响")
print("=" * 74)
G.CONFIG_FILE.write_text("{}", encoding="utf-8")
app3 = G.App()
app3.root.withdraw()
app3.root.update()
app3._activate_license(code_video)
app3._activate_license(code_mp)
print(f"  激活后: {list((app3.cfg.license_codes or {}).keys())}")
before_n = len(app3.cfg.license_codes)
app3._clear_license(lic.PRODUCT_MP_ARTICLE)
print(f"  清公众号后: {list((app3.cfg.license_codes or {}).keys())}")
check("C6a 只少了公众号那一条",
      len(app3.cfg.license_codes) == before_n - 1,
      f"{before_n} → {len(app3.cfg.license_codes)}")
check("C6b 视频号授权仍在",
      lic.PRODUCT_VIDEO in (app3.cfg.license_codes or {}),
      str(list(app3.cfg.license_codes or {})))
app3.root.destroy()

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
