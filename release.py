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


def build_frozen(v: tuple, force: bool):
    out_dir = DIST / f"v{v[0]}.{v[1]}.{v[2]}" / APP_DIR_NAME
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


def make_share_zip(v: tuple, out_dir: Path, force: bool):
    zip_path = DIST / f"{ZIP_PREFIX}{v[0]}.{v[1]}.{v[2]}{ZIP_SUFFIX}"
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
            z.write(p, arcname=f"{APP_DIR_NAME}/{rel.as_posix()}")
    sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    log(f"  ✅ zip 完成: {zip_path.stat().st_size / 1048576:.1f} MB")
    log(f"  sha256: {sha}")
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

    out_dir = build_frozen(new, args.force)
    verify_frozen(out_dir)
    stage_usage_instructions(out_dir, new)
    pii_scan(out_dir)
    make_share_zip(new, out_dir, args.force)

    if not args.no_commit:
        commit_and_tag(new, args.note.strip())
    log(f"\n🎉 发布完成: v{new[0]}.{new[1]}.{new[2]}")
    log(f"  构建: dist/v{new[0]}.{new[1]}.{new[2]}/")
    log(f"  分享: dist/{ZIP_PREFIX}{new[0]}.{new[1]}.{new[2]}{ZIP_SUFFIX}")


if __name__ == "__main__":
    main()
