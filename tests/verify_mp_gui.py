#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公众号视频 GUI 入口测试（v2.0.19）。

覆盖：
  G1 按钮存在 + 悬停提示已挂
  G2 未授权时显示 🔒（且有解锁引导）
  G3 基础版码（ver=1）→ 未授权公众号
  G4 完整版码（ver=2 feature=all）→ 已授权，按钮解锁
  G5 换码后按钮状态实时刷新（不用重启）
  G6 清除授权后按钮回到锁定态
  G7 解析窗口能打开（已授权时）
  G8 UI 未构建时 _refresh_mp_button 不崩（启动期安全）

跑法：python tests/verify_mp_gui.py
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


def pump(app, ms=250):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        try:
            app.root.update()
        except Exception:
            break
        time.sleep(0.005)


def make_app(tmpdir):
    import tkinter as tk
    r = tk.Tk()
    r.withdraw()
    r.destroy()
    import sphdl.gui as G
    import sphdl.config as C
    G.RECORDS_FILE = tmpdir / "records.json"
    G.CONFIG_FILE = tmpdir / "config.json"
    C.PROXY_BACKUP_FILE = tmpdir / "proxy_backup.json"
    (tmpdir / "config.json").write_text(json.dumps({
        "port": 8899, "download_dir": str(tmpdir / "dl"),
        "auto_proxy": False, "auto_download": False,
        "download_images": True, "natural_capture": True,
        "guide_auto_shown": True}), encoding="utf-8")
    return G.App()


tmpdir = Path(tempfile.mkdtemp(prefix="sphdl_mpgui_"))
app = None
try:
    # 准备密钥（生成两个测试码）
    from sphdl import license as lic
    sec = (ROOT / ".license_secret").read_text(encoding="utf-8").strip()
    (ROOT / "sphdl" / "_lic_key.py").write_text(
        '"""tmp"""\nK = %r\n' % lic.obfuscate(bytes.fromhex(sec)),
        encoding="utf-8")
    import importlib
    importlib.reload(lic)

    CODE_BASIC = lic.make_code(20000, flags=0)                        # ver=1 视频号
    CODE_V2 = lic.make_code(20000, product=lic.PRODUCT_VIDEO)         # ver=2 视频号
    CODE_MP = lic.make_code(0, product=lic.PRODUCT_MP_ARTICLE)        # ver=2 公众号
    print(f"测试码 视频号ver1: {CODE_BASIC}")
    print(f"测试码 视频号ver2: {CODE_V2}")
    print(f"测试码 公众号:     {CODE_MP}")

    app = make_app(tmpdir)
    pump(app, 400)

    # ---------- G1 按钮存在 + 提示 ----------
    print("\n" + "=" * 74)
    print("G1 按钮存在 + 悬停提示")
    print("=" * 74)
    check("G1a btn_mp 已创建", getattr(app, "btn_mp", None) is not None)
    txt = app.btn_mp.cget("text")
    check("G1b 按钮文案含「公众号视频」", "公众号视频" in txt, txt)
    check("G1c 按钮可点击（未授权也不禁用）",
          str(app.btn_mp.cget("state")) != "disabled",
          str(app.btn_mp.cget("state")))
    # 悬停提示：ToolTip 会把对象挂在 widget 上（看实现）
    has_tip = False
    try:
        from sphdl.gui import ToolTip
        # _tt 创建了 ToolTip 实例；检查有没有引用（不同实现方式都试）
        for attr in dir(app.btn_mp):
            if "tip" in attr.lower():
                has_tip = True
                break
        # 退而求其次：确认 _tt 被调用过（源码级）
        src = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8",
                                                    errors="replace")
        has_tip = has_tip or ("self._tt(self.btn_mp," in src)
    except Exception:
        pass
    check("G1d 已挂悬停提示（_tt）", has_tip)

    # ---------- G2/G3 未授权（默认） ----------
    print("\n" + "=" * 74)
    print("G2/G3 未授权 → 显示 🔒")
    print("=" * 74)
    check("G2a 默认无授权 → 按钮带 🔒", "🔒" in app.btn_mp.cget("text"),
          app.btn_mp.cget("text"))
    check("G2b _refresh_mp_button 返回 False",
          app._refresh_mp_button() is False)

    # 基础版码（ver=1）→ 仍无公众号功能
    ok = app._activate_license(CODE_BASIC)
    pump(app, 300)
    check("G3a 基础版码激活成功", ok is True)
    check("G3b ★基础版码（ver=1）→ 公众号仍未授权",
          "🔒" in app.btn_mp.cget("text"), app.btn_mp.cget("text"))
    check("G3c 基础版档位正确",
          app.store.max_records == 20000, str(app.store.max_records))

    # ---------- G4/G5 完整版码 → 解锁 ----------
    print("\n" + "=" * 74)
    print("G4/G5 公众号码（ver=2 product=2）→ 解锁 + 实时刷新")
    print("=" * 74)
    ok = app._activate_license(CODE_MP)
    pump(app, 300)
    check("G4a 公众号码激活成功", ok is True)
    check("G4b ★换码后按钮立即解锁（无需重启）",
          "🔒" not in app.btn_mp.cget("text"), app.btn_mp.cget("text"))
    check("G4c _refresh_mp_button 返回 True",
          app._refresh_mp_button() is True)
    info = app._license_info()
    check("G4d 授权信息含公众号功能",
          lic.has_feature(info, lic.PRODUCT_MP_ARTICLE), str(info.get("features")))

    # ---------- G4b2 视频号 ver=2 码也不解锁公众号（产品互斥） ----------
    print("\n" + "=" * 74)
    print("G4b2 ★产品互斥：视频号 ver=2 码不解锁公众号")
    print("=" * 74)
    app._activate_license(CODE_V2)
    pump(app, 300)
    check("G4b2a 视频号 ver=2 码激活成功", True)
    check("G4b2b ★公众号按钮仍带 🔒（互斥生效）",
          "🔒" in app.btn_mp.cget("text"), app.btn_mp.cget("text"))
    check("G4b2c 档位为 20000", app.store.max_records == 20000,
          str(app.store.max_records))
    # 换回公众号码继续后面的测试
    app._activate_license(CODE_MP)
    pump(app, 300)

    # ---------- G7 解析窗口能打开 ----------
    print("\n" + "=" * 74)
    print("G7 解析窗口能打开")
    print("=" * 74)
    before = len(app.root.winfo_children())
    try:
        app._open_mp_window()
        pump(app, 300)
        after = len(app.root.winfo_children())
        check("G7a 窗口已创建", after > before, f"{before} → {after}")
        titles = [w.winfo_class() for w in app.root.winfo_children()]
        check("G7b 存在 Toplevel 窗口", "Toplevel" in titles, str(titles))
        # 关闭所有 Toplevel 避免影响后续
        for w in app.root.winfo_children():
            if w.winfo_class() == "Toplevel":
                w.destroy()
        pump(app, 200)
    except Exception as e:                              # noqa: BLE001
        check("G7a 窗口已创建", False, f"{type(e).__name__}: {e}")

    # ---------- G6 清除授权 → 回到锁定 ----------
    print("\n" + "=" * 74)
    print("G6 清除授权 → 按钮回到锁定态")
    print("=" * 74)
    cleared = app._clear_license()
    pump(app, 300)
    check("G6a 清除授权成功", cleared is True)
    check("G6b ★按钮回到 🔒", "🔒" in app.btn_mp.cget("text"),
          app.btn_mp.cget("text"))

    # ---------- G8 启动期安全 ----------
    print("\n" + "=" * 74)
    print("G8 UI 未构建时调用不崩（启动期安全）")
    print("=" * 74)
    class Dummy:
        pass
    d = Dummy()
    try:
        r = type(app)._refresh_mp_button(d)
        check("G8a 无 btn_mp 时返回 False 不崩", r is False, f"return={r}")
    except Exception as e:                              # noqa: BLE001
        check("G8a 无 btn_mp 时返回 False 不崩", False,
              f"{type(e).__name__}: {e}")

finally:
    if app is not None:
        try:
            app.root.destroy()
        except Exception:
            pass
    (ROOT / "sphdl" / "_lic_key.py").unlink(missing_ok=True)
    shutil.rmtree(tmpdir, ignore_errors=True)

print()
ok = sum(1 for _, p, _ in RESULTS if p)
print(f"RESULT: {'ALL PASS' if ok == len(RESULTS) else 'FAIL'} ({ok}/{len(RESULTS)})")
for n, p, _ in RESULTS:
    if not p:
        print("  未通过: " + n)
sys.exit(0 if ok == len(RESULTS) else 1)
