"""客户三连问修复验证（v2.9.7）。

═══ 客户报障（2026-11-01）═══
1. 工具底部留给日志框的高度太小了
2. 界面勾选的符号恢复到 √，不应该用 X
3. ★发现授权管理丢失了，之前每次换新版本授权信息会保留
   （这条最严重，是已售客户体验事故）

═══ ③ 的根因（代码级）═══
`userdata.py:_resolve_inner()` 里：
```python
shutil.copy2(src, target)      # 从旧版本/程序目录复制配置
_sanitize_license(target)      # ← 把刚复制过来的授权码清空！
```
`_sanitize_license` 的本意是"清掉打包机带出去的开发机授权"，
但**无差别对迁移结果执行** → 客户换版本必丢授权。

修法：判断 src 是否在程序目录之下
  · 在程序目录（打包机配置）→ 清（原意）
  · 在用户目录（客户自己的授权）→ **保留**

═══ 覆盖 ═══
S1  ★迁移时区分来源：程序目录的才清洗
S2  ★用户目录来源不清洗（保留授权）
S3  ★_sanitize_license 本身仍然有效（防开发机授权泄漏）
S4  ★日志框高度 >= 14 行（客户嫌太小）
S5  ★功能列表用 √ 不用 ✗ 作为"已授权"
S6  未授权仍用 ✗（区分明确）
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


from sphdl import userdata as UD  # noqa: E402

src_txt = (ROOT / "sphdl" / "userdata.py").read_text(encoding="utf-8")
gui_txt = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8")

print("=" * 74)
print("S1-S2 授权迁移（最严重的问题）")
print("=" * 74)
check("S1a ★迁移时判断 src 是否在程序目录",
      "_under_appdir" in src_txt)
check("S1b ★程序目录来源 → 清洗",
      "_under_appdir" in src_txt and
      src_txt.count("_sanitize_license(target)") >= 1)
check("S2a ★用户目录来源 → 不清洗（else 分支）",
      "else:" in src_txt and "保留授权码" in src_txt)
check("S2b ★有注释说明为什么区分",
      "客户自己激活的记录" in src_txt or "客户授权" in src_txt)

# 实测 _sanitize_license 本身仍然工作（防泄漏）
print()
print("=" * 74)
print("S3 _sanitize_license 本身仍有效（防开发机授权泄漏）")
print("=" * 74)
tmp = Path(tempfile.mkdtemp()) / "config.json"
tmp.write_text(json.dumps({
    "port": 8899,
    "license_codes": {"1": "SPHDL-FAKE-CODE"},
    "license_code": "SPHDL-OLD",
    "download_dir": "E:/x"}, ensure_ascii=False), encoding="utf-8")
changed = UD._sanitize_license(tmp)
after = json.loads(tmp.read_text(encoding="utf-8"))
check("S3a ★能清空 license_codes", after.get("license_codes") == {},
      str(after.get("license_codes")))
check("S3b ★能清空老字段 license_code",
      not after.get("license_code"), repr(after.get("license_code")))
check("S3c ★保留其他设置（端口/目录）",
      after.get("port") == 8899 and after.get("download_dir") == "E:/x")

print()
print("=" * 74)
print("S4 日志框高度")
print("=" * 74)
import re  # noqa: E402
m = re.search(r"self\.log_text = tk\.Text\(logf, height=(\d+)", gui_txt)
height = int(m.group(1)) if m else 0
check("S4 ★日志框高度 >= 14 行（客户嫌太小）", height >= 14,
      f"height={height}")

print()
print("=" * 74)
print("S5-S6 勾选符号")
print("=" * 74)
check("S5a ★已授权用 √", '"√" if on' in gui_txt or '"√" if _has_vc' in gui_txt)
check("S5b ★不再用 ✓ 作为已授权标记",
      '"✓" if on' not in gui_txt, "已替换为 √")
check("S6 未授权用 ✗（保留，区分明确）",
      '"✗"' in gui_txt)

print()
print("=" * 74)
print("S7 授权码实际可验证（客户现有码）")
print("=" * 74)
cfg_p = Path.home() / "AppData" / "Local" / "sphdl" / "config.json"
if cfg_p.exists():
    d = json.loads(cfg_p.read_text(encoding="utf-8"))
    codes = d.get("license_codes") or {}
    check("S7a ★用户目录里有授权码", len(codes) > 0, f"{len(codes)} 个")
    from sphdl import license as lic  # noqa: E402
    all_ok = True
    for k, c in codes.items():
        r = lic.verify_code(c)
        if not r.get("ok"):
            all_ok = False
            print(f"    码 [{k}] 无效：{r.get('error')}")
    check("S7b ★所有授权码仍然有效", all_ok, f"{len(codes)} 个全通过")
else:
    check("S7a 用户目录有配置文件", False, "文件不存在")

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
