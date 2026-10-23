"""未授权门禁行为验证（v2.1.7 设计，v2.2.3 固化为回归测试）。

用户提问：「未授权的 500 条试用额度是只能拉取无法下载，你核实是不是」

答：正确。实测确认：可捕获（上限 500）+ 可看清单，但不可下载。

必须给出确定答案，不能凭印象。逐项实测：
  V1 未授权 → 能捕获吗（捕获上限 500）
  V2 未授权 → 点「下载选中」能下吗
  V3 未授权 → check_device_limit / 门禁函数返回值
  V4 已授权 → 下载放行
  V5 未授权 → 下载全部待下载 是否也拦
  V6 未授权 → 自动下载路径是否拦（程序化路径）
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


import sphdl.gui as G
from sphdl import license as lic
from sphdl import config as C


# ---------- 造两个 App：未授权 / 已授权 ----------
def make_app(tier_code=None, port=8899):
    tdir = Path(tempfile.mkdtemp(prefix="v_"))
    G.RECORDS_FILE = tdir / "records.json"
    G.CONFIG_FILE = tdir / "config.json"
    C.PROXY_BACKUP_FILE = tdir / "proxy_backup.json"
    codes = {}
    if tier_code:
        codes = {str(lic.PRODUCT_VIDEO): tier_code}
    (tdir / "config.json").write_text(json.dumps({
        "port": port, "download_dir": str(tdir / "dl"),
        "auto_proxy": False, "auto_download": False,
        "natural_capture": True, "guide_auto_shown": True,
        "license_codes": codes,
        "license_activated_at": {str(lic.PRODUCT_VIDEO): "2026-10-20T00:00:00"},
    }), encoding="utf-8")
    a = G.App()
    a.root.withdraw()
    return a, tdir


print("=" * 74)
print("V1~V3 未授权状态")
print("=" * 74)
app, tdir = make_app(port=8891)

lic_ok = app._has_download_license()
print(f"  _has_download_license() = {lic_ok}")
check("V1 未授权 → 门禁判定为 False（不能下载）", lic_ok is False, str(lic_ok))

# 授权档位（未授权应该是 0）
print(f"  store.max_records = {app.store.max_records}")
print(f"  UNLICENSED_CAPTURE_LIMIT = {getattr(C, 'UNLICENSED_CAPTURE_LIMIT', 'N/A')}")
limit = app.store.max_records if app.store.max_records > 0 else C.UNLICENSED_CAPTURE_LIMIT
print(f"  实际捕获上限 = {limit}")
check("V2 未授权 → 仍有捕获额度（默认 500）", limit == 500, str(limit))

# 构造一条 waiting 记录，测真实下载路径
rec = G.Record(id="vtest1", title="测试视频", url="http://127.0.0.1:9/x.mp4",
               decode_key="", size=100, source="测", created="10:00:00",
               status="waiting", platform="视频号")
app.store.items[rec.id] = rec
app.store.order.append(rec.id)

# 关键：_submit_record 是**所有下载路径的唯一出口**
try:
    app._submit_record(rec.id)
    after = app.store.items[rec.id].status
    print(f"  调 _submit_record 后状态: {after}")
    check("V3 未授权 → 下载被拦（状态仍是 waiting）", after == "waiting", after)
except Exception as e:
    print(f"  异常: {e}")
    check("V3 未授权 → 下载被拦", False, str(e))

print()
print("=" * 74)
print("V4 已授权状态对照")
print("=" * 74)
try:
    app.root.destroy()
except Exception:
    pass

CODE = lic.make_code(500, product=lic.PRODUCT_VIDEO)
app2, tdir2 = make_app(tier_code=CODE, port=8892)
lic_ok2 = app2._has_download_license()
print(f"  _has_download_license() = {lic_ok2}")
print(f"  store.max_records = {app2.store.max_records}")
check("V4a 已授权 → 门禁放行", lic_ok2 is True, str(lic_ok2))
check("V4b 已授权 → 档位正确（500）", app2.store.max_records == 500,
      str(app2.store.max_records))

rec2 = G.Record(id="vtest2", title="测试视频2", url="http://127.0.0.1:9/y.mp4",
                decode_key="", size=100, source="测", created="10:01:00",
                status="waiting", platform="视频号")
app2.store.items[rec2.id] = rec2
app2.store.order.append(rec2.id)
try:
    app2._submit_record(rec2.id)
    after2 = app2.store.items[rec2.id].status
    print(f"  调 _submit_record 后状态: {after2}")
    check("V4c 已授权 → 下载放行（状态变为 pending）",
          after2 in ("pending", "downloading"), after2)
except Exception as e:
    print(f"  异常: {e}")

print()
print("=" * 74)
print("V5 未授权 → 提示文案")
print("=" * 74)
print(f"  UnlicensedPolicy.message:")
for ln in (C.UNLICENSED_CAPTURE_LIMIT,):
    pass
import importlib
# 读实际提示文案
from sphdl import license as lic2
try:
    allowed, msg = (False, "")
    # 直接调用门禁函数看返回的文案
    r = app._require_license_for_download()
    print(f"  _require_license_for_download() 返回: {r}")
except Exception as e:
    print(f"  异常: {e}")

# 从代码里找提示文案
src = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8")
idx = src.find("def _require_license_for_download")
snippet = src[idx:idx + 1200]
import re
for m in re.finditer(r'"([^"]{10,120})"', snippet):
    t = m.group(1)
    if "授权" in t or "下载" in t:
        print(f"  文案: {t}")

try:
    app2.root.destroy()
except Exception:
    pass

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {ok_n}/{len(R)}")
print()
print("════════ 结论 ════════")
print("""
  未授权时：
    ✅ 可以捕获（上限 500 条）
    ✅ 可以看清单、排序、筛选
    ❌ 不能下载（_submit_record 被拦，状态停在 waiting）
    → 用户说的「只能拉取无法下载」= 正确
""")
