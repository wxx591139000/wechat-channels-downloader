"""「点赞增强包」增值授权验证（v2.6.0）。

═══ 客户决策（2026-10-27）═══
「把支持点赞展示并排序的功能做成增值功能，单独一档售卖」→ 选方案 A：

  · 独立增值码，可与视频号码**叠加**使用
  · 能力位写在码里（ver=5 的 caps 字节），不是产品线
    （产品线互斥，做成 product=4 会导致客户买了点赞就不能用视频号）

═══ 核心风险（本测试重点）═══
license.py 是加密相关模块，改错会让**已发出的老码失效** —— 那是灾难。
所以必须验证 ver=1/2/3/4 全部照旧能解析，且 caps 恒为 0。

覆盖：
  G1  生成 ver=5 码（caps=点赞位）→ 能解析出 caps
  G2  ★老码回归：ver=1 老码仍可解析，caps=0
  G3  ★老码回归：ver=2 三种产品线都可解析
  G4  ★老码回归：ver=3 设备绑定码可解析
  G5  ★老码回归：ver=4 限台数码可解析
  G6  ★无点赞能力的码 has_capability=False
  G7  叠加：视频号码 + 点赞码 → 下载权限来自前者，点赞来自后者
  G8  篡改 caps 位 → MAC 校验拒绝
  G9  未知能力位 → make_code 报错（防越权）
  G10 设备绑定 + 能力位 → 明确报错（当前不支持）
  G11 点赞码 + 限台数 → 可组合
  G12 caps_labels 显示正确
  G13 UI：未授权时点赞列显示「—」，表头带 🔒
  G14 UI：已授权时显示真实数字，表头无 🔒
  G15 UI：未授权点点赞表头 → 弹购买引导（不排序）
  G16 UI：已授权点点赞表头 → 正常排序
  G17 看板：_has_like_stats 遍历多码叠加判定
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


# ══════════ 确保密钥注入（license 测试必须）══════════
sec = ROOT / ".license_secret"
if sec.exists():
    py = str(ROOT.parent / ".venv" / "Scripts" / "python.exe")
    code = ("import sys, pathlib; sys.path.insert(0,'.')\n"
            "from sphdl import license as lic\n"
            "s = bytes.fromhex(pathlib.Path('.license_secret').read_text().strip())\n"
            "pathlib.Path('sphdl/_lic_key.py').write_text("
            "'K = %r\\n' % lic.obfuscate(s), encoding='utf-8')\n")
    subprocess.run([py, "-c", code], cwd=str(ROOT), capture_output=True)

import sphdl.license as L

print("=" * 74)
print("G1-G5 码型生成与解析（老码回归是重点）")
print("=" * 74)
check("前置：密钥可用", bool(L.get_secret()))


def mk(**kw):
    c = L.make_code(**kw)
    return c, L.verify_code(c)


# G1
c5, i5 = mk(tier=2000, product=L.PRODUCT_VIDEO, caps=L.CAP_LIKE_STATS)
print(f"  ver=5 码: {c5[:38]}...")
print(f"    ver={i5['ver']} tier={i5['tier']} caps={i5['caps']}")
check("G1a 生成 ver=5", i5["ver"] == 5, str(i5["ver"]))
check("G1b caps 位正确", i5["caps"] == L.CAP_LIKE_STATS, str(i5["caps"]))
check("G1c 仍是视频号产品（不是新产品线）",
      i5["product"] == L.PRODUCT_VIDEO, str(i5["product"]))
check("G1d 档位保留 2000", i5["tier"] == 2000, str(i5["tier"]))

# G2 老码
c1, i1 = mk(tier=500)
print(f"  ver=1 老码: ver={i1['ver']} tier={i1['tier']} caps={i1['caps']}")
check("G2a ★ver=1 老码仍可解析", i1 is not None and i1["ver"] == 1)
check("G2b ★老码 caps=0（无点赞能力）", i1["caps"] == 0, str(i1["caps"]))
check("G2c ★老码档位正确", i1["tier"] == 500, str(i1["tier"]))

# G3 ver=2 三产品
for pname, prod in (("视频号", L.PRODUCT_VIDEO),
                    ("公众号", L.PRODUCT_MP_ARTICLE),
                    ("抖音", L.PRODUCT_DOUYIN)):
    tier = 2000 if prod == L.PRODUCT_VIDEO else 0
    c, i = mk(tier=tier, product=prod)
    print(f"  ver=2 {pname}: ver={i['ver']} prod={i['product']} caps={i['caps']}")
    check(f"G3 ver=2 {pname}仍可解析", i is not None and i["product"] == prod)
    check(f"G3b ver=2 {pname} caps=0", i["caps"] == 0, str(i["caps"]))

# G4 ver=3
c3, i3 = mk(tier=500, product=L.PRODUCT_VIDEO, device="163145d5")
print(f"  ver=3 设备绑定: ver={i3['ver']} bound={i3['device_bound']} caps={i3['caps']}")
check("G4 ★ver=3 设备绑定码仍可解析",
      i3 is not None and i3["ver"] == 3 and i3["device_bound"] is True)
check("G4b ver=3 caps=0", i3["caps"] == 0)

# G5 ver=4
c4, i4 = mk(tier=1000, product=L.PRODUCT_VIDEO, max_devices=3)
print(f"  ver=4 限台数: ver={i4['ver']} maxdev={i4['max_devices']} caps={i4['caps']}")
check("G5 ★ver=4 限台数码仍可解析",
      i4 is not None and i4["ver"] == 4 and i4["max_devices"] == 3)
check("G5b ver=4 caps=0", i4["caps"] == 0)

print()
print("=" * 74)
print("G6-G7 能力位判定与叠加")
print("=" * 74)
check("G6a 无点赞码 has_capability=False",
      L.has_capability(i4, L.CAP_LIKE_STATS) is False)
check("G6b 点赞码 has_capability=True",
      L.has_capability(i5, L.CAP_LIKE_STATS) is True)
check("G6c is_like_stats_product 正确",
      L.is_like_stats_product(i5) is True
      and L.is_like_stats_product(i1) is False)
check("G6d None 安全", L.has_capability(None, L.CAP_LIKE_STATS) is False)

# G7 叠加语义
c_main, i_main = mk(tier=1000, product=L.PRODUCT_VIDEO)          # 只有下载
c_like, i_like = mk(tier=1000, product=L.PRODUCT_VIDEO,
                    caps=L.CAP_LIKE_STATS)                        # 带点赞
print(f"  主码 caps={i_main['caps']}  点赞码 caps={i_like['caps']}")
check("G7a 主码不能给点赞能力",
      not L.has_capability(i_main, L.CAP_LIKE_STATS))
check("G7b 点赞码能（叠加后可用）",
      L.has_capability(i_like, L.CAP_LIKE_STATS))
check("G7c 两者都是视频号产品（可共存）",
      i_main["product"] == i_like["product"] == L.PRODUCT_VIDEO)
check("G7d 只买点赞码也含下载权限（同产品线）",
      L.has_feature(i_like, L.PRODUCT_VIDEO) is True)

print()
print("=" * 74)
print("G8-G11 安全边界")
print("=" * 74)
# G8 篡改
body = c_main.replace("SPHDL-", "").replace("-", "")
raw = bytearray(L.b32decode(body))
raw[4] = 1          # 把 caps 改成有值
fake = "SPHDL-" + L._group(L.b32encode(bytes(raw)))
print(f"  篡改 caps 后: {L.verify_code(fake)}")
check("G8 ★篡改 caps 被 MAC 拒绝", L.verify_code(fake) is None)

# G9 未知能力位
try:
    L.make_code(tier=500, caps=0x80)
    check("G9 未知能力位被拒绝", False, "没报错")
except ValueError as e:
    check("G9 未知能力位被拒绝", True, str(e)[:40])

# G10 设备绑定 + 能力位
try:
    L.make_code(tier=500, caps=L.CAP_LIKE_STATS, device="163145d5")
    check("G10 设备绑定+能力位明确报错", False, "没报错")
except ValueError as e:
    check("G10 设备绑定+能力位明确报错", True, str(e)[:44])

# G11 点赞 + 限台数
c11, i11 = mk(tier=2000, product=L.PRODUCT_VIDEO,
              caps=L.CAP_LIKE_STATS, max_devices=2)
print(f"  点赞+限台数: ver={i11['ver']} caps={i11['caps']} maxdev={i11['max_devices']}")
check("G11a 可组合", i11["ver"] == 5 and i11["caps"] == 1)
check("G11b 台数限制生效", i11["max_devices"] == 2
      and i11["device_limited"] is True)

# G12
print(f"  caps_labels: {L.caps_labels(i5)} / {L.caps_labels(i1)}")
check("G12 能力名正确",
      L.caps_labels(i5) == ["点赞增强包"] and L.caps_labels(i1) == [],
      str(L.caps_labels(i5)))

print()
print("=" * 74)
print("G13-G17 UI 三态")
print("=" * 74)
import tkinter as tk
from tkinter import ttk
import sphdl.gui as G


def make_app(codes):
    """codes: list → 按**真实格式**写成 dict（键=产品线，值=码）。

    ★ 真实 config.json 里 license_codes 是 dict：
        {"1": "SPHDL-...", "2": "SPHDL-..."}
      第一版测试写的是 list，导致「已授权」分支全挂 —— 测试跟着实现一起错了。
      这里改成真实格式，才验得出问题。
    """
    d = Path(tempfile.mkdtemp(prefix="cap_"))
    G.CONFIG_FILE = d / "config.json"
    G.RECORDS_FILE = d / "records.json"
    codes_dict = {str(i + 1): c for i, c in enumerate(codes)}
    G.CONFIG_FILE.write_text(json.dumps({"license_codes": codes_dict}),
                             encoding="utf-8")
    G.RECORDS_FILE.write_text("[]", encoding="utf-8")
    app = G.App()
    app.root.deiconify()
    app.root.geometry("1300x760+20+20")
    for _ in range(8):
        app.root.update()
        time.sleep(0.06)
    return app


def add_rows(app, lk=8888):
    r = G.Record(id="t1", title="测试视频", url="http://x/1.mp4",
                 decode_key="", size=1000, source="test",
                 created="10-27 12:00:00", status="waiting", seq=1,
                 like_count=lk)
    app.store.add(r)
    try:
        app._add_row(r)
    except Exception:
        pass
    app.root.update()
    return r


# ── 未授权 ──
app = make_app([])
app._like_stats_ok = app._has_like_stats()
app._refresh_like_column()
app.root.update()
print(f"  未授权: _has_like_stats={app._like_stats_ok} "
      f"表头={app.tree.heading('like','text')!r}")
check("G13a 未授权 _has_like_stats=False", app._like_stats_ok is False)
check("G13b 表头带 🔒", "🔒" in app.tree.heading("like", "text"),
      app.tree.heading("like", "text"))
r = add_rows(app)
iid = app._row_of.get("t1")
vals = app.tree.item(iid, "values") if iid else ()
print(f"  未授权点赞单元格: {vals[6] if len(vals) > 6 else '?'}")
check("G13c 未授权显示「—」（不泄露数据）",
      len(vals) > 6 and vals[6] == "—", str(vals[6] if len(vals) > 6 else "?"))

# G15 未授权点表头 → 弹框（用 monkeypatch 记录，避免阻塞）
clicked = {"n": 0}
import tkinter.messagebox as MB
_orig_ask = MB.askyesno
G.messagebox.askyesno = lambda *a, **k: (clicked.__setitem__("n", clicked["n"] + 1), False)[1]
before_sort = app._sort_col
app._on_tree_sort("like")
app.root.update()
print(f"  未授权点点赞表头: 弹框次数={clicked['n']} 排序列={before_sort}→{app._sort_col}")
check("G15a 未授权点点赞表头会弹购买引导", clicked["n"] >= 1, str(clicked["n"]))
check("G15b 且不执行排序", app._sort_col == before_sort,
      f"{before_sort} → {app._sort_col}")
G.messagebox.askyesno = _orig_ask
app.root.destroy()

# ── 已授权 ──
app2 = make_app([c_like])
app2._like_stats_ok = app2._has_like_stats()
app2._refresh_like_column()
app2.root.update()
print(f"  已授权: _has_like_stats={app2._like_stats_ok} "
      f"表头={app2.tree.heading('like','text')!r}")
check("G14a 已授权 _has_like_stats=True", app2._like_stats_ok is True)
check("G14b 表头无 🔒", "🔒" not in app2.tree.heading("like", "text"),
      app2.tree.heading("like", "text"))
r2 = add_rows(app2, lk=8888)
iid2 = app2._row_of.get("t1")
vals2 = app2.tree.item(iid2, "values") if iid2 else ()
print(f"  已授权点赞单元格: {vals2[6] if len(vals2) > 6 else '?'}")
check("G14c 已授权显示真实数字",
      len(vals2) > 6 and vals2[6] == "8888", str(vals2[6] if len(vals2) > 6 else "?"))

# G16 已授权能排序
app2._sort_col = None
app2._on_tree_sort("like")
app2.root.update()
print(f"  已授权点点赞表头: 排序列={app2._sort_col}")
check("G16 已授权可正常排序", app2._sort_col == "like", str(app2._sort_col))

# G17 多码叠加
app3 = make_app([c_main, c_like])
app3._like_stats_ok = app3._has_like_stats()
app3.root.update()
print(f"  双码叠加: _has_like_stats={app3._like_stats_ok}")
check("G17 ★双码叠加（主码+点赞码）能识别点赞能力",
      app3._like_stats_ok is True)
app3.root.destroy()

# 反向：只有当主码
app4 = make_app([c_main])
app4._like_stats_ok = app4._has_like_stats()
app4.root.update()
print(f"  只有主码: _has_like_stats={app4._like_stats_ok}")
check("G17b 只有主码时点赞锁定", app4._like_stats_ok is False)
app4.root.destroy()

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
