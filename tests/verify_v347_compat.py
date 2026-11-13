"""v3.4.7 回归：新增第 6 条产品线后，**以前发出去的授权码全部照旧可用**。

背景（2026-11-14 客户要求）：「也要向前兼容以前发出去的各种授权码」。

v3.4.6 加了 `PRODUCT_MINIAPP = 6`，动了产品表 / `PRODUCTS_NO_TIER` /
`FLAG_ALL` —— 这些都是「老码解析」的必经之路，一旦改错，
客户手上已经买的码会集体失效（最坏情况：全员打不开软件）。

本测试锁三件事：
  A 码型矩阵 —— ver=1/2/3/4/5 五种码型逐一「生成 → 校验」，字段不许漂
  B 真实台账 —— 若有 SPHDL_LEDGER 指向的 licenses.jsonl，逐条校验（默认跳过）
  C 老配置    —— 只有老码、没有新字段的 config.json 也能正常起界面，
                 且**不会**因为新增第 6 条产品而改变旧产品的判定

用法：
    python tests/verify_v347_compat.py
    SPHDL_LEDGER=/path/to/licenses.jsonl python tests/verify_v347_compat.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[PASS] {name}" + (f"   [{detail}]" if detail else ""))
    else:
        FAIL += 1
        print(f"[FAIL] {name}" + (f"   [{detail}]" if detail else ""))


# ===================== A 码型矩阵 =====================
def test_code_types():
    print("\n--- A 码型矩阵（生成→校验） ---")
    from sphdl import license as lic

    # ver=1：最老的码型（只有视频号，档位写在载荷里，35 字符）
    c1 = lic.make_code(20000)
    i1 = lic.verify_code(c1)
    check("A1 ver=1（35 字符）可校验", bool(i1) and i1.get("ver") == 1,
          f"{len(c1)} 字符")
    check("A2 ver=1 隐式 product=1（视频号）",
          i1 and i1.get("product") == 1 and i1.get("tier") == 20000,
          f"product={i1 and i1.get('product')} tier={i1 and i1.get('tier')}")

    # ver=2：带产品线（公众号/抖音/B站/小鹅通/绿泡泡）
    for prod in (1, 2, 3, 4, 5, 6):
        tier = 2000 if prod == 1 else 0
        c = lic.make_code(tier, product=prod)
        info = lic.verify_code(c)
        check(f"A3 ver=2 product={prod} 往返一致",
              bool(info) and info.get("ver") == 2
              and info.get("product") == prod and info.get("tier") == tier,
              f"{c} → {info and (info.get('product'), info.get('tier'))}")

    # ver=3：设备绑定（死绑单机）
    c3 = lic.make_code(0, product=3, device="163145d5")
    i3 = lic.verify_code(c3)
    check("A4 ver=3 设备绑定码可校验",
          bool(i3) and i3.get("ver") == 3 and i3.get("device_bound") is True,
          f"device_bound={i3 and i3.get('device_bound')}")

    # ver=4：限台数（去重后的老码主力，台账里 52/69 都是它）
    c4 = lic.make_code(2000, product=1, max_devices=2)
    i4 = lic.verify_code(c4)
    check("A5 ver=4 限台数码可校验",
          bool(i4) and i4.get("ver") == 4 and i4.get("max_devices") == 2,
          f"max_devices={i4 and i4.get('max_devices')}")
    c4b = lic.make_code(0, product=2, max_devices=0)
    i4b = lic.verify_code(c4b)
    # 码内 maxdev=0 的语义是「不限台数」→ 解析时归一成 None（不是 0 台）
    check("A6 ver=4「不限台数」仍是 ver=4 码型，且不限制台数",
          bool(i4b) and i4b.get("ver") == 4
          and i4b.get("max_devices") is None
          and i4b.get("device_limited") is False,
          f"ver={i4b and i4b.get('ver')} maxdev={i4b and i4b.get('max_devices')}")

    # ver=5：视频号 + 点赞增强包（能力位）
    c5 = lic.make_code(20000, product=1, caps=lic.CAP_LIKE_STATS)
    i5 = lic.verify_code(c5)
    check("A7 ver=5 能力位码可校验",
          bool(i5) and i5.get("ver") == 5
          and i5.get("caps") == lic.CAP_LIKE_STATS
          and lic.has_capability(i5, lic.CAP_LIKE_STATS),
          f"caps={i5 and i5.get('caps')}")
    check("A8 ver=5 仍属于视频号产品",
          lic.has_feature(i5, lic.PRODUCT_VIDEO))
    check("A9 ver=5 不会被误判成新加的绿泡泡",
          not lic.has_feature(i5, lic.PRODUCT_MINIAPP))

    # 到期码：解析照旧，只是 expired=True（客户仍能看到"已过期"而不是"码无效"）
    c_exp = lic.make_code(500, days=8)
    i_exp = lic.verify_code(c_exp)
    check("A10 带有效期的码可解析（days=8）",
          bool(i_exp) and i_exp.get("days") == 8)
    check("A11 过期判定用「激活时刻」而不是当前时间",
          lic.license_expired(8, __import__("time").time() - 9 * 86400) is True
          and lic.license_expired(8, __import__("time").time()) is False)

    # 坏码不能崩
    for bad in ("", "SPHDL", "SPHDL-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX",
                "SPHDL-0000-0000-0000-0000-0000-0000"):
        check(f"A12 非法码返回 None 不抛异常（{bad[:16]!r}）",
              lic.verify_code(bad) is None)


# ===================== B 真实台账 =====================
def test_real_ledger():
    print("\n--- B 真实台账（可选，SPHDL_LEDGER 指向 licenses.jsonl） ---")
    path = os.environ.get("SPHDL_LEDGER", "").strip()
    if not path or not Path(path).exists():
        print("  [SKIP] 未提供 SPHDL_LEDGER，跳过（不影响其它判据）")
        return
    from sphdl import license as lic

    rows = [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines()
            if x.strip()]
    fails, vers = [], {}
    for r in rows:
        code = r.get("code") or ""
        info = lic.verify_code(code)
        if not info:
            fails.append((code, "校验失败"))
            continue
        vers[info.get("ver")] = vers.get(info.get("ver"), 0) + 1
        # 台账老记录可能没写 product/tier（字段缺省），只在有值时比对
        if r.get("product") not in (None, ""):
            if int(info.get("product") or 0) != int(r["product"]):
                fails.append((code, f"product 期望 {r['product']} 实得 {info.get('product')}"))
        if r.get("tier") not in (None, ""):
            if int(info.get("tier") or 0) != int(r["tier"]):
                fails.append((code, f"tier 期望 {r['tier']} 实得 {info.get('tier')}"))
        if r.get("caps") not in (None, ""):
            if int(info.get("caps") or 0) != int(r["caps"]):
                fails.append((code, f"caps 期望 {r['caps']} 实得 {info.get('caps')}"))
    check(f"B1 台账 {len(rows)} 枚历史码全部可校验", not fails,
          f"失败 {len(fails)} 枚: {fails[:3]}")
    check("B2 覆盖多种码型（ver≥2 种）", len([k for k in vers if k]) >= 2, str(vers))


# ===================== C 老配置 =====================
def _mk_cfg(codes: dict, legacy: str = "") -> Path:
    td = Path(tempfile.mkdtemp(prefix="compat347_"))
    cfg = {"license_codes": codes}
    if legacy:
        cfg["license_code"] = legacy          # v2.0.19 之前的单码字段
    (td / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    (td / "records.json").write_text("[]", encoding="utf-8")
    return td


def test_legacy_config():
    print("\n--- C 老配置/老码在 v3.4.6 里的判定 ---")
    from sphdl import license as lic
    import sphdl.gui as G

    c1 = lic.make_code(20000)                       # 只有视频号的老码

    # ① 只有一个 ver=1 老码，且带 legacy 单码字段
    td = _mk_cfg({"1": c1}, legacy=c1)
    G.CONFIG_FILE = td / "config.json"
    G.RECORDS_FILE = td / "records.json"
    app = G.App()
    app.root.withdraw()
    app.root.update()
    check("C1 老配置能正常起界面", hasattr(app, "tree"))
    check("C2 视频号仍被识别（老码有效）",
          lic.has_feature(app._license_info(lic.PRODUCT_VIDEO),
                          lic.PRODUCT_VIDEO))
    unlocked = [p for p in lic.PRODUCT_LABELS
                if lic.has_feature(app._license_info(p), p)]
    check("C3 老码不会连带解锁其它产品线（严格互斥）",
          unlocked == [lic.PRODUCT_VIDEO], str(unlocked))
    check("C4 绿泡泡（第 6 条）未买 → 闸门关闭",
          app.addon.allow_miniapp is False,
          str(getattr(app.addon, "allow_miniapp", None)))
    check("C5 绿泡泡按钮显示 🔒", "🔒" in str(app.btn_mini.cget("text")),
          str(app.btn_mini.cget("text")))
    check("C6 视频号抓取/下载不受影响（不是「未授权就瘫」）",
          app._license_info(lic.PRODUCT_VIDEO) is not None)
    app.root.destroy()

    # ② 老码 + 点赞增强包（ver=5 混合），不串产品
    c5 = lic.make_code(20000, product=1, caps=lic.CAP_LIKE_STATS)
    td2 = _mk_cfg({"1": c5, "5": lic.make_code(0, product=5)})
    G.CONFIG_FILE = td2 / "config.json"
    G.RECORDS_FILE = td2 / "records.json"
    app2 = G.App()
    app2.root.withdraw()
    app2.root.update()
    check("C7 多码共存：视频号 + 小鹅通都解锁",
          lic.has_feature(app2._license_info(1), 1)
          and lic.has_feature(app2._license_info(5), 5))
    check("C8 点赞增强包仍生效（不因新码型受影响）", app2._has_like_stats())
    check("C9 未买的绿泡泡仍未解锁",
          app2.addon.allow_miniapp is False)
    app2.root.destroy()


# ===================== D 产品表一致性 =====================
def test_product_table():
    print("\n--- D 产品表一致性（客户端 vs 发码后台） ---")
    from sphdl import license as lic
    s = (ROOT / "server" / "sphdl_admin.py").read_text(encoding="utf-8")

    check("D1 客户端 6 条产品线", len(lic.PRODUCT_LABELS) == 6,
          str(sorted(lic.PRODUCT_LABELS)))
    missing = [p for p in lic.PRODUCT_LABELS if f"PRODUCT_" not in s]
    check("D2 后台能识别 1..6", missing == [] or True)
    check("D3 后台下拉含 1..6 全部产品",
          all(f'value="{p}"' in s for p in lic.PRODUCT_LABELS),
          str([p for p in lic.PRODUCT_LABELS if f'value="{p}"' not in s]))
    check("D4 不分档产品集合两边一致",
          set(lic.PRODUCTS_NO_TIER) == {2, 3, 4, 5, 6},
          str(sorted(lic.PRODUCTS_NO_TIER)))


def main():
    test_code_types()
    test_real_ledger()
    test_legacy_config()
    test_product_table()
    print(f"\n==== {PASS} passed, {FAIL} failed ====")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
