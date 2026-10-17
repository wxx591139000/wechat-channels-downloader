#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设备绑定"4 项取 2"测试（v2.0.19 抗硬件变动改造）。

背景：原实现是「整串哈希」，客户换硬盘/重装系统设备码全变 → 绑定码失效，
      售后成本高。改为存 4 个分量、容许 2 项不同（行业通行做法）。

覆盖：
  D1 device_components 返回 4 个分量
  D2 bind_token 格式（8 位十六进制）
  D3 ★绑定串发码 → 本机通过
  D4 ★换硬盘 → 仍通过（抗误伤，核心价值）
  D5 ★换主板 → 仍通过
  D6 ★重装系统（guid 变）→ 仍通过
  D7 ★换整机 → 拒绝
  D8 只采到部分分量时不误伤（放宽阈值）
  D9 脏数据不崩溃
  D10 非绑定码一律通过
  D11 设备码发码（严格模式）兼容
  D12 空设备信息不阻止

跑法：python tests/verify_device_bind.py
"""
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sphdl import license as lic  # noqa: E402
from sphdl import telemetry as tel  # noqa: E402

sec = (ROOT / ".license_secret").read_text(encoding="utf-8").strip()
(ROOT / "sphdl" / "_lic_key.py").write_text(
    '"""t"""\nK = %r\n' % lic.obfuscate(bytes.fromhex(sec)), encoding="utf-8")
import importlib  # noqa: E402
importlib.reload(lic)
importlib.reload(tel)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


# ---------- 构造可控分量（不依赖真实硬件，测试才可重复） ----------
C = ["166b4c9c1060a787", "3163c4f0e591554a", "45d369775b50b20e",
     "d536c46e94bf6257"]
OTHER = ["a1b2c3d4e5f60718", "b2c3d4e5f6071829", "c3d4e5f607182930",
         "d4e5f60718293041"]


def mk(comps):
    tok = "".join((c[:2] if len(c) >= 2 else "00") for c in comps)
    return tok


# ---------- D1 ----------
print("=" * 74)
print("D1 device_components 返回 4 个分量")
print("=" * 74)
real = tel.device_components()
check("D1a 返回 4 项", len(real) == 4, f"{len(real)} 项")
check("D1b 每项为 16 位十六进制或空",
      all((not x) or (len(x) == 16 and all(ch in "0123456789abcdef" for ch in x))
          for x in real), str([len(x) for x in real]))
n_ok = sum(1 for x in real if x)
check("D1c 至少采到 3 项（本机应能采到）", n_ok >= 3, f"采到 {n_ok} 项")

# ---------- D2 ----------
print("\n" + "=" * 74)
print("D2 bind_token 格式")
print("=" * 74)
tok = tel.bind_token(C)
check("D2a 8 位", len(tok) == 8, tok)
check("D2b 全十六进制", all(c in "0123456789abcdef" for c in tok), tok)
check("D2c 与分量首字节一致", tok == "163145d5", tok)

# ---------- D3~D7 核心矩阵 ----------
print("\n" + "=" * 74)
print("D3~D7 ★核心：绑定 + 抗硬件变动")
print("=" * 74)
token = mk(C)
code = lic.make_code(20000, product=lic.PRODUCT_VIDEO, device=token)
info = lic.verify_code(code)
check("D3a 码可校验", info is not None)
check("D3b ver=3 且 device_bound", info["ver"] == 3 and info["device_bound"])
ok, _ = lic.check_device(info, components=C)
check("D3c ★绑定串 + 原机 → 通过", ok is True)

# 换硬盘（disk 变）
c_disk = C[:3] + ["ffffffffffffffff"]
ok, _ = lic.check_device(info, components=c_disk)
check("D4 ★换硬盘 → 仍通过（3 项匹配）", ok is True)

# 换主板（board 变）
c_board = [C[0], "eeeeeeeeeeeeeeee", C[2], C[3]]
ok, _ = lic.check_device(info, components=c_board)
check("D5 ★换主板 → 仍通过（3 项匹配）", ok is True)

# 重装系统（guid 变）
c_guid = [C[0], C[1], "dddddddddddddddd", C[3]]
ok, _ = lic.check_device(info, components=c_guid)
check("D6 ★重装系统 → 仍通过（3 项匹配）", ok is True)

# 换整机（全变）
ok, why = lic.check_device(info, components=OTHER)
check("D7 ★换整机 → 拒绝", ok is False, why.split("\n")[0][:32])

# ---------- D8 部分分量 ----------
print("\n" + "=" * 74)
print("D8 只采到部分分量时不误伤")
print("=" * 74)
# 只有 2 项可用，其中 1 项匹配 → need 降到 1 → 通过
ok, _ = lic.check_device(info, components=[C[0], "", "", ""])
check("D8a 只采到 1 项且匹配 → 通过", ok is True)
ok, _ = lic.check_device(info, components=["", "", "", ""])
check("D8b 一项都没采到 → 不阻止", ok is True)

# ---------- D9 脏数据 ----------
print("\n" + "=" * 74)
print("D9 脏数据不崩溃")
print("=" * 74)
try:
    ok, _ = lic.check_device(info, components=["zzzz", "!!!", "", ""])
    check("D9a 非十六进制分量不抛异常", True, f"结果={ok}")
except Exception as e:                                  # noqa: BLE001
    check("D9a 非十六进制分量不抛异常", False, f"{type(e).__name__}: {e}")
try:
    ok, _ = lic.check_device(info, components=None)
    check("D9b components=None 不崩", True, f"结果={ok}")
except Exception as e:                                  # noqa: BLE001
    check("D9b components=None 不崩", False, str(e))

# ---------- D10 非绑定码 ----------
print("\n" + "=" * 74)
print("D10 非绑定码一律通过")
print("=" * 74)
c_free = lic.make_code(20000, product=lic.PRODUCT_VIDEO)
i_free = lic.verify_code(c_free)
check("D10a ver=2 不绑机", i_free["ver"] == 2 and not i_free["device_bound"])
check("D10b 任意设备都通过", lic.check_device(i_free, components=OTHER)[0] is True)
check("D10c 不给设备信息也通过", lic.check_device(i_free)[0] is True)

# ---------- D11 严格模式兼容 ----------
print("\n" + "=" * 74)
print("D11 设备码发码（严格模式）")
print("=" * 74)
fp = hashlib.sha256("|".join(C).encode()).hexdigest()
disp = tel.device_display(fp)
code_s = lic.make_code(20000, product=lic.PRODUCT_VIDEO, device=disp)
info_s = lic.verify_code(code_s)
check("D11a 严格码可校验", info_s is not None)
check("D11b ★传分量也能通过（有回退）",
      lic.check_device(info_s, disp, components=C)[0] is True)
check("D11c 只传设备码也通过",
      lic.check_device(info_s, disp)[0] is True)
check("D11d 换整机仍拒绝",
      lic.check_device(info_s, tel.device_display(
          hashlib.sha256("|".join(OTHER).encode()).hexdigest()),
          components=OTHER)[0] is False)

# ---------- D12 空信息 ----------
print("\n" + "=" * 74)
print("D12 取不到设备信息 → 不阻止")
print("=" * 74)
check("D12a 空分量+空设备码 → 通过",
      lic.check_device(info, "", components=[])[0] is True)

(ROOT / "sphdl" / "_lic_key.py").unlink(missing_ok=True)

print()
ok = sum(1 for _, p, _ in RESULTS if p)
print(f"RESULT: {'ALL PASS' if ok == len(RESULTS) else 'FAIL'} ({ok}/{len(RESULTS)})")
for n, p, _ in RESULTS:
    if not p:
        print("  未通过: " + n)
sys.exit(0 if ok == len(RESULTS) else 1)
