"""授权继承规则验证（v2.2.7）。

═══ 用户明确要求（2026-10-23）═══
「后续压缩包分享给别人时，默认是不能有授权的。
  只能是对方电脑装过授权，更换新版本时继承电脑里已有的授权。」

═══ 这条规则要防的攻击场景 ═══
  打包时误把开发机的 config.json 带进 zip（或客户自己目录里残留）
  → 别人解压就白得授权。
  旧逻辑（v2.2.5/2.2.6）实测确实会继承 → 已修复。

覆盖：
  L1 ★压缩包目录里带授权 → 迁移后授权被清空（核心）
  L2 设置项保留（下载目录等），只清授权
  L3 ★本机用户级已有授权 → 正常沿用（升级不丢授权）
  L4 相邻版本目录带授权 → 也不继承
  L5 records.json（列表）仍可正常迁移（不涉及授权）
  L6 打包产物里不含 config.json / 授权码
"""
import json
import os
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


from sphdl import userdata as U

_orig_frozen, _orig_appdir = U.is_frozen, U.app_dir


def setup(tmpname):
    tmp = Path(tempfile.mkdtemp(prefix=tmpname + "_"))
    app = tmp / "dist" / "v2.2.7" / "微信视频号下载器"
    app.mkdir(parents=True)
    local = tmp / "LocalAppData"
    local.mkdir()
    U.is_frozen = lambda: True
    U.app_dir = lambda: app
    os.environ["LOCALAPPDATA"] = str(local)
    U.reset_for_tests()
    return tmp, app, local


def read(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


print("=" * 76)
print("L1 ★压缩包目录里带授权 → 必须清空")
print("=" * 76)
tmp, app, local = setup("leak")
# 模拟"打包时误带了开发机的 config.json"
(app / "config.json").write_text(json.dumps({
    "license_codes": {"1": "LEAKED-VIDEO-CODE", "2": "LEAKED-MP-CODE"},
    "download_dir": "E:/我的下载",
    "auto_download": True,
    "max_records": 2000,
}, ensure_ascii=False), encoding="utf-8")

p = U.resolve("config.json")
d = read(p)
print(f"  迁移后 license_codes: {d.get('license_codes')}")
print(f"  保留设置: download_dir={d.get('download_dir')} "
      f"auto_download={d.get('auto_download')} max_records={d.get('max_records')}")
check("L1a 授权码被清空（别人拿不到授权）",
      not d.get("license_codes"), str(d.get("license_codes")))
check("L2a 下载目录保留", d.get("download_dir") == "E:/我的下载",
      str(d.get("download_dir")))
check("L2b auto_download 保留", d.get("auto_download") is True)
check("L2c max_records 保留", d.get("max_records") == 2000)

print()
print("=" * 76)
print("L4 相邻版本目录带授权 → 也不继承")
print("=" * 76)
tmp2, app2, local2 = setup("adj")
oldver = tmp2 / "dist" / "v2.2.3" / "微信视频号下载器"
oldver.mkdir(parents=True)
(oldver / "config.json").write_text(json.dumps({
    "license_codes": {"1": "OLD-VERSION-AUTH"},
    "download_dir": "D:/旧设置",
}, ensure_ascii=False), encoding="utf-8")

p = U.resolve("config.json")
d = read(p)
print(f"  从 v2.2.3 迁移后: license_codes={d.get('license_codes')} "
      f"download_dir={d.get('download_dir')}")
check("L4a 相邻版本的授权也不继承", not d.get("license_codes"),
      str(d.get("license_codes")))
check("L4b 设置仍被继承", d.get("download_dir") == "D:/旧设置")

print()
print("=" * 76)
print("L3 ★本机已授权的机器升级 → 授权必须保住")
print("=" * 76)
tmp3, app3, local3 = setup("keep")
udir = local3 / "sphdl"
udir.mkdir(parents=True)
(udir / "config.json").write_text(json.dumps({
    "license_codes": {"1": "MY-VALID-CODE"},
    "max_records": 20000,
}, ensure_ascii=False), encoding="utf-8")

p = U.resolve("config.json")
d = read(p)
print(f"  用户级已有授权的机器 → {p}")
print(f"  license_codes: {d.get('license_codes')}")
check("L3a 本机授权保留（升级不丢）",
      d.get("license_codes", {}).get("1") == "MY-VALID-CODE",
      str(d.get("license_codes")))
check("L3b 走的是用户级路径", p.parent == udir, str(p))

# 即使程序目录里也有带授权的 config，也不能覆盖本机授权
(app3 / "config.json").write_text(json.dumps({
    "license_codes": {"1": "PACKAGED-LEAK"},
}), encoding="utf-8")
U.reset_for_tests()
p = U.resolve("config.json")
d = read(p)
print(f"  程序目录有 LEAK 时，仍用: {d.get('license_codes')}")
check("L3c 程序目录的授权不能覆盖本机授权",
      d.get("license_codes", {}).get("1") == "MY-VALID-CODE",
      str(d.get("license_codes")))

print()
print("=" * 76)
print("L5 records.json（列表）正常迁移 — 不涉及授权")
print("=" * 76)
tmp4, app4, local4 = setup("rec")
(app4 / "records.json").write_text(json.dumps([
    {"id": "r1", "title": "老客户抓的视频", "platform": "视频号"},
    {"id": "r2", "title": "第二个", "platform": "公众号"},
], ensure_ascii=False), encoding="utf-8")

rp = U.resolve("records.json")
recs = read(rp)
print(f"  records 迁移 → {rp.name}  条数={len(recs)}")
check("L5a 列表正常迁移（老客户不丢清单）", len(recs) == 2, f"{len(recs)} 条")
check("L5b 列表内容完整", recs[0].get("title") == "老客户抓的视频")

print()
print("=" * 76)
print("L6 打包产物不含授权")
print("=" * 76)
import zipfile
zips = sorted((ROOT / "dist").glob("*.zip"))
if zips:
    z = zipfile.ZipFile(zips[-1])
    names = z.namelist()
    # ★ 不能只匹配 "license" —— 第三方库的 LICENSE 文件会被误报。
    #   只认我们自己的数据文件（精确文件名 / 明确后缀）
    _base = [n.rsplit("/", 1)[-1].lower() for n in names]
    exact = ("config.json", "config.json.bak", "records.json",
             "proxy_backup.json", "_lic_key.py", "licenses.jsonl")
    bad = [n for n, b in zip(names, _base)
           if b in exact
           or b.endswith(".secret")
           or b.startswith("licenses.jsonl")]
    print(f"  检查: {zips[-1].name}")
    if bad:
        for n in bad:
            print("    ❌", n)
    else:
        print("    ✅ 无 config.json / 密钥 / 台账")
    check("L6a 发布包内无敏感文件", not bad, str(bad[:3]))
    # 再扫一遍内容里有没有授权码字样
    hit = 0
    for n in names:
        if not n.lower().endswith((".json", ".txt", ".bat", ".md")):
            continue
        try:
            data = z.read(n)
        except Exception:
            continue
        if b"SPHDL-" in data and b"license_codes" in data:
            print("    ❌ 含授权码:", n)
            hit += 1
    check("L6b 包内文本文件不含授权码", hit == 0, f"{hit} 个文件命中")
else:
    check("L6a 发布包内无敏感文件", True, "无 zip")

# 还原
U.is_frozen, U.app_dir = _orig_frozen, _orig_appdir
U.reset_for_tests()
os.environ.pop("LOCALAPPDATA", None)

print()
ok_n = sum(1 for _, p_, _ in R if p_)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p_, _ in R:
    if not p_:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
