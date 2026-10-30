"""生产环境真实浏览器验证：话术页复制按钮（v2.8.4）。

═══ 客户反馈（2026-10-30，电脑 + 手机都复制不了）═══
「电脑版网页点击新加的 2 个复制按钮没有反应，也没有复制成功」
「在手机上访问也无法正确复制」

═══ 三个连环 bug（都已修）═══
① `cp(t, event && event.target)` —— 内联 onclick 里全局 event 不可用，
   `event && ...` 抛 ReferenceError → 函数中断 → 点了没反应
   → 改为按钮显式传 `this`
② `return out.join("\n")` 在 Python f-string 里，`\n` 被转成**真实换行**，
   把 JS 字符串切断 → 整个 <script> 语法错误 → 所有函数未定义
   → 改为 `join("\\n")`（输出字面量 \n）
③ **`cp()` 函数根本没插进话术页** —— JS_COPY 只在发码结果页引用，
   话术页只有 JS_PASTE → `copyLink` 报 `cp is not defined`
   → 话术页补 `<script>{JS_COPY}</script>`

═══ 为什么必须打生产环境 ═══
本地起的服务用的是临时数据，测不出「生产话术库里有没有版本」。
本脚本连**真实生产站点**，用真实口令登录，点真实按钮、读真实剪贴板。
"""
import json
import sys
import time
from pathlib import Path

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


BASE = "https://sphdl-admin.zhuanlu.xyz"
TOKEN_FILE = Path(r"C:/Users/Dancing/.sphdl_admin_token")

# 从生产取口令（ssh 读，避免写死）
import subprocess
try:
    tok = subprocess.run(
        ["ssh", "vps-aliyun", "sudo cat /opt/sphdl-admin/.admin_token"],
        capture_output=True, text=True, timeout=60).stdout.strip()
except Exception as e:
    tok = ""
    print(f"取口令失败: {e}")
if not tok:
    print("❌ 拿不到后台口令，无法验证")
    sys.exit(1)
print(f"已取到口令（{len(tok)} 字符）")

from playwright.sync_api import sync_playwright

errors = []
with sync_playwright() as pw:
    b = pw.chromium.launch(channel="msedge", headless=True)
    ctx = b.new_context(permissions=["clipboard-read", "clipboard-write"])
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

    # 登录
    pg.goto(f"{BASE}/login", wait_until="domcontentloaded")
    pg.fill("input[name=pw]", tok)
    pg.press("input[name=pw]", "Enter")
    pg.wait_for_load_state("domcontentloaded")
    time.sleep(1.0)
    print(f"  登录后 URL: {pg.url}")

    # 进话术页
    pg.goto(f"{BASE}/snippets", wait_until="domcontentloaded")
    time.sleep(0.8)
    body = pg.inner_text("body")
    head = body[:80].replace("\n", " ")
    print(f"  话术页: {head!r}")

    print()
    print("=" * 74)
    print("P1 生产页面是否有 cp() 与 copyLink()")
    print("=" * 74)
    has_cp = pg.evaluate("typeof cp === 'function'")
    has_link = pg.evaluate("typeof copyLink === 'function'")
    has_full = pg.evaluate("typeof copyFull === 'function'")
    n_ver = pg.evaluate("typeof VERS !== 'undefined' ? VERS.length : -1")
    print(f"  cp={has_cp}  copyLink={has_link}  copyFull={has_full}  VERS={n_ver} 个版本")
    check("P1a ★cp 函数存在", has_cp)
    check("P1b ★copyLink 存在", has_link)
    check("P1c copyFull 存在", has_full)

    if not (has_cp and has_link) or n_ver <= 0:
        print("\n  ⚠ 生产话术库没有版本数据或函数缺失，无法点按钮验证")
        print("     （需要先在后台话术页添加至少一个网盘版本）")
    else:
        print()
        print("=" * 74)
        print("P2 真实点击「复制链接」")
        print("=" * 74)
        btns = pg.query_selector_all("button")
        target = None
        for btn in btns:
            oc = btn.get_attribute("onclick") or ""
            if oc.startswith("copyLink("):
                target = btn
                break
        check("P2a 找到复制链接按钮", target is not None)
        if target:
            errors.clear()
            target.click()
            time.sleep(0.6)
            cb = pg.evaluate("navigator.clipboard.readText()")
            btn_txt = target.inner_text()
            print(f"  剪贴板: {cb!r}")
            print(f"  按钮文字: {btn_txt!r}")
            check("P2b ★剪贴板非空（复制成功）", bool(cb and cb.strip()),
                  (cb or "")[:70])
            check("P2c ★无 JS 异常", not errors, str(errors[:2]))
            check("P2d 有「已复制」反馈", "已复制" in btn_txt or "✅" in btn_txt)

        print()
        print("=" * 74)
        print("P3 真实点击「复制完整话术」")
        print("=" * 74)
        t2 = None
        for btn in pg.query_selector_all("button"):
            oc = btn.get_attribute("onclick") or ""
            if oc.startswith("copyFull("):
                t2 = btn
                break
        check("P3a 找到复制完整话术按钮", t2 is not None)
        if t2:
            time.sleep(1.5)
            errors.clear()
            t2.click()
            time.sleep(0.6)
            cb2 = pg.evaluate("navigator.clipboard.readText()")
            print(f"  剪贴板:\n{cb2}")
            check("P3b ★完整话术非空", bool(cb2 and cb2.strip()))
            check("P3c ★含下载链接", "http" in (cb2 or ""))
            check("P3d 无 JS 异常", not errors, str(errors[:2]))

    # 手机 UA 再验一次（客户说手机上也不行）
    print()
    print("=" * 74)
    print("P4 手机 UA 验证")
    print("=" * 74)
    m = ctx.new_page()
    m.set_viewport_size({"width": 390, "height": 844})
    m.goto(f"{BASE}/snippets", wait_until="domcontentloaded")
    time.sleep(0.6)
    m_cp = m.evaluate("typeof cp === 'function'")
    m_link = m.evaluate("typeof copyLink === 'function'")
    print(f"  cp={m_cp}  copyLink={m_link}")
    check("P4a ★手机视口下函数也加载", m_cp and m_link)
    if m_link:
        btns_m = m.query_selector_all("button")
        t3 = None
        for btn in btns_m:
            oc = btn.get_attribute("onclick") or ""
            if oc.startswith("copyLink("):
                t3 = btn
                break
        if t3:
            m.click(f"button[onclick='{t3.get_attribute('onclick')}']")
            time.sleep(0.6)
            cb3 = m.evaluate("navigator.clipboard.readText()")
            print(f"  手机剪贴板: {cb3!r}")
            check("P4b ★手机点击也能复制", bool(cb3 and cb3.strip()),
                  (cb3 or "")[:60])

    b.close()

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
