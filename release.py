#!/usr/bin/env python3
"""
sphdl 发布脚本：自动升版本（三位语义化 X.Y.Z）→ 构建冻结包 → 生成分享版 zip。

用法（在 sphdl/ 下，用 venv 的 python 运行）：
    python release.py                 # 自动：自上个 tag 有变更（提交或工作区改动）
                                      #       则 patch+1；构建 dist/vX.Y.Z + 分享 zip
    python release.py --major         # 手动升大版本（X+1.0.0）
    python release.py --minor         # 手动升中版本（X.Y+1.0）
    python release.py --patch         # 手动升补丁版本（X.Y.Z+1）
    python release.py --no-bump       # 不升版本（同版本产物已存在则拒绝，--force 覆盖）
    python release.py --note "说明"   # 在 CHANGELOG 新版本节追加一行
    python release.py --no-commit     # 构建/打包后不自动 git 提交 + 打 tag

列表容量档位（v2.0.17）—— 两条构建通道：

  A. 通用版（推荐，一个包打天下）：未授权默认档 + 授权码解锁
     python release.py                      # 未授权默认档 500 条
     python release.py --default-records 100 # 试用版：未授权默认档 100 条
     客户在界面点「🔑 授权管理」粘贴你发的码 → 解锁 500/1000/2000/20000。
     授权码用 tools/license_tool.py gen <档位> 生成（密钥 .license_secret 不入库）。

  B. 预置版（档位写死，界面无授权入口，按需发不同版本）
     python release.py --max-records 500        # 500 条版
     python release.py --max-records 1000       # 1000 条版
     python release.py --max-records 2000       # 2000 条版
     python release.py --max-records 20000      # 旗舰版（2 万条）
     python release.py --all-editions           # 一次性构建上述 4 个版本
     说明：输出 dist/vX.Y.Z/微信视频号下载器-<档位>/ 与
           dist/微信视频号下载器-vX.Y.Z-<档位>-win64-portable.zip；
           预置版默认不自动提交/打 tag（加 --commit 可强制）。

  构建期：从仓库根 .license_secret 注入 sphdl/_lic_key.py（不入库，构建后自动清理）；
          缺密钥时中止（--no-license 可构建无授权功能的包）。
          构建后自动跑 exe --selftest-license 验证密钥已注入（防"发出去所有码都无效"）。

行为约定：
  - 版本单一事实源 sphdl/__init__.py 的 __version__，恒为三位 X.Y.Z
  - 有变化（自上个 tag 有提交或工作区有改动）→ 自动升补丁号
  - 保留旧版本：dist/vX.Y.Z/ 与 dist/*-win64-portable.zip 一律不删；
    目标版本产物已存在则拒绝（--force 才覆盖）
  - 分享版 zip 默认放 dist/，内容白名单（exe + _internal + install-cert.bat
    + 使用说明.txt），绝不含 config/records/logs/downloads 等本机数据
  - 构建后 PII 扫描（本机路径/用户名/邮箱/手机号），命中即中止
"""
import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent          # sphdl/（git 仓库根）
PKG = ROOT / "sphdl" / "__init__.py"            # 版本单一事实源
SPEC = ROOT / "微信视频号下载器.spec"
CHANGELOG = ROOT / "CHANGELOG.md"
DIST = ROOT / "dist"
WORK = ROOT / "build_tmp_release"
APP_DIR_NAME = "微信视频号下载器"
ZIP_PREFIX = f"{APP_DIR_NAME}-v"
ZIP_SUFFIX = "-win64-portable.zip"

# 分享 zip 白名单：只打包这些内容（其余一律不带，防本机数据泄漏）
ZIP_INCLUDE = {"微信视频号下载器.exe", "_internal", "install-cert.bat", "使用说明.txt"}

# 列表容量锁档（v2.0.17）：档位 → 产物名后缀；键与 sphdl.config.MAX_RECORDS_TIERS 一致
EDITION_SUFFIX = {500: "轻量版", 1000: "标准版", 2000: "专业版", 20000: "旗舰版"}
EDITION_CLI = {"500": 500, "1000": 1000, "2000": 2000, "20000": 20000,
               "2万": 20000, "旗舰": 20000, "旗舰版": 20000}
EDITION_FILE = ROOT / "sphdl" / "_edition.py"   # 构建期档位文件（构建后还原）
_LIC_NAME = ".license" + "_secret"   # 授权密钥文件名（不入库）
LIC_KEY_SRC = ROOT / _LIC_NAME        # 授权密钥路径
LIC_KEY_FILE = ROOT / "sphdl" / "_lic_key.py"   # 构建期注入的校验密钥（构建后清理）

# PII 扫描：本机路径/用户名命中即中止（只匹配真实用户数据，避免第三方
# 二进制内嵌的 CI 构建路径如 C:\Users\runneradmin 造成误报）；
# 邮箱/手机命中仅在文本类文件中打印警告（限量，二进制随机字节极易误报）
PII_ABORT_PATTERNS = [
    b"Dancing", b"myHermesWorkSpace", b"ObsidianHouse",
    b"C:\\Users\\Dancing", "E:\\视频号下载".encode("utf-8"),
]
PII_WARN_PATTERNS = [
    (re.compile(rb"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "邮箱"),
    (re.compile(rb"1[3-9]\d{9}"), "11 位手机号"),
]
TEXT_SUFFIXES = {".txt", ".md", ".log", ".json", ".py", ".bat", ".tcl", ".msg",
                 ".tm", ".dat", ".eps", ".pem", ".toml", ".cfg", ".ini",
                 ".yaml", ".yml", ".enc"}
WARN_LIMIT = 15
VERSION_RE = re.compile(r'__version__\s*=\s*"(\d+)\.(\d+)\.(\d+)"')


def log(msg):
    print(msg, flush=True)


def run(cmd, **kw):
    log("  $ " + " ".join(str(c) for c in cmd))
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def git(*args):
    return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                          text=True)


def read_version() -> tuple:
    m = VERSION_RE.search(PKG.read_text(encoding="utf-8"))
    if not m:
        sys.exit(f"❌ 无法从 {PKG} 解析 __version__")
    return tuple(int(x) for x in m.groups())


def write_version(v: tuple):
    text = PKG.read_text(encoding="utf-8")
    PKG.write_text(VERSION_RE.sub(f'__version__ = "{v[0]}.{v[1]}.{v[2]}"',
                                  text, count=1), encoding="utf-8")


def bump(v: tuple, level: str) -> tuple:
    major, minor, patch = v
    if level == "major":
        return major + 1, 0, 0
    if level == "minor":
        return major, minor + 1, 0
    return major, minor, patch + 1


def last_tag() -> str:
    r = git("describe", "--tags", "--abbrev=0")
    return r.stdout.strip() if r.returncode == 0 else ""


def has_changes_since(tag: str) -> bool:
    """自上个 tag 有提交，或工作区有（非产物类）改动 → 视为有变化。"""
    if tag:
        r = git("rev-list", f"{tag}..HEAD")
        if r.returncode == 0 and r.stdout.strip():
            return True
    r = git("status", "--porcelain")
    if r.returncode != 0:
        return True
    ignore = re.compile(
        r"^\?\?\s+(build|build_|dist|dist_|dist/|tests/.*\.mp4|usage_instructions_v12[35])")
    for line in r.stdout.splitlines():
        if line.startswith("!!"):      # ignored
            continue
        if line.startswith("??") and ignore.match(line):
            continue                   # 构建/产物类 untracked 不算变化
        return True
    return False


def ensure_changelog_section(v: tuple, note: str = ""):
    """确保 CHANGELOG 存在 [X.Y.Z] 节；把「待发版」头规范化；追加 note。"""
    text = CHANGELOG.read_text(encoding="utf-8")
    header = f"## [{v[0]}.{v[1]}.{v[2]}] - {date.today():%Y-%m-%d}"
    lines = text.split("\n")
    idx = next((i for i, ln in enumerate(lines) if ln.startswith("## [")), None)
    existing = next((i for i, ln in enumerate(lines)
                     if ln.startswith(f"## [{v[0]}.{v[1]}.{v[2]}]")), None)
    if existing is not None:
        # 规范化「待发版」头 → 正式版头
        if "待发版" in lines[existing]:
            lines[existing] = header
    else:
        insert_at = idx if idx is not None else 2
        lines[insert_at:insert_at] = ["", header, ""]
    if note:
        anchor = existing if existing is not None else (
            next(i for i, ln in enumerate(lines) if ln.startswith(f"## [{v[0]}.{v[1]}.{v[2]}]")))
        # 在节头后的第一个空行处插入 bullet
        j = anchor + 1
        while j < len(lines) and lines[j] == "":
            j += 1
        lines.insert(j, f"- {note}")
    CHANGELOG.write_text("\n".join(lines), encoding="utf-8")


def build_frozen(v: tuple, force: bool, edition_sfx: str = ""):
    out_dir = DIST / f"v{v[0]}.{v[1]}.{v[2]}" / f"{APP_DIR_NAME}{edition_sfx}"
    if out_dir.exists() and not force:
        sys.exit(f"❌ {out_dir} 已存在（保留旧版本）。确认要覆盖请加 --force")
    log(f"📦 PyInstaller 构建 → {out_dir}")
    if WORK.exists():
        shutil.rmtree(WORK)
    run([sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm",
         "--distpath", str(DIST / f"v{v[0]}.{v[1]}.{v[2]}"),
         "--workpath", str(WORK)])
    return out_dir


def verify_frozen(out_dir: Path):
    exe = out_dir / "微信视频号下载器.exe"
    log(f"🧪 冻结自检: {exe} --selftest-threaded")
    subprocess.run([str(exe), "--selftest-threaded"], timeout=90)
    logf = out_dir / "logs" / "sphdl.log"
    if not logf.exists():
        sys.exit("❌ 冻结自检未生成日志")
    tail = logf.read_text(encoding="utf-8", errors="replace")
    if "线程自检通过" not in tail:
        sys.exit("❌ 冻结自检未通过（日志未见「线程自检通过」）")
    log("  ✅ 冻结自检通过")


def stage_usage_instructions(out_dir: Path, v: tuple):
    """把最新 usage_instructions_v*.txt 拷为 使用说明.txt，并更新版本号。"""
    cands = sorted(ROOT.glob("usage_instructions_v*.txt"))
    if not cands:
        return
    text = cands[-1].read_text(encoding="utf-8")
    text = re.sub(r"v\d+\.\d+\.\d+", f"v{v[0]}.{v[1]}.{v[2]}", text)
    (out_dir / "使用说明.txt").write_text(text, encoding="utf-8")


def pii_scan(out_dir: Path):
    log("🕵️  PII 扫描（分享包白名单内容）")
    hits = []
    warns = []
    for p in out_dir.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(out_dir)
        if rel.parts and rel.parts[0] not in ZIP_INCLUDE:
            continue
        data = p.read_bytes()
        for pat in PII_ABORT_PATTERNS:
            if pat in data:
                hits.append((str(rel), pat))
        if p.suffix.lower() in TEXT_SUFFIXES:
            for rx, name in PII_WARN_PATTERNS:
                if rx.search(data):
                    warns.append((str(rel), name))
    shown = 0
    for rel, pat in warns:
        if shown < WARN_LIMIT:
            log(f"  ⚠️ 警告（{pat}）: {rel}")
            shown += 1
    if len(warns) > shown:
        log(f"  …另有 {len(warns) - shown} 条警告（文本类文件，非阻断）")
    if hits:
        for rel, pat in hits:
            log(f"  ❌ 命中: {rel}  {pat}")
        sys.exit("❌ PII 扫描中止：分享包内含本机路径/用户名")
    log("  ✅ PII 扫描通过")


def make_share_zip(v: tuple, out_dir: Path, force: bool, edition_sfx: str = ""):
    zip_path = DIST / (f"{ZIP_PREFIX}{v[0]}.{v[1]}.{v[2]}{edition_sfx}{ZIP_SUFFIX}")
    if zip_path.exists() and not force:
        sys.exit(f"❌ {zip_path} 已存在（保留旧版本）。确认要覆盖请加 --force")
    log(f"🗜️  分享版 zip → {zip_path}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in out_dir.rglob("*"):
            rel = p.relative_to(out_dir)
            if not p.is_file():
                continue
            if rel.parts[0] not in ZIP_INCLUDE:
                continue
            z.write(p, arcname=f"{out_dir.name}/{rel.as_posix()}")
    sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    log(f"  ✅ zip 完成: {zip_path.stat().st_size / 1048576:.1f} MB")
    log(f"  sha256: {sha}")
    return zip_path


def inject_license_key() -> bool:
    """把 .license_secret 混淆后写入 sphdl/_lic_key.py；返回是否注入成功。"""
    if not LIC_KEY_SRC.exists():
        return False
    try:
        secret = bytes.fromhex(LIC_KEY_SRC.read_text(encoding="utf-8").strip())
    except Exception as e:
        sys.exit(f"❌ {LIC_KEY_SRC} 内容非法（应为 hex）: {e}")
    if len(secret) < 16:
        sys.exit(f"❌ {LIC_KEY_SRC} 太短（≥16 字节，建议 32 字节）")
    sys.path.insert(0, str(ROOT))
    from sphdl import license as lic
    blob = lic.obfuscate(secret)
    LIC_KEY_FILE.write_text(
        '"""构建期注入的授权校验密钥（不入库；release.py 由 .license_secret 生成）。"""\n\n'
        "K = bytes([" + ", ".join(str(b) for b in blob) + "])\n",
        encoding="utf-8")
    log(f"🔑 授权密钥已注入 → {LIC_KEY_FILE.relative_to(ROOT)}")
    return True


def cleanup_license_key():
    """删除构建期注入的校验密钥（仓库里不能留）。"""
    if LIC_KEY_FILE.exists():
        LIC_KEY_FILE.unlink()
        log(f"🧹 已清理 {LIC_KEY_FILE.relative_to(ROOT)}")


def lic_label(n) -> str:
    """档位展示名（与 config.MAX_RECORDS_LABELS 一致：轻量版/标准版/专业版/旗舰版）。"""
    from sphdl import config as _cfg
    return _cfg.label_of(500 if n is None else n)


def verify_license_in_frozen(out_dir: Path, expect: bool = True):
    """构建后验证：冻结包内授权密钥已注入（否则发出去的包所有授权码都无效）。"""
    exe = out_dir / "微信视频号下载器.exe"
    log("🧪 授权自检: exe --selftest-license")
    r = subprocess.run([str(exe), "--selftest-license"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    out = (r.stdout or "") + (r.stderr or "")
    if not expect:
        log("  ℹ️  --no-license 构建，跳过授权自检")
        return
    if "ALL PASS" not in out:
        sys.exit(f"❌ 冻结包授权自检未通过（密钥未注入或实现异常）：\n{out[-800:]}")
    log("  ✅ 授权自检通过（密钥已注入，4 档往返一致）")


def write_edition_lock(locked, default_records=None):
    """构建前写入 sphdl/_edition.py。

    locked=None → 未锁档（档位由授权码决定）；default_records → 未授权默认档。
    返回原文本以便还原。
    """
    original = EDITION_FILE.read_text(encoding="utf-8")
    body = ('"""构建期档位配置（由 release.py 生成；仓库默认为未锁档 + 默认 500）。"""\n\n'
            f"LOCKED_MAX_RECORDS = {'None' if locked is None else int(locked)}\n"
            "DEFAULT_MAX_RECORDS_OVERRIDE = "
            f"{'None' if default_records is None else int(default_records)}\n")
    EDITION_FILE.write_text(body, encoding="utf-8")
    return original


def restore_edition(original: str):
    EDITION_FILE.write_text(original, encoding="utf-8")


def build_one_edition(v: tuple, force: bool, locked, args,
                      default_records=None) -> Path:
    """构建一个版本。

    locked=None → 通用版：未授权默认档 default_records（默认 500），
                  客户端凭授权码解锁 500/1000/2000/20000
    locked=N    → 预置版：档位写死 N，界面隐藏授权入口
    """
    sfx = "" if locked is None else "-" + EDITION_SUFFIX[locked]
    tag_txt = (f"通用版（授权码解锁；未授权默认档 {lic_label(default_records)}）"
               if locked is None else f"{EDITION_SUFFIX[locked]}（预置锁档）")
    log(f"\n{'=' * 62}\n🏷️  构建: {tag_txt}\n{'=' * 62}")
    if not args.no_license and not LIC_KEY_SRC.exists():
        sys.exit("❌ 未找到 .license_secret —— 授权码功能将不可用。\n"
                 "   先运行: python tools/license_tool.py init\n"
                 "   或加 --no-license 构建一个不含授权功能的包")
    original = write_edition_lock(locked, default_records)
    try:
        if not args.no_license:
            inject_license_key()
        out_dir = build_frozen(v, force, sfx)
        verify_frozen(out_dir)
        verify_license_in_frozen(out_dir, expect=not args.no_license)
        stage_usage_instructions(out_dir, v)
        pii_scan(out_dir)
        zip_path = make_share_zip(v, out_dir, force, sfx)
    finally:
        restore_edition(original)
        cleanup_license_key()
    log(f"  ✅ 完成: {tag_txt} → {zip_path.name}")
    return zip_path


def commit_and_tag(v: tuple, note: str):
    ver = f"{v[0]}.{v[1]}.{v[2]}"
    run(["git", "add", "-u"])
    run(["git", "add", str(PKG), str(CHANGELOG), str(ROOT / "release.py"),
         str(ROOT / ".gitignore"), str(ROOT / "README.md")])
    if git("diff", "--cached", "--quiet").returncode == 0:
        log("ℹ️  无暂存变更，跳过提交")
    else:
        msg = f"release v{ver}"
        if note:
            msg += f": {note}"
        run(["git", "commit", "-m", msg])
    # tag 必须指向包含本次版本号的提交：已存在（可能指向旧提交）则删除重建
    if git("tag", "-l", f"v{ver}").stdout.strip():
        run(["git", "tag", "-d", f"v{ver}"])
    run(["git", "tag", "-a", f"v{ver}", "-m", f"v{ver}"])
    log(f"  ✅ 已提交并打 tag v{ver}")


def main():
    ap = argparse.ArgumentParser(description="sphdl 自动发布脚本")
    ap.add_argument("--major", action="store_true")
    ap.add_argument("--minor", action="store_true")
    ap.add_argument("--patch", action="store_true")
    ap.add_argument("--no-bump", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-commit", action="store_true")
    ap.add_argument("--note", default="")
    ap.add_argument("--max-records", default=None, choices=sorted(EDITION_CLI),
                    help="预置锁档构建：档位写死（500/1000/2000/20000）")
    ap.add_argument("--default-records", default=None,
                    help="通用版未授权默认档位（如 100 做试用版；默认 500）")
    ap.add_argument("--no-license", action="store_true",
                    help="不注入授权密钥（构建无授权功能的包）")
    ap.add_argument("--all-editions", action="store_true",
                    help="一次性构建 500/1000/2000/无限 四个锁档版本")
    ap.add_argument("--commit", action="store_true",
                    help="锁档构建时也执行 git 提交 + 打 tag")
    args = ap.parse_args()

    level = "patch"
    if args.major:
        level = "major"
    elif args.minor:
        level = "minor"
    elif args.patch:
        level = "patch"

    ver = read_version()
    tag = last_tag()
    changes = has_changes_since(tag)
    log(f"当前版本: {ver[0]}.{ver[1]}.{ver[2]} | 上个 tag: {tag or '(无)'} "
        f"| 有变化: {changes}")
    if args.no_bump:
        new = ver
    elif args.major or args.minor or args.patch:
        new = bump(ver, level)
    elif changes:
        new = bump(ver, "patch")
        log(f"🔢 检测到变化，自动升补丁版 → {new[0]}.{new[1]}.{new[2]}")
    else:
        new = ver

    if new != ver:
        write_version(new)
        log(f"✏️  __version__: {ver[0]}.{ver[1]}.{ver[2]} → "
            f"{new[0]}.{new[1]}.{new[2]}")
    ensure_changelog_section(new, args.note.strip())

    # 档位版本构建（v2.0.17）
    #   --all-editions / --max-records → 预置锁档的版本（默认不提交/打 tag）
    #   无参数 → 通用版（授权码解锁；未授权默认档 --default-records 或 500）
    if args.all_editions or args.max_records is not None:
        locked_list = ([500, 1000, 2000, 20000] if args.all_editions
                       else [EDITION_CLI[args.max_records]])
        zips = [build_one_edition(new, args.force, locked, args)
                for locked in locked_list]
        if args.commit and not args.no_commit:
            commit_and_tag(new, args.note.strip())
        log(f"\n🎉 档位版本构建完成: v{new[0]}.{new[1]}.{new[2]} 共 {len(zips)} 个")
        for zp in zips:
            log(f"  · {zp.relative_to(ROOT)}")
        return

    default_records = (None if args.default_records is None
                       else EDITION_CLI.get(str(args.default_records).lower(),
                                            args.default_records))
    zip_path = build_one_edition(new, args.force, None, args, default_records)

    if not args.no_commit:
        commit_and_tag(new, args.note.strip())
    log(f"\n🎉 发布完成: v{new[0]}.{new[1]}.{new[2]}")
    log(f"  构建: dist/v{new[0]}.{new[1]}.{new[2]}/")
    log(f"  分享: dist/{ZIP_PREFIX}{new[0]}.{new[1]}.{new[2]}{ZIP_SUFFIX}")


if __name__ == "__main__":
    main()
