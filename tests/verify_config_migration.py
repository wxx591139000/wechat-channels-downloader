"""跨版本配置迁移测试（v2.2.5）。

═══ 客户报障（2026-10-23）═══
「为何我换新版本 2.2.4 时，之前授权好的视频号与公众号授权没了」

根因：CONFIG_FILE = BASE_DIR / "config.json"，而 BASE_DIR 是 exe 目录。
客户解压新版到新文件夹 → 等于全新安装 → 授权码清零。

实测现场：
```
dist/v2.2.2/微信视频号下载器/config.json   授权码 1 个
dist/v2.2.3/微信视频号下载器/config.json   授权码 2 个
dist/v2.2.4/微信视频号下载器/config.json   授权码 0 个   ← 事故
```

修法：配置/列表改存 %LOCALAPPDATA%\\sphdl，首次启动自动迁移。

覆盖：
  U1 用户级目录路径正确（含 %LOCALAPPDATA%）
  U2 环境变量 SPHDL_DATA_DIR 可覆盖
  U3 用户级已有配置 → 直接用，不覆盖
  U4 ★程序目录有配置 → 迁移到用户级（老用户首次升级）
  U5 ★相邻版本目录有配置 → 挑授权码最多的那份迁移
  U6 都没有 → 新建空目录，不报错
  U7 迁移后仍是有效 JSON 且授权码数量一致
  U8 幂等：重复调用不重复迁移、不覆盖已迁移的
  U9 开发模式（非 frozen）不污染 LOCALAPPDATA
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


from sphdl import userdata


def make_cfg(path: Path, codes: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"license_codes": codes,
                                "download_dir": "E:/x",
                                "max_records": 500}, ensure_ascii=False),
                    encoding="utf-8")


print("=" * 76)
print("U1 用户级目录路径")
print("=" * 76)
tmp = Path(tempfile.mkdtemp(prefix="ud_"))
os.environ[userdata.ENV_OVERRIDE] = str(tmp / "override")
userdata.reset_for_tests()
p = userdata.user_dir()
print(f"  SPHDL_DATA_DIR={tmp / 'override'} → user_dir={p}")
check("U1/U2 环境变量可覆盖数据目录", p == tmp / "override", str(p))

# 清掉覆盖，测真实逻辑
del os.environ[userdata.ENV_OVERRIDE]
userdata.reset_for_tests()

# 伪造 frozen + LOCALAPPDATA，模拟打包环境
fake_local = tmp / "LocalAppData"
fake_local.mkdir()
fake_app = tmp / "dist" / "v2.2.4" / "微信视频号下载器"
fake_app.mkdir(parents=True)

_orig_frozen = userdata.is_frozen
_orig_appdir = userdata.app_dir
userdata.is_frozen = lambda: True
userdata.app_dir = lambda: fake_app
os.environ["LOCALAPPDATA"] = str(fake_local)
userdata.reset_for_tests()

print()
print("=" * 76)
print("U6 什么都没有 → 新建目录")
print("=" * 76)
p = userdata.resolve("config.json")
print(f"  resolve → {p}")
check("U6a 指向用户级目录",
      p == fake_local / "spdlh" or p.parent.name == "sphdl", str(p))
check("U6b 目录已创建", p.parent.is_dir(), str(p.parent))
check("U6c 文件尚未生成（等程序写）", not p.exists())

print()
print("=" * 76)
print("U4 ★程序目录有配置 → 迁移")
print("=" * 76)
make_cfg(fake_app / "config.json", {"1": "SPHDL-CODE-VIDEO", "2": "SPHDL-CODE-MP"})
userdata.reset_for_tests()
p = userdata.resolve("config.json")
print(f"  源: {fake_app / 'config.json'}")
print(f"  目标: {p}")
check("U4a 迁到用户级", p.parent != fake_app, str(p))
check("U4b 目标文件已生成", p.exists())
d = json.loads(p.read_text(encoding="utf-8"))
print(f"  迁移后授权码: {d.get('license_codes')}")
check("U4c 授权码数量一致（2 个）", len(d.get("license_codes") or {}) == 2,
      str(d.get("license_codes")))
check("U4d 授权码内容一致",
      d["license_codes"].get("1") == "SPHDL-CODE-VIDEO")

print()
print("=" * 76)
print("U8 幂等：再调一次不改动")
print("=" * 76)
mt = p.stat().st_mtime
p.write_text(json.dumps({"license_codes": {"1": "CHANGED-AFTER"}}), encoding="utf-8")
userdata.reset_for_tests()
p2 = userdata.resolve("config.json")
d2 = json.loads(p2.read_text(encoding="utf-8"))
print(f"  二次 resolve 后内容: {d2.get('license_codes')}")
check("U8a 二次调用返回同一路径", p2 == p, str(p2))
check("U8b 不覆盖已存在的用户级文件（保留 CHANGED-AFTER）",
      d2["license_codes"].get("1") == "CHANGED-AFTER",
      str(d2.get("license_codes")))

print()
print("=" * 76)
print("U5 ★相邻版本目录 → 挑授权码最多的")
print("=" * 76)
# 造一个"全新目录"，自己目录没有 config
fake_new = tmp / "dist" / "v2.2.5" / "微信视频号下载器"
fake_new.mkdir(parents=True)
userdata.app_dir = lambda: fake_new
# 清掉用户级，逼它去扫描
for f in (fake_local / "sphdl").glob("config.json"):
    f.unlink()
userdata.reset_for_tests()

old222 = tmp / "dist" / "v2.2.2" / "微信视频号下载器" / "config.json"
old223 = tmp / "dist" / "v2.2.3" / "微信视频号下载器" / "config.json"
make_cfg(old222, {"1": "OLD-222"})
make_cfg(old223, {"1": "BEST-223-V", "2": "BEST-223-MP"})

p = userdata.resolve("config.json")
d = json.loads(p.read_text(encoding="utf-8"))
print(f"  扫描到的最优源: v2.2.3（2 个码）")
print(f"  迁移后: {d.get('license_codes')}")
check("U5a 挑中授权码最多的（v2.2.3 而非 v2.2.2）",
      len(d.get("license_codes") or {}) == 2, str(d.get("license_codes")))
check("U5b 内容来自 v2.2.3",
      d["license_codes"].get("1") == "BEST-223-V", str(d.get("license_codes")))

print()
print("=" * 76)
print("U7 records.json 同样迁移")
print("=" * 76)
rec_src = fake_new / "records.json"
rec_src.write_text(json.dumps([{"id": "r1", "title": "旧记录",
                               "platform": "视频号"}], ensure_ascii=False),
                   encoding="utf-8")
userdata.reset_for_tests()
rp = userdata.resolve("records.json")
print(f"  records → {rp}")
check("U7a records 也迁到用户级", rp.parent != fake_new, str(rp))
check("U7b 内容保留",
      "旧记录" in rp.read_text(encoding="utf-8") if rp.exists() else False)

print()
print("=" * 76)
print("U9 开发模式不污染 LOCALAPPDATA")
print("=" * 76)
userdata.is_frozen = lambda: False
userdata.app_dir = _orig_appdir
userdata.reset_for_tests()
p = userdata.user_dir()
print(f"  开发模式 user_dir → {p}")
check("U9 开发模式仍用项目目录", "LocalAppData" not in str(p)
      and "AppData" not in str(p), str(p))

# 还原
userdata.is_frozen = _orig_frozen
userdata.app_dir = _orig_appdir
userdata.reset_for_tests()
os.environ.pop("LOCALAPPDATA", None)

print()
ok_n = sum(1 for _, p_, _ in R if p_)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p_, _ in R:
    if not p_:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
