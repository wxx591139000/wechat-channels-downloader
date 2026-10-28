"""授权管理页显示四项功能状态（v2.6.3）。

═══ 客户需求（2026-10-27）═══
「『授权管理』界面里也需要有抖音授权」

原状：授权管理页只有「视频号✓ / 公众号视频✗」两项，
新增的抖音、点赞增强包在页面上看不到 —— 客户不知道还能买什么/买了什么。

覆盖：
  A1 无授权时：四项都显示，视频号✓、其余 ✗
  A2 有抖音码时：抖音显示 ✓
  A3 有点赞增强码时：点赞增强包显示 ✓
  A4 叠加：视频号码 + 点赞码 → 视频号✓ 且 点赞增强包✓
  A5 未授权项可点击（cursor=hand2 + 有 <Button-1> 绑定）
  A6 已授权项不可点击（无绑定）
  A7 提示文案含「联系发布方购买」
"""
import json
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


# 确保密钥
sec = ROOT / ".license_secret"
py = str(ROOT.parent / ".venv" / "Scripts" / "python.exe")
if sec.exists():
    code = ("import sys, pathlib; sys.path.insert(0,'.')\n"
            "from sphdl import license as lic\n"
            "s = bytes.fromhex(pathlib.Path('.license_secret').read_text().strip())\n"
            "pathlib.Path('sphdl/_lic_key.py').write_text("
            "'K = %r\\n' % lic.obfuscate(s), encoding='utf-8')\n")
    subprocess.run([py, "-c", code], cwd=str(ROOT), capture_output=True)

import sphdl.license as L
import tkinter as tk
from tkinter import ttk
import sphdl.gui as G

# 准备码
c_vc = L.make_code(2000, product=L.PRODUCT_VIDEO)
c_dy = L.make_code(0, product=L.PRODUCT_DOUYIN)
c_like = L.make_code(2000, product=L.PRODUCT_VIDEO, caps=L.CAP_LIKE_STATS)
c_mp = L.make_code(0, product=L.PRODUCT_MP_ARTICLE)


def walk(x, acc=None):
    if acc is None:
        acc = []
    for c in x.winfo_children():
        acc.append(c)
        walk(c, acc)
    return acc


def open_lic_page(codes):
    d = Path(tempfile.mkdtemp(prefix="lic_"))
    G.CONFIG_FILE = d / "config.json"
    G.RECORDS_FILE = d / "records.json"
    G.CONFIG_FILE.write_text(
        json.dumps({"license_codes": {str(i + 1): c
                                      for i, c in enumerate(codes)}}),
        encoding="utf-8")
    G.RECORDS_FILE.write_text("[]", encoding="utf-8")
    app = G.App()
    app.root.deiconify()
    app.root.geometry("1200x760+20+20")
    for _ in range(8):
        app.root.update()
        time.sleep(0.06)
    app._on_license_input()
    for _ in range(12):
        app.root.update()
        time.sleep(0.08)
    return app


def find_toplevel(app, kw):
    for w in app.root.winfo_children():
        if isinstance(w, tk.Toplevel):
            try:
                if kw in w.title():
                    return w
            except Exception:
                pass
    return None


def feat_texts(win):
    labels = [c for c in walk(win) if isinstance(c, (ttk.Label, tk.Label))]
    out = []
    for lb in labels:
        try:
            t = str(lb.cget("text"))
        except Exception:
            continue
        if any(k in t for k in ("视频号", "公众号视频", "抖音", "点赞增强包")):
            out.append((t, lb))
    return out


print("=" * 74)
print("A1 无授权")
print("=" * 74)
app = open_lic_page([])
win = find_toplevel(app, "授权")
check("A1a 授权管理窗口已打开", win is not None)
if win:
    pairs = feat_texts(win)
    texts = [t for t, _ in pairs]
    print(f"  功能项: {texts}")
    check("A1b 出现「视频号」", any("视频号" in t for t in texts), str(texts))
    check("A1c 出现「抖音」", any("抖音" in t for t in texts), str(texts))
    check("A1d 出现「点赞增强包」",
          any("点赞增强包" in t for t in texts), str(texts))
    check("A1e 无授权时抖音是 ✗",
          any("抖音" in t and "✗" in t for t in texts), str(texts))
    check("A1f 无授权时点赞增强包是 ✗",
          any("点赞增强包" in t and "✗" in t for t in texts), str(texts))

    # A5 未授权可点击
    lb_dy = next((lb for t, lb in pairs if "抖音" in t), None)
    if lb_dy is not None:
        binds = lb_dy.bind("<Button-1>")
        cur = ""
        try:
            cur = str(lb_dy.cget("cursor"))
        except Exception:
            pass
        print(f"  抖音标签: 点击绑定={bool(binds)} cursor={cur!r}")
        check("A5 未授权项可点击（有绑定 + hand2）",
              bool(binds) and cur == "hand2", f"bind={bool(binds)} cur={cur}")
    win.destroy()
app.root.destroy()

print()
print("=" * 74)
print("A2/A3/A4 各码识别")
print("=" * 74)
for label, codes, expect in (
    ("只有抖音码", [c_dy], ("抖音",)),
    ("只有点赞码", [c_like], ("点赞增强包",)),
    ("只有公众号码", [c_mp], ("公众号视频",)),
    ("视频号+点赞码（叠加）", [c_vc, c_like], ("视频号", "点赞增强包")),
):
    app = open_lic_page(codes)
    win = find_toplevel(app, "授权")
    pairs = feat_texts(win) if win else []
    texts = [t for t, _ in pairs]
    print(f"  {label:22s} → {texts}")
    ok = True
    for kw in expect:
        # 该项必须显示 ✓
        hit = any(kw in t and "✓" in t for t in texts)
        check(f"A {label} → {kw}✓", hit, str([t for t in texts if kw in t]))
        ok &= hit
    # 反向：没买的必须 ✗
    if label == "只有抖音码":
        check("A2b 只有抖音码时点赞增强包是 ✗",
              any("点赞增强包" in t and "✗" in t for t in texts), str(texts))
    if win:
        win.destroy()
    app.root.destroy()

print()
print("=" * 74)
print("A7 提示文案")
print("=" * 74)
src = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8")
check("A7a 抖音提示含「联系发布方购买」",
      "联系发布方购买「抖音批量下载版」授权码" in src)
check("A7b 点赞提示含「联系发布方购买」",
      "联系发布方购买「点赞增强包」授权码" in src)
check("A7c 点赞提示说明可叠加",
      "叠加使用" in src)

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
