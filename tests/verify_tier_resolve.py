#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""档位解析回归测试（v2.0.19）—— 客户实测 bug 的防复发。

2026-10-17 客户报告：粘了**公众号授权码**后，列表上限从 500 变成 0。
根因：公众号码的 tier = 0（产品不分档），而 _resolve_tier() 直接
      返回 info["tier"] → store.max_records = 0 → 界面「上限 0 条」，
      逻辑上等于一条视频都抓不进来。

本测试锁死正确行为：
  T1 未授权 → 默认档 500
  T2 视频号各档码 → 对应档位
  T3 ★公众号码 → 回落到默认档，绝不能是 0
  T4 ★公众号码不能让 is_full() 恒真
  T5 视频号码 → 公众号 → 视频号码 反复切换，档位每次都正确
  T6 清除授权 → 回默认档
  T7 公众号码下视频号仍可正常入库到默认档上限

跑法：python tests/verify_tier_resolve.py
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


def pump(app, ms=200):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        try:
            app.root.update()
        except Exception:
            break
        time.sleep(0.005)


# 准备密钥
from sphdl import license as lic  # noqa: E402
sec = (ROOT / ".license_secret").read_text(encoding="utf-8").strip()
(ROOT / "sphdl" / "_lic_key.py").write_text(
    '"""t"""\nK = %r\n' % lic.obfuscate(bytes.fromhex(sec)), encoding="utf-8")
import importlib  # noqa: E402
importlib.reload(lic)

import tkinter as tk  # noqa: E402
rt = tk.Tk(); rt.withdraw(); rt.destroy()

import sphdl.gui as G  # noqa: E402
import sphdl.config as C  # noqa: E402

tdir = Path(tempfile.mkdtemp(prefix="tierfix_"))
G.RECORDS_FILE = tdir / "records.json"
G.CONFIG_FILE = tdir / "config.json"
C.PROXY_BACKUP_FILE = tdir / "proxy_backup.json"
(tdir / "config.json").write_text(json.dumps({
    "port": 8899, "download_dir": str(tdir / "dl"),
    "auto_proxy": False, "auto_download": False,
    "download_images": True, "natural_capture": True,
    "guide_auto_shown": True}), encoding="utf-8")

app = G.App()
try:
    pump(app, 300)
    DEFAULT = app.cfg.default_max_records

    CODE_V500 = lic.make_code(500, product=lic.PRODUCT_VIDEO)
    CODE_V1000 = lic.make_code(1000, product=lic.PRODUCT_VIDEO)
    CODE_V2000 = lic.make_code(2000, product=lic.PRODUCT_VIDEO)
    CODE_V20K = lic.make_code(20000, product=lic.PRODUCT_VIDEO)
    CODE_MP = lic.make_code(0, product=lic.PRODUCT_MP_ARTICLE)
    CODE_OLD = "SPHDL-0420-07S0-G286-DTC2-J7DH-401W"   # 已发出的 ver=1 老码

    # ---------- T1 ----------
    print("=" * 74)
    print("T1 未授权 → 默认档")
    print("=" * 74)
    check("T1a _resolve_tier 返回默认档",
          app._resolve_tier() == DEFAULT, str(app._resolve_tier()))
    check("T1b store.max_records = 默认档",
          app.store.max_records == DEFAULT, str(app.store.max_records))
    check("T1c ★默认档 = 0（v2.1.7 未授权，可捕获不能下载）", DEFAULT == 0, str(DEFAULT))

    # ---------- T2 ----------
    print("\n" + "=" * 74)
    print("T2 视频号各档码 → 对应档位")
    print("=" * 74)
    for code, want in [(CODE_V500, 500), (CODE_V1000, 1000),
                       (CODE_V2000, 2000), (CODE_V20K, 20000),
                       (CODE_OLD, 20000)]:
        app._activate_license(code)
        pump(app, 150)
        check(f"T2 视频号码 → {want}",
              app.store.max_records == want, str(app.store.max_records))

    # ---------- T3 ★核心 bug ----------
    print("\n" + "=" * 74)
    print("T3 ★公众号码 → 绝不能变成 0")
    print("=" * 74)
    # 先回到默认（清授权）
    app._clear_license()
    pump(app, 150)
    before = app.store.max_records
    ok = app._activate_license(CODE_MP)
    pump(app, 200)
    check("T3a 公众号码激活成功", ok is True)
    check("T3b ★公众号码不让上限变负数/异常（回落到默认档）",
          app.store.max_records == DEFAULT, str(app.store.max_records))
    check("T3c ★回落到默认档",
          app.store.max_records == DEFAULT, str(app.store.max_records))
    check("T3d _resolve_tier 与 store 一致（都是默认档）",
          app._resolve_tier() == DEFAULT, str(app._resolve_tier()))
    check("T3e 公众号功能已解锁",
          lic.is_mp_product(app._license_info()) is True)

    # ---------- T4 ----------
    print("\n" + "=" * 74)
    print("T4 ★公众号码下 is_full 不能恒真")
    print("=" * 74)
    check("T4a is_full 为 False（列表空）", app.store.is_full() is False)
    # 加一条视频号记录，验证能入库
    from sphdl.gui import Record
    r1 = Record(id="q1", title="测试", url="http://x", decode_key="",
                size=1, source="测试", created="2026-10-17 11:00:00",
                platform="视频号", video_id="qv1")
    check("T4b ★公众号码下视频号仍可入库", app.store.add(r1) is True)

    # ---------- T5 ----------
    print("\n" + "=" * 74)
    print("T5 反复切换：视频号 → 公众号 → 视频号")
    print("=" * 74)
    app._activate_license(CODE_V2000)
    pump(app, 150)
    check("T5a 切到专业版 → 2000", app.store.max_records == 2000,
          str(app.store.max_records))
    app._activate_license(CODE_MP)
    pump(app, 150)
    # ★ 正确语义：公众号码**不影响**视频号已买的档位（仍是 2000），
    #   但绝不能变成 0。之前测试期望写错了（以为是回落到默认档）。
    check("T5b ★切到公众号 → 保持 2000，且不是 0",
          app.store.max_records == 2000, str(app.store.max_records))
    check("T5b2 ★公众号产品已解锁",
          lic.is_mp_product(app._license_info(lic.PRODUCT_MP_ARTICLE)) is True)
    check("T5b3 ★视频号授权仍在（未被顶掉）",
          app._license_info(lic.PRODUCT_VIDEO) is not None)
    app._activate_license(CODE_V1000)
    pump(app, 150)
    check("T5c 再切回标准版 → 1000", app.store.max_records == 1000,
          str(app.store.max_records))

    # ---------- T6 ----------
    print("\n" + "=" * 74)
    print("T6 清除授权 → 回默认档")
    print("=" * 74)
    app._clear_license()
    pump(app, 150)
    check("T6a 清除后回默认档", app.store.max_records == DEFAULT,
          str(app.store.max_records))
    check("T6b 清除后回落到默认档 0（未授权）", app.store.max_records == 0,
          str(app.store.max_records))

    # ---------- T7 ----------
    print("\n" + "=" * 74)
    print("T7 公众号码下，视频号能正常加到默认档上限")
    print("=" * 74)
    app._clear_license()          # 先清空所有授权
    pump(app, 150)
    app._activate_license(CODE_MP)  # 只买公众号
    pump(app, 150)
    check("T7pre 只买公众号 → 档位为默认档",
          app.store.max_records == DEFAULT, str(app.store.max_records))
    app.store.items.clear()
    app.store.order.clear()
    # ★ v2.1.7：默认档 0 = 未授权，捕获上限由 UNLICENSED_CAPTURE_LIMIT 决定
    cap = DEFAULT if DEFAULT > 0 else C.UNLICENSED_CAPTURE_LIMIT
    added = 0
    for i in range(cap + 5):
        rec = Record(id=f"z{i}", title=f"v{i}", url=f"http://x/{i}",
                     decode_key="", size=1, source="测试",
                     created=f"2026-10-17 11:{i//60:02d}:{i%60:02d}",
                     platform="视频号", video_id=f"zv{i}")
        if app.store.add(rec):
            added += 1
    check(f"T7a ★恰好加到 {cap} 条（未授权捕获上限）", added == cap,
          f"实际 {added}")
    check("T7b 达到上限后 is_full = True", app.store.is_full() is True)

    # ---------- T8 ★两条产品线互不覆盖（客户实测 bug） ----------
    print("\n" + "=" * 74)
    print("T8 ★视频号 / 公众号 授权互不覆盖")
    print("=" * 74)
    app._clear_license()
    pump(app, 150)

    app._activate_license(CODE_MP)
    pump(app, 150)
    check("T8a 先买公众号 → 公众号已解锁",
          lic.is_mp_product(app._license_info(lic.PRODUCT_MP_ARTICLE)) is True)
    check("T8b 此时视频号未授权",
          app._license_info(lic.PRODUCT_VIDEO) is None)

    app._activate_license(CODE_V2000)
    pump(app, 150)
    check("T8c ★再买视频号 → 视频号档位 2000",
          app.store.max_records == 2000, str(app.store.max_records))
    check("T8d ★公众号授权没被顶掉（关键 bug 修复）",
          lic.is_mp_product(app._license_info(lic.PRODUCT_MP_ARTICLE)) is True)
    check("T8e 公众号按钮仍解锁",
          app._refresh_mp_button() is True)

    # 反过来：再激活一个公众号码，视频号档位不能丢
    app._activate_license(lic.make_code(0, product=lic.PRODUCT_MP_ARTICLE))
    pump(app, 150)
    check("T8f ★换公众号码后视频号档位仍是 2000",
          app.store.max_records == 2000, str(app.store.max_records))
    check("T8g 视频号授权仍在",
          app._license_info(lic.PRODUCT_VIDEO) is not None)

    # 单独清除公众号，视频号不受影响
    app._clear_license(lic.PRODUCT_MP_ARTICLE)
    pump(app, 150)
    check("T8h ★单独清公众号 → 公众号锁定",
          app._license_info(lic.PRODUCT_MP_ARTICLE) is None)
    check("T8i ★单独清公众号 → 视频号仍是 2000",
          app.store.max_records == 2000, str(app.store.max_records))

finally:
    try:
        app.root.destroy()
    except Exception:
        pass
    shutil.rmtree(tdir, ignore_errors=True)
    (ROOT / "sphdl" / "_lic_key.py").unlink(missing_ok=True)

print()
ok = sum(1 for _, p, _ in RESULTS if p)
print(f"RESULT: {'ALL PASS' if ok == len(RESULTS) else 'FAIL'} ({ok}/{len(RESULTS)})")
for n, p, _ in RESULTS:
    if not p:
        print("  未通过: " + n)
sys.exit(0 if ok == len(RESULTS) else 1)
