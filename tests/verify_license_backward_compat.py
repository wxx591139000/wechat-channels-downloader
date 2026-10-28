"""已售授权码兼容性审计（发布新版前必跑，v2.6.4）。

═══ 为什么这是红线（skill 项目归档 §4.5.4）═══
客户升级到新版后，**买过的授权码必须仍然有效**。
凭据失效 = 已付费客户被升级弄坏 = 退款 + 差评。

本次新增 ver=5 码型（带能力位），**必须实测**：
  · ver=1/2/3/4 四种历史码型是否仍全部有效
  · 每种码型 × 每种档位 全组合
  · ★真实台账里发出的每一个码逐个校验（不是只测自己刚生成的）

覆盖：
  C1  ver=1 老码 × 4 档位
  C2  ver=2 视频号 × 4 档位 + 公众号 + 抖音
  C3  ver=3 设备绑定 × 4 档位
  C4  ver=4 限台数 × 4 档位
  C5  ver=5 能力位码 × 4 档位
  C6  ★真实台账（生产 licenses.jsonl）里每个码都能校验通过
  C7  ★真实台账的档位/产品与码内一致
  C8  台账里 ver=5 码的 caps 与台账记录一致
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


# 确保密钥注入
py = str(ROOT.parent / ".venv" / "Scripts" / "python.exe")
sec = ROOT / ".license_secret"
code = ("import sys, pathlib; sys.path.insert(0,'.')\n"
        "from sphdl import license as lic\n"
        "s = bytes.fromhex(pathlib.Path('.license_secret').read_text().strip())\n"
        "pathlib.Path('sphdl/_lic_key.py').write_text("
        "'K = %r\\n' % lic.obfuscate(s), encoding='utf-8')\n")
subprocess.run([py, "-c", code], cwd=str(ROOT), capture_output=True)

import sphdl.license as L

print("=" * 74)
print("C1-C5 全码型 × 全档位 组合")
print("=" * 74)
TIERS = (500, 1000, 2000, 20000)

# ver=1 老码：product 默认 0/1 且无 max_devices/device → 走 ver=1
cases = []
for t in TIERS:
    cases.append(("ver=1", t, dict(tier=t)))
for t in TIERS:
    cases.append(("ver=2视频号", t, dict(tier=t, product=L.PRODUCT_VIDEO, max_devices=None)))
cases.append(("ver=2公众号", 0, dict(tier=0, product=L.PRODUCT_MP_ARTICLE)))
cases.append(("ver=2抖音", 0, dict(tier=0, product=L.PRODUCT_DOUYIN)))
for t in TIERS:
    cases.append(("ver=3绑定", t, dict(tier=t, product=L.PRODUCT_VIDEO, device="163145d5")))
for t in TIERS:
    cases.append(("ver=4台数", t, dict(tier=t, product=L.PRODUCT_VIDEO, max_devices=3)))
for t in TIERS:
    cases.append(("ver=5点赞", t, dict(tier=t, product=L.PRODUCT_VIDEO,
                                       caps=L.CAP_LIKE_STATS, max_devices=1)))

fails = []
for name, expect_tier, kw in cases:
    try:
        c = L.make_code(**kw)
        info = L.verify_code(c)
        if info is None:
            fails.append(f"{name} tier={expect_tier}: 校验失败")
            continue
        if info["tier"] != expect_tier:
            fails.append(f"{name} tier={expect_tier}: 解析成 {info['tier']}")
            continue
        print(f"  ✅ {name:10s} tier={expect_tier:6d} → ver={info['ver']} "
              f"prod={info['product']} caps={info['caps']}")
    except Exception as e:
        fails.append(f"{name} tier={expect_tier}: {type(e).__name__}: {e}")

check(f"C1-C5 {len(cases)} 组组合全部有效", not fails,
      "; ".join(fails[:3]) if fails else f"{len(cases)}/{len(cases)}")

print()
print("=" * 74)
print("C6-C8 ★真实台账逐个校验（生产码）")
print("=" * 74)
# 台账在 VPS；本地拉一份只读副本校验
LEDGER_REMOTE = "/opt/sphdl-admin/data/licenses.jsonl"
tmp = Path("/tmp/ledger_audit.jsonl")
if sys.platform.startswith("win"):
    tmp = Path(r"C:/Users/Dancing/AppData/Local/Temp/ledger_audit.jsonl")
r = subprocess.run(["scp", "vps-aliyun:" + LEDGER_REMOTE, str(tmp)],
                   capture_output=True, timeout=60)
print(f"  拉取台账: {'成功' if r.returncode == 0 else '失败'} → {tmp}")

if r.returncode == 0 and tmp.exists():
    rows = []
    for ln in tmp.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            rows.append(json.loads(ln))
        except Exception:
            continue
    issued = [x for x in rows if x.get("code")]
    print(f"  台账总行数: {len(rows)}  含码行数: {len(issued)}")

    bad, mismatch, capmis = [], [], []
    for x in issued:
        c = x["code"]
        info = L.verify_code(c)
        who = x.get("customer") or "(未署名)"
        if info is None:
            bad.append(f"{c} ({who})")
            continue
        # 档位/产品一致性（台账有的才比）
        if x.get("tier") is not None and info["tier"] != int(x["tier"]):
            mismatch.append(f"{c} 台账tier={x['tier']} 码内={info['tier']}")
        if x.get("product") is not None and info["product"] != int(x["product"]):
            mismatch.append(f"{c} 台账prod={x['product']} 码内={info['product']}")
        if x.get("caps") is not None and info.get("caps", 0) != int(x["caps"]):
            capmis.append(f"{c} 台账caps={x['caps']} 码内={info.get('caps')}")

    print(f"  校验失败: {len(bad)}   档位/产品不一致: {len(mismatch)}   "
          f"caps 不一致: {len(capmis)}")
    if bad:
        for b in bad[:5]:
            print(f"    ❌ {b}")
    check("C6 ★真实台账所有码都能校验通过", not bad,
          f"{len(bad)} 个失效" if bad else f"{len(issued)} 个全部有效")
    check("C7 ★台账档位/产品与码内一致", not mismatch,
          "; ".join(mismatch[:2]) if mismatch else "全部一致")
    check("C8 台账 caps 与码内一致（新增字段）", not capmis,
          "; ".join(capmis[:2]) if capmis else "全部一致")

    # 打印台账概览
    from collections import Counter
    vers = Counter()
    for x in issued:
        info = L.verify_code(x["code"])
        if info:
            vers[info["ver"]] += 1
    print(f"  台账码型分布: {dict(sorted(vers.items()))}")
else:
    check("C6 ★真实台账逐个校验", False, "台账拉取失败")

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
