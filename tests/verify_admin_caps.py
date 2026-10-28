"""发码后台：点赞增强 + 抖音 + 复制文案格式验证（v2.6.1）。

═══ 客户需求（2026-10-27）═══
1. 「补上后台勾选」（点赞增强包要能在后台勾，不用敲命令行）
2. 「发码里点复制授权码时，应该是『轻量版2台授权码：XXXX』
    『公众号视频授权码：XXXX』『点赞增强包授权码：XXXX』格式，
    这样我粘贴时方便」

覆盖：
  B1  code_label：视频号各档位 + 台数
  B2  code_label：公众号
  B3  code_label：抖音
  B4  code_label：点赞增强包（带 caps）
  B5  后台表单有抖音选项
  B6  后台表单有「点赞增强包」勾选框
  B7  后台 make_code(caps=1) 生成 ver=5 码
  B8  ★后台生成的 ver=5 码能被客户端 license.py 解析出 caps
  B9  后台：设备绑定 + caps → 明确报错
  B10 后台：caps + 台数可组合
  B11 复制按钮复制的是「带标签的完整文案」
  B12 老码型（后台无 caps）仍是 ver=2，不破坏
"""
import importlib.util
import json
import subprocess
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


# ══════════ 加载服务端模块（独立导入）══════════
SRV = ROOT / "server" / "sphdl_admin.py"
spec = importlib.util.spec_from_file_location("sphdl_admin_mod", SRV)
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)

print("=" * 74)
print("B1-B4 复制文案格式")
print("=" * 74)
cases = [
    ("视频号 轻量 2 台", dict(tier=500, product=1, max_devices=2),
     "轻量2台授权码："),
    ("视频号 标准 1 台", dict(tier=1000, product=1, max_devices=1),
     "标准1台授权码："),
    ("视频号 旗舰 不限台", dict(tier=2000, product=1), "旗舰授权码："),
    ("公众号", dict(tier=0, product=2), "公众号视频授权码："),
    ("抖音", dict(tier=0, product=3), "抖音批量下载授权码："),
    ("点赞增强包", dict(tier=2000, product=1, caps=1), "点赞增强包授权码："),
]
for name, kw, want in cases:
    got = S.code_label(**kw)
    print(f"  {name:18s} → {got!r}")
    check(f"B 文案: {name}", got == want, f"{got!r} vs {want!r}")

print()
print("=" * 74)
print("B5-B6 表单选项")
print("=" * 74)
src = SRV.read_text(encoding="utf-8")
check("B5 产品下拉有抖音（value=3）",
      '<option value="3">抖音批量下载' in src)
check("B6 有点赞增强勾选框",
      'name="cap_like"' in src and "点赞增强包" in src)
check("B6b 勾选框说明提到叠加使用", "叠加使用" in src)

print()
print("=" * 74)
print("B7-B10 后台生成能力位码")
print("=" * 74)
if not S._secret():
    print("  ⚠️ 服务端未配置密钥（SPHDL_LIC_SECRET），跳过生成测试")
    check("B7 后台生成 ver=5 码", False, "无密钥")
else:
    c = S.make_code(2000, product=1, caps=S.CAP_LIKE_STATS, max_devices=1)
    print(f"  生成: {c}")
    # 用客户端 license 解析
    from sphdl import license as L
    info = L.verify_code(c)
    print(f"  客户端解析: {info}")
    check("B7 后台能生成码", bool(c))
    check("B8 ★后台生成的码客户端能解析", info is not None)
    if info:
        check("B8b ★caps 位正确", info["caps"] == L.CAP_LIKE_STATS,
              str(info["caps"]))
        check("B8c ver=5", info["ver"] == 5, str(info["ver"]))
        check("B8d 已是视频号产品（叠加用）",
              info["product"] == L.PRODUCT_VIDEO, str(info["product"]))
        check("B8e 台数写入", info.get("max_devices") == 1,
              str(info.get("max_devices")))

    # B9 设备绑定 + caps
    try:
        S.make_code(500, product=1, caps=S.CAP_LIKE_STATS, device="163145d5")
        check("B9 设备绑定+caps 被拒绝", False, "没报错")
    except ValueError as e:
        check("B9 设备绑定+caps 被拒绝", True, str(e)[:40])

    # B10 caps + 台数
    c10 = S.make_code(2000, product=1, caps=S.CAP_LIKE_STATS, max_devices=3)
    i10 = L.verify_code(c10)
    check("B10 caps+台数可组合",
          i10 is not None and i10["caps"] == 1 and i10["max_devices"] == 3,
          f"{i10['caps'] if i10 else '?'}/{i10.get('max_devices') if i10 else '?'}")

    # B12 老码型
    c12 = S.make_code(1000, product=1)
    i12 = L.verify_code(c12)
    check("B12 后台无 caps 仍是 ver=2（老码型不变）",
          i12 is not None and i12["ver"] == 2 and i12["caps"] == 0,
          f"ver={i12['ver'] if i12 else '?'} caps={i12['caps'] if i12 else '?'}")

print()
print("=" * 74)
print("B11 复制按钮文案")
print("=" * 74)
check("B11a 复制按钮复制 lbl 元素（带标签）",
      "cp(document.getElementById('lbl')" in src)
check("B11b 页面显示带标签文案",
      "_lbl + code" in src, "见 flash 拼装")
check("B11c copyBoth 用 LBL 常量",
      "LBL+CODE" in src)
check("B11d 注入了 LBL 常量", "const LBL=" in src)

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
