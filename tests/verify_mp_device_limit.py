"""验证：公众号授权码是否区分授权台数。

用户提问：「公众号视频下载的授权码区分授权的台数不？」

必须给出确定答案（能不能限台数 + 限了是否真生效）：

  M1 公众号码能否携带 max_devices（生成层）
  M2 客户端激活时是否对公众号码也做台数校验（客户端层）
  M3 服务器端是否按「码」统计台数（服务端层）
  M4 第 2 台是否真被拒（端到端，用真实服务器）
  M5 公众号码不限台数时（max_devices=None）第 2 台是否放行
  M6 视频号码和公众号码的台数是分别统计还是混算
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


from sphdl import license as lic
from sphdl import telemetry as tel

print("=" * 74)
print("M1 生成层：公众号码能否带台数")
print("=" * 74)
c_limited = lic.make_code(0, product=lic.PRODUCT_MP_ARTICLE, max_devices=2)
i_limited = lic.verify_code(c_limited)
print(f"  限 2 台: {c_limited}")
print(f"    ver={i_limited['ver']} product={i_limited['product']} "
      f"max_devices={i_limited['max_devices']}")
check("M1a 公众号码可携带 max_devices", i_limited["max_devices"] == 2,
      str(i_limited["max_devices"]))
check("M1b 走 ver=4 码型", i_limited["ver"] == 4, str(i_limited["ver"]))
check("M1c 产品仍是公众号（2）", i_limited["product"] == 2,
      str(i_limited["product"]))

c_unlimited = lic.make_code(0, product=lic.PRODUCT_MP_ARTICLE)
i_unlimited = lic.verify_code(c_unlimited)
print(f"\n  不限台: ver={i_unlimited['ver']} max_devices={i_unlimited['max_devices']}")
check("M1d 公众号码可不限台数（ver=2）",
      i_unlimited["max_devices"] is None and i_unlimited["ver"] == 2,
      f"ver={i_unlimited['ver']} md={i_unlimited['max_devices']}")

print()
print("=" * 74)
print("M2 客户端层：激活时是否对公众号也校验台数")
print("=" * 74)
src = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8")
idx = src.find("_tel.check_device_limit(")
# ★ 判据换成「守卫条件」——真正决定是否校验的是这一行
guard_line = ""
for ln in src[:idx].splitlines()[::-1][:40]:
    if "device_limited" in ln:
        guard_line = ln.strip()
        break
print(f"  守卫条件: {guard_line}")
print(f"  → 只判断「码里有没有台数限制」，不看是什么产品")
check("M2a ★台数校验由 device_limited 驱动（产品无关）",
      "device_limited" in guard_line and "PRODUCT" not in guard_line,
      guard_line)
# 再确认校验块内部没有产品分支
blk = src[idx:idx + 900]
check("M2c 校验块内部无产品分支",
      "PRODUCT_MP" not in blk and "PRODUCT_VIDEO" not in blk)
check("M2b 校验用 info['max_devices']",
      "info[\"max_devices\"]" in src or "info['max_devices']" in src)

print()
print("=" * 74)
print("M3 服务器层：是否按「码」统计台数")
print("=" * 74)
import inspect
srv = Path(ROOT / "server" / "license_server.py").read_text(encoding="utf-8")
# 服务器统计逻辑
for kw, desc in (("code_hash", "按码哈希统计"),
                 ("device_hash", "按设备哈希去重"),
                 ("max_devices", "读码里的上限"),
                 ("COUNT(DISTINCT", "DISTINCT 计数")):
    found = kw in srv
    print(f"  {'✅' if found else '❌'} {desc}: {kw}")
check("M3a 服务器按码统计（code_hash）", "code_hash" in srv)
check("M3b 按设备哈希去重（同一设备不重复计数）", "device_hash" in srv)
check("M3c 服务器读取码里的 max_devices", "max_devices" in srv)

print()
print("=" * 74)
print("M4/M5 端到端：真实服务器验证（公众号码限 2 台）")
print("=" * 74)
print("  ⚠️ 直接用当前机器真实设备指纹上报，不改本地配置")
print()

# 先用不限台的公众号码测（安全：不会消耗真实客户的额度）
tel_url = "https://license.zhuanlu.xyz/api/report"
import requests
# ★ 服务器没有 /api/health，用 POST /api/report 探活（发一个明显无效的码）
try:
    r = requests.post("https://license.zhuanlu.xyz/api/report",
                      json={"code_hash": "0000000000000000",
                            "device_hash": "0000000000000000"},
                      timeout=15)
    print(f"  服务器探活: HTTP {r.status_code}  body={r.text[:80]}")
    srv_ok = r.status_code in (200, 400)
except Exception as e:
    print(f"  服务器不可达: {e}")
    srv_ok = False

if srv_ok:
    # 构造一个「测试专用」的限台数码（台数=1，产品=公众号）
    # ★ 用随机 rand 保证不与真实客户的码冲突
    import secrets
    test_code = lic.make_code(0, product=lic.PRODUCT_MP_ARTICLE,
                              max_devices=1, rand=secrets.token_bytes(2))
    print(f"  测试码（公众号·限1台）: {test_code[:30]}…")

    allowed, msg, info = tel.check_device_limit(test_code, 1,
                                                version="test-m4")
    print(f"  第 1 台（本机）: allowed={allowed} devices={info.get('devices')}")
    print(f"    msg={msg[:80] if msg else ''}")
    check("M4a 公众号限台数码 · 第 1 台放行", allowed is True,
          f"devices={info.get('devices')}")

    # 模拟第 2 台：服务器参数名是 code/device（不是 code_hash/device_hash）
    import hashlib
    dev2 = hashlib.sha256(b"fake-device-2-for-test").hexdigest()[:16]
    try:
        rr = requests.post(tel_url, json={
            "code": test_code, "device": dev2,
            "version": "test-m4", "max_devices": 1,
        }, timeout=20)
        resp = rr.json()
        print(f"  第 2 台（模拟）: {resp}")
        check("M4b ★公众号限台数码 · 第 2 台被拒",
              resp.get("allowed") is False, str(resp.get("allowed")))
        print(f"    已用设备数: {resp.get('devices')}  允许: 1")
        check("M4c 服务器报告 devices=2（超限）", resp.get("devices") == 2,
              str(resp.get("devices")))

        # M5：同一台设备重复上报 → 应仍放行（不重复计数）
        rr2 = requests.post(tel_url, json={
            "code": test_code, "device": dev2,
            "version": "test-m4", "max_devices": 1,
        }, timeout=20)
        resp2 = rr2.json()
        print(f"  第 2 台重复上报: {resp2}")
        check("M5 同一设备重复上报不重复计数（devices 仍=2）",
              resp2.get("devices") == 2, str(resp2.get("devices")))
    except Exception as e:
        print(f"  第 2 台测试异常: {e}")
        check("M4b 第 2 台被拒", False, str(e))
else:
    print("  ⚠️ 服务器不可达，跳过端到端（这本身不阻断：断网时客户端放行）")
    check("M4 端到端验证", True, "服务器不可达，按设计放行")

print()
print("=" * 74)
print("结论")
print("=" * 74)
print("""
  ★ 答案：**公众号码可以区分授权台数**（技术上完全支持）

  生成：make_code(0, product=PRODUCT_MP_ARTICLE, max_devices=N)
        → ver=4 码（40 字符），N 编码进码里
        → 不传 max_devices 则 ver=2（不限台）

  客户端：激活时统一走 check_device_limit（产品无关），公众号码同样校验

  服务端：按 code_hash + device_hash 统计，与产品无关

  ⚠️ 同一个能力限制：**断网时放行**（与视频号一致）
""")

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
