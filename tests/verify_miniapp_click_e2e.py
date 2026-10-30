"""小程序自动选集：WGC 端到端验证（v2.9.0）。

═══ 验证链路（全部真实）═══
1. 起真实 Edge 窗口，页面里放：两个标定块 + 一个"选集"按钮 + 点击计数器
2. WGC 截图 → 应拿到真实页面像素（PrintWindow/BitBlt 都失败过）
3. 两点标定 → 求 帧坐标→客户区坐标 的缩放+平移
4. 模板匹配找到按钮 → 换算成屏幕坐标 → 真实点击
5. **用 DOM 点击计数器验证真的点到了**

═══ 前三次截图方案实测结果（记录在此，避免重蹈）═══
PrintWindow(hwnd,dc,2) → 截不到页面内容
BitBlt(窗口DC)         → 全黑
BitBlt(桌面DC)         → 截到完全无关的窗口（Excel）
WGC                    → ✅ 成功（本测试验证）

═══ 判定标准 ═══
**DOM 计数器 +1**，不是"函数返回 True"。
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


from sphdl import autopage as AP
from sphdl import miniapp_click as MC

TITLE = "MCAUTO-E2E"

HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
html,body{margin:0;padding:0;background:#0b0b10;width:100%;height:100%}
#cal1{position:fixed;left:40px;top:40px;width:30px;height:30px;background:#ff2020}
#cal2{position:fixed;left:700px;top:500px;width:30px;height:30px;background:#2040ff}
#b{position:fixed;left:300px;top:200px;width:90px;height:34px;
   background:#20ff20;border:3px solid #ffffff}
#cnt{position:fixed;left:200px;top:20px;color:#ffdd00;font:22px sans-serif}
</style></head><body>
<div id="cal1"></div><div id="cal2"></div><div id="b"></div>
<div id="cnt">0</div>
<script>
window.__clicks = 0;
document.getElementById('b').addEventListener('click', function(){
  window.__clicks++;
  document.getElementById('cnt').textContent = window.__clicks;
});
</script></body></html>"""

from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    br = pw.chromium.launch(channel="msedge", headless=False,
                            args=["--window-position=150,120",
                                  "--window-size=1000,820"])
    ctx = br.new_context(viewport={"width": 980, "height": 740})
    pg = ctx.new_page()
    pg.set_content(HTML)
    pg.evaluate(f"document.title = {TITLE!r}")
    time.sleep(1.5)

    import win32gui

    def live_hwnd():
        c = []

        def _c(h, _):
            if not win32gui.IsWindowVisible(h):
                return True
            if TITLE not in (win32gui.GetWindowText(h) or ""):
                return True
            r = win32gui.GetWindowRect(h)
            if r[0] < -5000:
                return True
            c.append((h, (r[2] - r[0]) * (r[3] - r[1])))
            return True

        win32gui.EnumWindows(_c, None)
        c.sort(key=lambda x: -x[1])
        return c[0][0] if c else None

    hwnd = live_hwnd()
    check("E1 找到模拟窗口", hwnd is not None, str(hwnd))

    if hwnd:
        print()
        print("=" * 74)
        print("E2 WGC 截图")
        print("=" * 74)
        img = MC.grab_wgc(hwnd)
        check("E2a ★★WGC 能截到画面", img is not None,
              str(img.size) if img else "None")

        if img:
            c00 = win32gui.ClientToScreen(hwnd, (0, 0))
            wr = win32gui.GetWindowRect(hwnd)
            print(f"  窗口 {wr[2]-wr[0]}×{wr[3]-wr[1]}  "
                  f"客户区原点 {c00}  WGC帧 {img.size}")

            print()
            print("=" * 74)
            print("E3 两点标定")
            print("=" * 74)
            marks = []
            for el, color in (("cal1", (255, 32, 32)),
                              ("cal2", (32, 64, 255))):
                b = pg.evaluate(
                    "(() => {const r=document.getElementById('%s')"
                    ".getBoundingClientRect();"
                    "return {l:r.left,t:r.top,w:r.width,h:r.height};})()" % el)
                cx = b["l"] + b["w"] / 2
                cy = b["t"] + b["h"] / 2
                print(f"  {el} DOM中心({cx:.0f},{cy:.0f})")
                marks.append((cx, cy, color))

            calib = MC.calibrate(hwnd, img, marks)
            check("E3a ★两点标定成功", calib is not None, str(calib))
            if calib:
                print(f"  缩放 sx={calib['sx']:.4f} sy={calib['sy']:.4f}  "
                      f"平移 ox={calib['ox']:.1f} oy={calib['oy']:.1f}")

            if calib:
                print()
                print("=" * 74)
                print("E4 模板匹配按钮")
                print("=" * 74)
                # 先在帧里定位绿色按钮，裁模板
                g = MC.find_color_centroid(
                    img, (32, 255, 32), tol=25)
                check("E4a ★帧里看到绿色按钮", g is not None, str(g))
                if g:
                    # 用标定把帧坐标换成客户区，再算框
                    cc = MC.calib_to_client(g[0], g[1], calib)
                    print(f"  绿色中心 帧({g[0]:.0f},{g[1]:.0f}) → "
                          f"客户区({cc[0]:.0f},{cc[1]:.0f})")
                    bb = pg.evaluate(
                        "(() => {const r=document.getElementById('b')"
                        ".getBoundingClientRect();"
                        "return {l:r.left,t:r.top,w:r.width,h:r.height};})()")
                    exp = (bb["l"] + bb["w"] / 2, bb["t"] + bb["h"] / 2)
                    err = (abs(cc[0] - exp[0]), abs(cc[1] - exp[1]))
                    print(f"  DOM 真值({exp[0]:.0f},{exp[1]:.0f})  误差 {err}")
                    check("E4b ★★标定换算误差 ≤5px",
                          err[0] <= 5 and err[1] <= 5, str(err))

                    # 裁模板 + 匹配（模拟客户框选）
                    px = img.load()
                    xs, ys = [], []
                    for y in range(0, img.size[1], 2):
                        for x in range(0, img.size[0], 2):
                            r, gg, b2 = px[x, y][:3]
                            if abs(r - 32) < 25 and gg > 230 and abs(b2 - 32) < 25:
                                xs.append(x)
                                ys.append(y)
                    L, Rr, T, B = min(xs), max(xs), min(ys), max(ys)
                    tpl = img.crop((L + 3, T + 3, Rr - 2, B - 2))
                    got = MC.find_template(img, tpl)
                    check("E4c ★模板匹配成功", got is not None,
                          str(got and got[:2]))

                    print()
                    print("=" * 74)
                    print("E5 换算屏幕坐标 → 真实点击 → DOM 验证")
                    print("=" * 74)
                    if got:
                        hwnd2 = live_hwnd() or hwnd
                        cc2 = MC.calib_to_client(got[0], got[1], calib)
                        scr = MC.client_to_screen(hwnd2, cc2[0], cc2[1])
                        exp_scr = (c00[0] + exp[0], c00[1] + exp[1])
                        print(f"  帧({got[0]:.0f},{got[1]:.0f}) → "
                              f"客户区({cc2[0]:.0f},{cc2[1]:.0f}) → "
                              f"屏幕{scr}")
                        print(f"  真值屏幕({exp_scr[0]:.0f},{exp_scr[1]:.0f})")

                        AP.activate_window(hwnd2)
                        time.sleep(0.4)
                        before = pg.evaluate("window.__clicks")
                        ok = MC.click_at(scr[0], scr[1])
                        time.sleep(0.8)
                        after = pg.evaluate("window.__clicks")
                        print(f"  click_at={ok}  DOM 计数 {before} → {after}")
                        check("E5a click_at 成功", ok)
                        check("E5b ★★★真的点到了（DOM +1）",
                              after == before + 1, f"{before}→{after}")
                        if after == before + 1:
                            MC.click_at(scr[0], scr[1])
                            time.sleep(0.6)
                            a2 = pg.evaluate("window.__clicks")
                            check("E5c ★可重复点击", a2 == after + 1,
                                  f"{after}→{a2}")

    br.close()

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
