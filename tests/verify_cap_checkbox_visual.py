"""实测「点赞增强包」勾选框是否显示 ✓（v2.6.2）。

═══ 客户报障（2026-10-27）═══
「点击点赞增强包（点赞/转发/推荐展示 + 按点赞排序），前方的圆框要出现√」

═══ 根因 ═══
全局 CSS：`input,select{... -webkit-appearance:none}`
这条把 **checkbox 的原生勾选标记** 一起干掉了 —— 点上去看不到 ✓
（原生外观被禁用，又没给自定义勾）。

═══ 测试策略（不依赖登录）═══
从 `server/sphdl_admin.py` 里**原样抽出真实 CSS 与表单片段**，
组成一个临时 HTML 用真浏览器（Edge）打开，然后：
  1. 找 cap_like 复选框，读计算样式（appearance 不能是 none）
  2. 截图 → 点击 → 再截图，像素必须有可见差异（= 出现 ✓）
  3. 验证点文字也能勾（整行可点）
  4. 验证 value=1（后台能识别）

★ 为什么抽真实源码而不是手写 CSS：
  手写就变成"测我自己写的样式"，测不出线上那行
  `-webkit-appearance:none` 的副作用。必须用同一份字符串。
"""
import re
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


SRV = (ROOT / "server" / "sphdl_admin.py").read_text(encoding="utf-8")

print("=" * 74)
print("F0 源码级：复选框 CSS 规则存在")
print("=" * 74)
has_global_none = "-webkit-appearance:none" in SRV
has_cb_override = "input[type=checkbox]" in SRV
print(f"  全局 input 规则含 -webkit-appearance:none: {has_global_none}")
print(f"  存在 input[type=checkbox] 覆盖: {has_cb_override}")
check("F0a 存在复选框专属覆盖规则（否则 ✓ 会被吃掉）", has_cb_override)
# 覆盖规则里必须把 appearance 恢复成非 none
m = re.search(r"input\[type=checkbox\]\{(.*?)\}", SRV, re.S)
cb_css = m.group(1) if m else ""
print(f"  覆盖规则内容: {' '.join(cb_css.split())[:120]}")
check("F0b 覆盖规则恢复了 appearance",
      "appearance:auto" in cb_css.replace(" ", ""),
      "appearance:auto")
check("F0c 设置了 accent-color（勾用品牌色）",
      "accent-color" in cb_css)
check("F0d 有 capbox 整行可点样式", ".capbox" in SRV)

print()
print("=" * 74)
print("组装临时页面（CSS + 表单片段都取自真实源码）")
print("=" * 74)
# ★ 真实 CSS 是模块级常量 CSS = """..."""，page() 里用 {CSS} 插入。
#   第一版我按 <style>...</style> 抽，抽到空串 → 页面裸奔、
#   F3「appearance=auto」白白通过（没 CSS 当然不是 none）—— 假绿。
st = re.search(r'^CSS = """(.*?)"""', SRV, re.S | re.M)
css = st.group(1) if st else ""
print(f"  抽到真实 CSS {len(css)} 字符")
check("F0e ★抽到真实 CSS（否则整轮测试都是假绿）", len(css) > 2000,
      f"{len(css)} 字符")
check("F0e2 抽到的 CSS 含全局 input 规则",
      "input,select{" in css.replace("\n", " "))
check("F0e3 抽到的 CSS 含 checkbox 覆盖规则",
      "input[type=checkbox]" in css)

# 抽出 capbox 表单片段（可能跨行）
fm = re.search(r"(<label>增值功能.*?</label>\s*<label class=\"capbox\">.*?</label>)",
               SRV, re.S)
form_html = fm.group(1) if fm else ""
print(f"  抽到表单片段 {len(form_html)} 字符")
check("F0f 抽到真实表单片段", "cap_like" in form_html, str(len(form_html)))

page_html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>{css}</style></head><body>
<div class="card"><h3>发码</h3>
<form method="post" action="/gen">
{form_html}
<button>生成授权码</button>
</form></div></body></html>"""

tmp = Path(tempfile.mkdtemp(prefix="capbox_"))
html_file = tmp / "gen.html"
html_file.write_text(page_html, encoding="utf-8")
print(f"  临时页面: {html_file}")

print()
print("=" * 74)
print("F1-F11 真实浏览器验证")
print("=" * 74)
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    b = pw.chromium.launch(channel="msedge", headless=True)
    ctx = b.new_context(viewport={"width": 460, "height": 720})
    pg = ctx.new_page()
    pg.goto(html_file.as_uri(), wait_until="load", timeout=20000)

    cb = pg.query_selector("input[name=cap_like]")
    check("F1 页面存在 cap_like 复选框", cb is not None)
    if cb is None:
        b.close()
        raise SystemExit(1)

    info = pg.evaluate("""() => {
        const el = document.querySelector('input[name=cap_like]');
        const cs = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return {
            type: el.type, name: el.name, value: el.value,
            appearance: cs.webkitAppearance || cs.appearance,
            accent: cs.accentColor,
            w: Math.round(r.width), h: Math.round(r.height),
            checked: el.checked,
            inLabel: !!el.closest('label'),
            labelClass: (el.closest('label')||{}).className || ''
        };
    }""")
    print(f"  复选框: {info}")

    check("F2 type=checkbox", info["type"] == "checkbox", info["type"])
    check("F3 ★appearance 不是 none（关键：否则看不到 ✓）",
          str(info["appearance"]).lower() != "none", str(info["appearance"]))
    check("F4 accent-color 非 auto", info["accent"] not in ("", "auto"),
          str(info["accent"]))
    check("F5 尺寸 ≥16px", info["w"] >= 16 and info["h"] >= 16,
          f"{info['w']}x{info['h']}")
    check("F6 包在 label 里（整行可点）",
          info["inLabel"] and "capbox" in str(info["labelClass"]),
          str(info["labelClass"]))
    check("F7 初始未勾选", info["checked"] is False)
    check("F8 value=1（后台 form.get 能识别）",
          str(info["value"]) == "1", str(info["value"]))

    # 截图对比
    box = cb.bounding_box()
    clip = {"x": max(box["x"] - 10, 0), "y": max(box["y"] - 10, 0),
            "width": min(box["width"] + 320, 440),
            "height": box["height"] + 20}
    before = tmp / "before.png"
    pg.screenshot(path=str(before), clip=clip)

    cb.click()
    pg.wait_for_timeout(400)
    checked = pg.evaluate(
        "() => document.querySelector('input[name=cap_like]').checked")
    print(f"  点击后 checked = {checked}")
    check("F9 点击后变为已勾选", checked is True)

    after = tmp / "after.png"
    pg.screenshot(path=str(after), clip=clip)

    from PIL import Image
    i1 = Image.open(before).convert("RGB")
    i2 = Image.open(after).convert("RGB")
    w = min(i1.width, i2.width)
    h = min(i1.height, i2.height)
    p1, p2 = i1.load(), i2.load()
    diff = 0
    for y in range(h):
        for x in range(w):
            if p1[x, y] != p2[x, y]:
                diff += 1
    pct = diff / max(w * h, 1) * 100
    print(f"  勾选前后像素差异: {diff}px ({pct:.2f}%)")
    check("F10 ★勾选前后画面有变化（出现 ✓）", diff >= 30, f"{diff}px")

    # 只截复选框本身，看勾有没有画出来
    cb_before = tmp / "cb_before.png"
    cb_after = tmp / "cb_after.png"
    pg.evaluate("() => document.querySelector('input[name=cap_like]').checked = false")
    pg.wait_for_timeout(200)
    cb.screenshot(path=str(cb_before))
    pg.click("input[name=cap_like]")
    pg.wait_for_timeout(300)
    cb.screenshot(path=str(cb_after))
    a1 = Image.open(cb_before).convert("RGB")
    a2 = Image.open(cb_after).convert("RGB")
    n = 0
    q1, q2 = a1.load(), a2.load()
    for y in range(min(a1.height, a2.height)):
        for x in range(min(a1.width, a2.width)):
            if q1[x, y] != q2[x, y]:
                n += 1
    print(f"  复选框本体像素差异: {n}px（尺寸 {a1.size}）")
    check("F11 ★复选框本体画出勾（非整行高亮而已）", n >= 10, f"{n}px")

    # 点文字也能勾
    pg.evaluate("() => document.querySelector('input[name=cap_like]').checked = false")
    pg.wait_for_timeout(200)
    pg.click("text=点赞增强包")
    pg.wait_for_timeout(300)
    by_txt = pg.evaluate(
        "() => document.querySelector('input[name=cap_like]').checked")
    print(f"  点文字后 checked = {by_txt}")
    check("F12 点文字也能勾选（整行可点）", by_txt is True)

    # 留一张完整截图给人看
    pg.evaluate("() => document.querySelector('input[name=cap_like]').checked = true")
    pg.wait_for_timeout(200)
    full = tmp / "final.png"
    pg.screenshot(path=str(full))
    print(f"\n  截图目录: {tmp}")
    b.close()

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
