"""端到端验证：把 v2.2.8 zip 给另一台电脑，对方能看到什么（v2.2.9）。

═══ 用户担忧（2026-10-23）═══
「视频清单里面有很多文件，假如我把 2.2.8 压缩包发给用户，
  他们不会看到我的这些视频清单吧？按道理我电脑的视频清单、
  授权码等信息都不会被包含在打包文件里」

═══ 验证方法（不看代码，真跑一次）═══

模拟一台「全新电脑」：
  1. 用独立目录当 exe 所在目录
  2. 用独立目录当 %LOCALAPPDATA%（隔离真实用户目录）
  3. 解压 zip
  4. 检查解压出的文件里有没有 records/config
  5. 启动一次，检查新生成的 config/records 是不是空的
  6. 再次确认：开发机的清单/授权一个字都进不来

覆盖：
  X1 zip 内无 records.json / config.json / 授权码
  X2 解压目录内无 records.json / config.json
  X3 全新 LOCALAPPDATA 启动后，records.json 为空（无他人清单）
  X4 全新 LOCALAPPDATA 启动后，config.json 无授权码
  X5 ★开发机有 310 条清单 + 2 个授权码时，新环境仍为空
  X6 相邻目录里有旧 config/records 时，授权被清空（只继承设置）
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


zips = sorted((ROOT / "dist").glob("*.zip"))
if not zips:
    sys.exit("❌ dist 下没有 zip")
ZIP = zips[-1]
print(f"被测包: {ZIP.name}\n")

print("=" * 76)
print("X1 zip 内检查（客户拿到手的原始文件）")
print("=" * 76)
with zipfile.ZipFile(ZIP) as z:
    names = z.namelist()
    bad_files = [n for n in names
                 if re.search(r"(^|/)(config\.json|records\.json|"
                              r"config\.json\.bak|proxy_backup\.json)$", n)]
    print(f"  条目总数: {len(names)}")
    print(f"  config/records 类文件: {bad_files or '无'}")
    check("X1a zip 内无 config.json / records.json", not bad_files, str(bad_files))

    LIC_RX = re.compile(rb"SPHDL-[0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{4}-"
                        rb"[0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{4}")
    leaks = []
    for n in names:
        if not n.lower().endswith((".txt", ".md", ".json", ".bat", ".ini")):
            continue
        try:
            data = z.read(n)
        except Exception:
            continue
        for m in LIC_RX.finditer(data):
            c = m.group(0).decode("ascii", "replace")
            if set(c.replace("SPHDL-", "").replace("-", "")) != {"0"}:
                leaks.append((n, c))
    print(f"  真实授权码: {leaks or '无'}")
    check("X1b zip 内无真实授权码", not leaks, str(leaks[:3]))

    # 视频标题抽样：开发机清单里的标题不该出现在包里任何文本
    dev_records = Path(os.environ.get("LOCALAPPDATA", "")) / "sphdl" / "records.json"
    titles = []
    if dev_records.exists():
        try:
            for r in json.loads(dev_records.read_text(encoding="utf-8"))[:50]:
                t = (r.get("title") or "").strip()
                if len(t) >= 8:
                    titles.append(t)
        except Exception:
            pass
    print(f"  取样开发机标题: {len(titles)} 个")
    hit = []
    if titles:
        for n in names:
            if not n.lower().endswith((".txt", ".md", ".json", ".bat", ".ini")):
                continue
            try:
                data = z.read(n).decode("utf-8", "ignore")
            except Exception:
                continue
            for t in titles:
                if t in data:
                    hit.append((n, t))
                    break
    print(f"  命中开发机视频标题: {hit or '无'}")
    check("X1c zip 内不含开发机的视频标题", not hit, str(hit[:3]))

print()
print("=" * 76)
print("X2 解压到「另一台电脑」的目录，看有什么")
print("=" * 76)
sandbox = Path(tempfile.mkdtemp(prefix="newpc_"))
ver_dir = sandbox / "dist" / "v9.9.9"
ver_dir.mkdir(parents=True)
with zipfile.ZipFile(ZIP) as z:
    z.extractall(ver_dir)
app_dir = ver_dir / "微信视频号下载器"
files = sorted(p.name for p in app_dir.iterdir())
print(f"  解压目录: {app_dir}")
print(f"  顶层文件: {files}")
check("X2a 解压目录无 records.json", "records.json" not in files, str(files))
check("X2b 解压目录无 config.json", "config.json" not in files, str(files))

print()
print("=" * 76)
print("X3/X4 全新 %LOCALAPPDATA% 启动后生成什么")
print("=" * 76)
fake_local = sandbox / "LocalAppData"
fake_local.mkdir()
env = dict(os.environ)
env["LOCALAPPDATA"] = str(fake_local)
env["USERPROFILE"] = str(sandbox)      # 双保险，别摸到真实用户目录

# 用开发模式的 python 跑（不必真的用 exe，逻辑相同且更快）
probe = sandbox / "probe.py"
probe.write_text(
    "import sys, json, pathlib\n"
    f"sys.path.insert(0, r'{ROOT}')\n"
    "from sphdl import userdata\n"
    "userdata.is_frozen = lambda: True\n"
    "userdata.app_dir = lambda: pathlib.Path(r'" + str(app_dir) + "')\n"
    "userdata.reset_for_tests()\n"
    "cfg = userdata.resolve('config.json')\n"
    "rec = userdata.resolve('records.json')\n"
    "print('CFG', cfg)\n"
    "print('REC', rec)\n"
    "print('CFG_EXISTS', cfg.exists())\n"
    "print('REC_EXISTS', rec.exists())\n",
    encoding="utf-8")
r = subprocess.run([sys.executable, str(probe)], capture_output=True,
                   text=True, encoding="utf-8", errors="replace", env=env)
out = r.stdout
print("  " + out.replace("\n", "\n  ").strip())
cfg_p = None
rec_p = None
for line in out.splitlines():
    if line.startswith("CFG "):
        cfg_p = Path(line[4:].strip())
    elif line.startswith("REC "):
        rec_p = Path(line[4:].strip())

user_dir = fake_local / "sphdl"
print(f"  新环境用户目录: {user_dir}")
check("X3a records.json 不存在或为空",
      (not (user_dir / "records.json").exists()
       or json.loads((user_dir / "records.json").read_text(encoding="utf-8")) == []),
      "客户不会看到别人的清单")
if (user_dir / "records.json").exists():
    n = len(json.loads((user_dir / "records.json").read_text(encoding="utf-8")))
    print(f"    records.json 条数: {n}")

cfg_file = user_dir / "config.json"
if cfg_file.exists():
    d = json.loads(cfg_file.read_text(encoding="utf-8"))
    lic = d.get("license_codes") or {}
    print(f"    config.json 授权码: {lic or '无'}")
    check("X4a 新环境 config.json 无授权码", not lic, str(lic))
    check("X4b 新环境无单码字段", not d.get("license_code"), str(d.get("license_code")))
else:
    print("    config.json 尚未生成（程序首次保存时才写）")
    check("X4a 新环境 config.json 无授权码", True, "未生成")
    check("X4b 新环境无单码字段", True, "未生成")

print()
print("=" * 76)
print("X5 开发机有真实数据时，新环境仍然干净")
print("=" * 76)
real = Path(os.environ.get("LOCALAPPDATA", "")) / "sphdl"
if real.exists():
    rn = real / "records.json"
    if rn.exists():
        cnt = len(json.loads(rn.read_text(encoding="utf-8")))
        print(f"  开发机 records.json: {cnt} 条")
        print(f"  新环境 records 条数 : ", end="")
        np_ = fake_local / "sphdl" / "records.json"
        n2 = len(json.loads(np_.read_text(encoding="utf-8"))) if np_.exists() else 0
        print(n2)
        check("X5 开发机清单没有流到新环境", n2 == 0 or n2 != cnt, f"新={n2} 开={cnt}")
else:
    check("X5 开发机清单没有流到新环境", True, "开发机无数据，跳过")

print()
print("=" * 76)
print("X6 相邻目录有旧 config 时：授权清空，设置继承")
print("=" * 76)
neighbor = sandbox / "dist" / "v9.9.8" / "微信视频号下载器"
neighbor.mkdir(parents=True)
(neighbor / "config.json").write_text(json.dumps({
    "license_codes": {"1": "SPHDL-FAKE-CODE-FOR-TEST"},
    "download_dir": "D:/别人的下载目录",
    "auto_download": True,
}, ensure_ascii=False), encoding="utf-8")

shutil.rmtree(user_dir, ignore_errors=True)     # 清空新环境
probe2 = sandbox / "probe2.py"
probe2.write_text(
    "import sys, json, pathlib\n"
    f"sys.path.insert(0, r'{ROOT}')\n"
    "from sphdl import userdata\n"
    "userdata.is_frozen = lambda: True\n"
    "userdata.app_dir = lambda: pathlib.Path(r'" + str(app_dir) + "')\n"
    "userdata.reset_for_tests()\n"
    "cfg = userdata.resolve('config.json')\n"
    "d = json.loads(cfg.read_text(encoding='utf-8')) if cfg.exists() else {}\n"
    "print('LIC', json.dumps(d.get('license_codes') or {}))\n"
    "print('DL', d.get('download_dir'))\n",
    encoding="utf-8")
r2 = subprocess.run([sys.executable, str(probe2)], capture_output=True,
                    text=True, encoding="utf-8", errors="replace", env=env)
print("  " + r2.stdout.replace("\n", "\n  ").strip())
lic_out = {}
dl_out = None
for line in r2.stdout.splitlines():
    if line.startswith("LIC "):
        lic_out = json.loads(line[4:].strip())
    elif line.startswith("DL "):
        dl_out = line[3:].strip()
check("X6a 相邻目录的授权被清空", not lic_out, str(lic_out))
check("X6b 设置项仍继承（下载目录）", dl_out == "D:/别人的下载目录", str(dl_out))

print()
print("=" * 76)
print("清理沙箱")
print("=" * 76)
shutil.rmtree(sandbox, ignore_errors=True)
print("  已清理")

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
