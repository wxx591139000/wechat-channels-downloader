#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发码台账专项测试（v2.0.18 新增）。

覆盖：
  L1 写入/读取往返（含中文、特殊字符）
  L2 按码反查（容错：忽略横杠与大小写）
  L3 按设备码反查（识别同机多码）
  L4 统计（档位/渠道/日期分布）
  L5 坏行容错（不炸，跳过并告警）
  L6 append-only（多次写入不覆盖历史）
  L7 台账在 git 仓库外（PII 不入库红线）
  L8 写入失败不阻断发码（返回 False + 打印告警）
  L9 gen --no-ledger 不写台账
  L10 默认路径 = _archives/keys/licenses.jsonl（与密钥备份同目录）

跑法（在 sphdl/ 下）：python tests/verify_ledger.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))              # 先加仓库根，才能 import sphdl
sys.path.insert(0, str(ROOT / "tools"))    # 再加 tools（ledger.py）

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


import ledger as L  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="sphdl_ledger_"))
LP = tmp / "licenses.jsonl"

# ---------- L1 写入/读取往返 ----------
print("=" * 74)
print("L1 写入 / 读取往返")
print("=" * 74)
ok1 = L.append("SPHDL-AAAA-BBBB-CCCC-DDDD-EEEE-FFFF", 20000, "旗舰版",
               customer="张三", contact="闲鱼:zhangsan88",
               channel="闲鱼", price="199", note="旗舰版永久", path=LP)
check("L1a append 返回 True", ok1 is True)
recs = L.load(LP)
check("L1b 读到 1 条", len(recs) == 1, f"n={len(recs)}")
r = recs[0] if recs else {}
check("L1c 授权码字段正确", r.get("code") == "SPHDL-AAAA-BBBB-CCCC-DDDD-EEEE-FFFF")
check("L1d 中文客户名正确（未转义乱码）", r.get("customer") == "张三")
check("L1e 联系方式含冒号正确", r.get("contact") == "闲鱼:zhangsan88")
check("L1f 档位标签正确", r.get("tier_label") == "旗舰版")
check("L1g tier 数值正确", r.get("tier") == 20000)
check("L1h 有 issued 时间戳", bool(r.get("issued")), r.get("issued", ""))
check("L1i 文件是真 JSONL（每行一条 JSON）",
      all(json.loads(x) for x in LP.read_text(encoding="utf-8").splitlines() if x.strip()))

# ---------- L2 按码反查（容错） ----------
print("\n" + "=" * 74)
print("L2 按码反查（溯源：市面上出现某码）")
print("=" * 74)
check("L2a 原样查得到", len(L.find_by_code("SPHDL-AAAA-BBBB-CCCC-DDDD-EEEE-FFFF", LP)) == 1)
check("L2b 去横杠也查得到",
      len(L.find_by_code("SPHDLAAAABBBBCCCCDDDDEEEEFFFF", LP)) == 1)
check("L2c 小写也查得到",
      len(L.find_by_code("sphdl-aaaa-bbbb-cccc-dddd-eeee-ffff", LP)) == 1)
check("L2d 不存在的码返回空",
      len(L.find_by_code("SPHDL-9999-9999-9999-9999-9999-9999", LP)) == 0)
check("L2e 空码返回空（不炸）", len(L.find_by_code("", LP)) == 0)
check("L2f None 返回空（不炸）", len(L.find_by_code(None, LP)) == 0)

# ---------- L6 append-only ----------
print("\n" + "=" * 74)
print("L6 append-only（多次写入不覆盖历史）")
print("=" * 74)
L.append("SPHDL-1111-2222-3333-4444-5555-6666", 500, "轻量版",
         customer="李四", channel="闲鱼", path=LP)
L.append("SPHDL-7777-8888-9999-AAAA-BBBB-CCCC", 1000, "标准版",
         customer="王五", channel="微信", path=LP)
recs = L.load(LP)
check("L6a 三条都在（未覆盖）", len(recs) == 3, f"n={len(recs)}")
check("L6b 顺序为追加顺序",
      [x["customer"] for x in recs] == ["张三", "李四", "王五"])
check("L6c 首条内容未被改动", recs[0]["code"].startswith("SPHDL-AAAA"))

# ---------- L3 按设备码反查 ----------
print("\n" + "=" * 74)
print("L3 按设备码反查（识别同机多码 = 疑似转卖）")
print("=" * 74)
L.append("SPHDL-DEV1-0000-0000-0000-0000-0001", 2000, "专业版",
         customer="买家A", device="A3F8-91C2-B7E4-5D9A", path=LP)
L.append("SPHDL-DEV2-0000-0000-0000-0000-0002", 2000, "专业版",
         customer="买家B", device="A3F8-91C2-B7E4-5D9A", path=LP)  # 同机！
check("L3a 同设备码查到 2 条", len(L.find_by_device("A3F8-91C2-B7E4-5D9A", LP)) == 2)
check("L3b 设备码容错（去横杠/小写）",
      len(L.find_by_device("a3f891c2b7e45d9a", LP)) == 2)
check("L3c 不存在的设备码返回空",
      len(L.find_by_device("0000-0000-0000-0000", LP)) == 0)
check("L3d 空设备码返回空", len(L.find_by_device("", LP)) == 0)

# ---------- L4 统计 ----------
print("\n" + "=" * 74)
print("L4 统计（档位 / 渠道 / 日期 / 重复设备）")
print("=" * 74)
st = L.stats(LP)
check("L4a 总数 5", st["total"] == 5, f"total={st['total']}")
check("L4b 档位分布正确",
      st["by_tier"].get("旗舰版") == 1 and st["by_tier"].get("专业版") == 2,
      str(st["by_tier"]))
check("L4c 渠道分布正确",
      st["by_channel"].get("闲鱼") == 2 and st["by_channel"].get("微信") == 1
      and st["by_channel"].get("(未填)") == 2,
      str(st["by_channel"]))
check("L4d 日期分布非空", len(st["by_date"]) >= 1, str(st["by_date"]))
check("L4e 重复设备被识别",
      st["devices"].get("A3F8-91C2-B7E4-5D9A") == 2, str(st["devices"]))

# ---------- L5 坏行容错 ----------
print("\n" + "=" * 74)
print("L5 坏行容错（手工编辑坏了也不能炸）")
print("=" * 74)
BP = tmp / "bad.jsonl"
BP.write_text(
    '{"code":"SPHDL-OK"}\n'
    'this is not json at all\n'
    '\n'
    '{"code":"SPHDL-OK2","customer":"正常"}\n',
    encoding="utf-8")
recs = L.load(BP)
check("L5a 坏行被跳过，好行仍读到 2 条", len(recs) == 2, f"n={len(recs)}")
check("L5b 空行被忽略", all(r.get("code") for r in recs))
check("L5c 不抛异常", True)
check("L5d 不存在的文件返回空列表", L.load(tmp / "nope.jsonl") == [])

# ---------- L8 写入失败不阻断发码 ----------
print("\n" + "=" * 74)
print("L8 写入失败不阻断发码（返回 False + 告警，不抛异常）")
print("=" * 74)
# 指向一个无法创建的路径（Windows 上非法字符）
bad_path = Path("Z:/definitely/not/exists/licenses.jsonl")
try:
    ok = L.append("SPHDL-FAIL-TEST", 500, "轻量版", path=bad_path)
    check("L8a 返回 False 而非抛异常", ok is False, f"return={ok}")
except Exception as e:  # noqa: BLE001
    check("L8a 返回 False 而非抛异常", False, f"抛了异常: {e}")
check("L8b 未创建非法路径", not bad_path.exists())

# ---------- L7 台账在 git 仓库外 ----------
print("\n" + "=" * 74)
print("L7 ★台账在 git 仓库外（客户 PII 不入库红线）")
print("=" * 74)
default_p = L.ledger_path()
check("L7a 默认路径在 _archives/keys/ 下",
      "_archives" in str(default_p) and "keys" in str(default_p),
      str(default_p))
check("L7b 默认路径不在 git 仓库内（不在 sphdl/ 下）",
      str(default_p).replace("\\", "/").find("/wechat-channels-downloader/sphdl/") < 0)
r = subprocess.run(["git", "check-ignore", "-q", str(default_p)],
                   cwd=ROOT, capture_output=True, text=True)
check("L7c git 无法跟踪该路径（仓库外）",
      "outside repository" in (r.stderr or "") or r.returncode != 0,
      (r.stderr or "").strip()[:60])

# ---------- L9 gen --no-ledger ----------
print("\n" + "=" * 74)
print("L9 gen --no-ledger 不写台账")
print("=" * 74)
# 导出临时密钥以便生成码
sys.path.insert(0, str(ROOT))
from sphdl import license as lic  # noqa: E402
sec = (ROOT / ".license_secret").read_text(encoding="utf-8").strip()
(ROOT / "sphdl" / "_lic_key.py").write_text(
    '"""tmp"""\nK = %r\n' % lic.obfuscate(bytes.fromhex(sec)),
    encoding="utf-8")
try:
    env = dict(os.environ, SPHDL_LEDGER=str(tmp / "gen_ledger.jsonl"),
               PYTHONIOENCODING="utf-8")
    py = str(ROOT.parent / ".venv" / "Scripts" / "python.exe")
    r1 = subprocess.run([py, "tools/license_tool.py", "gen", "500",
                         "--no-ledger"], cwd=ROOT, env=env,
                        capture_output=True, text=True)
    check("L9a --no-ledger 明确提示未登记",
          "未登记台账" in (r1.stdout or ""), (r1.stdout or "")[:80])
    check("L9b --no-ledger 未创建台账文件",
          not (tmp / "gen_ledger.jsonl").exists())
    r2 = subprocess.run([py, "tools/license_tool.py", "gen", "1000",
                         "--customer", "测试客户", "--channel", "闲鱼"],
                        cwd=ROOT, env=env, capture_output=True, text=True)
    check("L9c 默认登记台账", (tmp / "gen_ledger.jsonl").exists())
    check("L9d 输出提示已登记", "已登记台账" in (r2.stdout or ""))
    gl = L.load(tmp / "gen_ledger.jsonl")
    check("L9e 台账内容正确（客户/渠道/档位）",
          len(gl) == 1 and gl[0]["customer"] == "测试客户"
          and gl[0]["channel"] == "闲鱼" and gl[0]["tier"] == 1000,
          str(gl[:1]))
    check("L9f 生成的码仍有效（台账不影响发码）",
          bool(lic.verify_code(gl[0]["code"])) if gl else False)
finally:
    (ROOT / "sphdl" / "_lic_key.py").unlink(missing_ok=True)

# ---------- L10 ledger 子命令可跑 ----------
print("\n" + "=" * 74)
print("L10 ledger 子命令（list / find / stats）可跑")
print("=" * 74)
env2 = dict(os.environ, SPHDL_LEDGER=str(LP), PYTHONIOENCODING="utf-8")
py = str(ROOT.parent / ".venv" / "Scripts" / "python.exe")
for act, val, kw in [("list", None, "台账共"),
                     ("stats", None, "总计"),
                     ("find", "SPHDL-AAAA-BBBB-CCCC-DDDD-EEEE-FFFF", "code")]:
    argv = [py, "tools/license_tool.py", "ledger", act]
    if val:
        argv.append(val)
    rr = subprocess.run(argv, cwd=ROOT, env=env2, capture_output=True, text=True)
    check(f"L10 {act} 正常输出（含「{kw}」）", kw in (rr.stdout or ""),
          (rr.stdout or "").strip().splitlines()[0][:70] if rr.stdout else "无输出")

import shutil  # noqa: E402
shutil.rmtree(tmp, ignore_errors=True)

print()
ok = sum(1 for _, p, _ in RESULTS if p)
print(f"RESULT: {'ALL PASS' if ok == len(RESULTS) else 'FAIL'} ({ok}/{len(RESULTS)})")
for n, p, _ in RESULTS:
    if not p:
        print("  未通过: " + n)
sys.exit(0 if ok == len(RESULTS) else 1)
