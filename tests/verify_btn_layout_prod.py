"""生产环境验证：话术页按钮改小 + 同行（v2.8.5）。"""
import subprocess
import sys
import time

BASE = "https://sphdl-admin.zhuanlu.xyz"

tok = subprocess.run(["ssh", "vps-aliyun", "sudo cat /opt/sphdl-admin/.admin_token"],
                     capture_output=True, text=True, timeout=60).stdout.strip()
print(f"口令 {len(tok)} 字符")

from playwright.sync_api import sync_playwright

errors = []
heights = []
ok_rows = False
cb = ""

with sync_playwright() as pw:
    b = pw.chromium.launch(channel="msedge", headless=True)
    ctx = b.new_context(viewport={"width": 1280, "height": 900},
                        permissions=["clipboard-read", "clipboard-write"])
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"{BASE}/login", wait_until="domcontentloaded")
    pg.fill("input[name=pw]", tok)
    pg.press("input[name=pw]", "Enter")
    pg.wait_for_load_state("domcontentloaded")
    time.sleep(1.0)
    pg.goto(f"{BASE}/snippets", wait_until="domcontentloaded")
    time.sleep(0.8)

    rects = pg.evaluate("""() => {
      const out = [];
      for (const btn of document.querySelectorAll('.ver-actions button')) {
        const r = btn.getBoundingClientRect();
        out.push({txt: btn.innerText.trim(), top: Math.round(r.top),
                  left: Math.round(r.left), w: Math.round(r.width),
                  h: Math.round(r.height)});
      }
      return out;
    }""")
    print("\n════ 生产环境：前 9 个按钮几何 ════")
    for r in rects[:9]:
        print(f"  {r['txt']:12s} top={r['top']:4d} left={r['left']:4d} "
              f"{r['w']}x{r['h']}")

    # 按 top 分组（同 top = 同一行）
    from collections import Counter
    tops = [r["top"] for r in rects]
    c = Counter(tops)
    # 每个卡片应有 3 个按钮同 top
    ok_rows = all(v >= 2 for v in c.values()) if c else False
    heights = [r["h"] for r in rects]
    print(f"\n  各行按钮数: {dict(c)}")
    print(f"  高度: {sorted(set(heights))}")

    btn = None
    for x in pg.query_selector_all("button"):
        if (x.get_attribute("onclick") or "").startswith("copyLink("):
            btn = x
            break
    if btn:
        btn.click()
        time.sleep(0.7)
        cb = pg.evaluate("navigator.clipboard.readText()")
    b.close()

print("\n════ 结论 ════")
print(f"  每行 ≥2 个按钮（同行）: {'✅' if ok_rows else '❌'}  {dict(c)}")
print(f"  按钮高度:               {sorted(set(heights))}  "
      f"{'✅ 已改小' if heights and max(heights) <= 34 else '❌'}")
print(f"  复制功能:               {'✅ ' + cb[:45] if cb else '❌ 空'}")
print(f"  JS 异常:                {len(errors)} 个")

sys.exit(0 if (ok_rows and heights and max(heights) <= 34 and cb) else 1)
