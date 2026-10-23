"""分享包「默认无授权」防护测试（v2.2.7）。

═══ 用户要求（2026-10-23）═══
「后续压缩包分享给别人时，默认是不能有授权的。
  只能是对方电脑装过授权，更换新版本时继承电脑里已有的授权」

即：**授权跟着电脑走（%LOCALAPPDATA%\\sphdl\\config.json），不跟压缩包走。**

三层防护：
  ① ZIP_INCLUDE 白名单（只允许 5 项）
  ② FORBIDDEN_IN_ZIP 黑名单（无视白名单，绝对禁止）
  ③ 打完包自动校验（发现敏感文件直接报错退出）

覆盖：
  G1 黑名单认得所有敏感文件名
  G2 黑名单认得敏感后缀（.log/.db/.jsonl/.secret）
  G3 黑名单认得禁入目录（downloads/logs/data/.venv/.git）
  G4 正常文件不被误杀
  G5 ★真实 v2.2.6 zip 里没有任何敏感文件
  G6 黑名单独立于白名单（即使白名单放宽也拦得住）
  G7 授权只跟电脑走：换目录后配置路径不变
"""
import json
import sys
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


import release as REL

print("=" * 74)
print("G1/G2/G3 黑名单覆盖")
print("=" * 74)
must_block = [
    "config.json", "config.json.bak", "records.json", "records.json.bak",
    "proxy_backup.json", ".license_secret", "_lic_key.py", "_key.py",
    ".admin_token", "snippets.json", "licenses.jsonl", "license.db",
    "logs/sphdl.log", "downloads/x.mp4", "data/licenses.jsonl",
    ".venv/pyvenv.cfg", ".git/config",
    "anything.log", "sub/dir/whatever.db", "a.secret", "b.jsonl",
]
for rel in must_block:
    why = REL._zip_forbidden(PurePosixPath(rel))
    print(f"  {rel:32s} → {why or '❌ 未拦截'}")
    check(f"G1/2/3 拦截 {rel}", bool(why), why)

print()
print("=" * 74)
print("G4 正常文件不被误杀")
print("=" * 74)
must_allow = [
    "微信视频号下载器.exe", "_internal/python311.dll",
    "install-cert.bat", "使用说明.txt", "诊断-双击我.bat",
]
for rel in must_allow:
    why = REL._zip_forbidden(PurePosixPath(rel))
    print(f"  {rel:32s} → {why or '✅ 放行'}")
    check(f"G4 放行 {rel}", not why, why)

print()
print("=" * 74)
print("G5 真实 zip 检查")
print("=" * 74)
zips = sorted((ROOT / "dist").glob("*.zip"))
if not zips:
    check("G5 有可检查的 zip", False, "dist 下没有 zip")
else:
    zp = zips[-1]           # 最新一个
    print(f"  检查: {zp.name}")
    bad = []
    with zipfile.ZipFile(zp) as z:
        for n in z.namelist():
            rel = n.split("/", 1)[1] if "/" in n else n
            why = REL._zip_forbidden(PurePosixPath(rel))
            if why:
                bad.append((n, why))
    if bad:
        for n, why in bad[:8]:
            print(f"    ❌ {n}  ← {why}")
    else:
        print(f"    ✅ {len(zipfile.ZipFile(zp).namelist())} 个条目，无敏感文件")
    check("G5 zip 内无敏感文件", not bad, f"{len(bad)} 个")

print()
print("=" * 74)
print("G6 黑名单独立于白名单（白名单放宽也拦得住）")
print("=" * 74)
# 模拟：有人把 "." / "*" 加进 ZIP_INCLUDE（最危险的操作）
orig = REL.ZIP_INCLUDE.copy()
try:
    REL.ZIP_INCLUDE.add(".")
    REL.ZIP_INCLUDE.add("*")
    # 黑名单仍然要拦
    for rel in ("config.json", "records.json", "_lic_key.py"):
        why = REL._zip_forbidden(PurePosixPath(rel))
        check(f"G6 白名单放宽后仍拦 {rel}", bool(why), why or "漏了！")
finally:
    REL.ZIP_INCLUDE.clear()
    REL.ZIP_INCLUDE.update(orig)

print()
print("=" * 74)
print("G7 授权只跟电脑走（换版本目录不影响）")
print("=" * 74)
from sphdl import userdata

_orig_frozen, _orig_appdir = userdata.is_frozen, userdata.app_dir
try:
    userdata.is_frozen = lambda: True
    import os
    import tempfile
    fake_local = Path(tempfile.mkdtemp(prefix="gl_")) / "LocalAppData"
    fake_local.mkdir()
    os.environ["LOCALAPPDATA"] = str(fake_local)
    d1 = Path(tempfile.mkdtemp()) / "dist" / "v2.2.6" / "微信视频号下载器"
    d1.mkdir(parents=True)
    userdata.app_dir = lambda: d1
    userdata.reset_for_tests()
    p1 = userdata.resolve("config.json")

    # 换到另一个版本目录
    d2 = Path(tempfile.mkdtemp()) / "dist" / "v2.2.7" / "微信视频号下载器"
    d2.mkdir(parents=True)
    userdata.app_dir = lambda: d2
    userdata.reset_for_tests()
    p2 = userdata.resolve("config.json")

    print(f"  v2.2.6 目录 → {p1}")
    print(f"  v2.2.7 目录 → {p2}")
    check("G7a 两个版本目录解析到同一个用户级路径", p1 == p2, f"{p1} vs {p2}")
    check("G7b 路径在 LOCALAPPDATA 下",
          "LocalAppData" in str(p1) or "sphdl" in str(p1), str(p1))
finally:
    userdata.is_frozen, userdata.app_dir = _orig_frozen, _orig_appdir
    userdata.reset_for_tests()
    os.environ.pop("LOCALAPPDATA", None)

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
