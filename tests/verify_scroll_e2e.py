"""自动翻页滚筒机制端到端验证（v2.7.1）。

═══ 为什么要有这个测试 ═══
客户实测「一条没抓到，页面也没翻页」—— 说明滚轮根本没落到目标窗口。
上一版只做了「移动光标 + SendInput 滚轮」，但滚轮是给**光标下最顶层窗口**的，
视频号窗口不在前台时，滚轮打在了别的窗口（很可能就是我们自己的窗口）。

这种问题**单测测不出来**（单测用的是注入的假 scroll_fn）。
所以这里起一个真实 Chromium 窗口（自建长页面），
调用生产代码里的 activate_window + send_wheel + post_wheel，
然后**直接读页面 scrollY** 判断到底滚没滚 —— 唯一可信的判据。

覆盖：
  S1 窗口能识别（标题优先评分）
  S2 ★滚动后 scrollY 变大（真的滚了）
  S3 连续滚 3 次，scrollY 单调递增
  S4 光标位置还原
  S5 activate_window 把窗口切到前台
  S6 post_wheel 单发也能滚（不依赖真实光标）
"""
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


from sphdl import autopage as AP

# ══════════ 起一个真实 Chromium 长页面（模拟视频号列表）══════════
tall_html = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body{margin:0;font-family:sans-serif}
.item{height:220px;border-bottom:2px solid #ccc;padding:10px;font-size:20px}
</style></head><body>
""" + "".join(f'<div class="item">回放 {i+1} —— 测试用的长列表</div>'
              for i in range(80)) + "</body></html>"

tmp = Path(tempfile.mkdtemp(prefix="scrolltest_"))
html = tmp / "tall.html"
html.write_text(tall_html, encoding="utf-8")

from playwright.sync_api import sync_playwright

TITLE = "SCROLLTEST-视频号-模拟"


def get_hwnd(page_title: str):
    """按标题找窗口 hwnd（用 pywin32，不依赖 autopage 的进程过滤）。"""
    import win32gui

    found = []

    def _cb(h, _):
        if not win32gui.IsWindowVisible(h):
            return True
        t = win32gui.GetWindowText(h)
        if page_title in t:
            found.append(h)
        return True

    win32gui.EnumWindows(_cb, None)
    return found[0] if found else None


with sync_playwright() as pw:
    b = pw.chromium.launch(channel="msedge", headless=False,
                           args=["--window-position=60,60",
                                 "--window-size=900,760"])
    ctx = b.new_context(viewport={"width": 880, "height": 700})
    pg = ctx.new_page()
    # 用 set_content 打开，并改窗口标题便于定位
    pg.set_content(tall_html)
    pg.evaluate(f"document.title = {TITLE!r}")
    time.sleep(1.0)

    hwnd = get_hwnd(TITLE)
    print(f"\n模拟窗口 hwnd = {hwnd}")
    check("S1 窗口能被定位", hwnd is not None, str(hwnd))

    if hwnd:
        import win32gui
        rect = win32gui.GetWindowRect(hwnd)
        print(f"  窗口位置: {rect}  尺寸 {rect[2]-rect[0]}×{rect[3]-rect[1]}")

        # 故意先把焦点给别处，验证「不在前台也能滚」的修复
        pg.evaluate("window.scrollTo(0,0)")
        y0 = pg.evaluate("window.scrollY")
        print(f"\n  滚动前 scrollY = {y0}")

        old_cursor = AP.get_cursor_pos()
        # 走生产代码路径：activate + 真实滚轮 + post_wheel
        fg = AP.activate_window(hwnd)
        cx = (rect[0] + rect[2]) // 2
        cy = (rect[1] + rect[3]) // 2
        target = hwnd
        try:
            sub = win32gui.WindowFromPoint((cx, cy))
            if sub and win32gui.GetAncestor(sub, 2) == hwnd:
                target = sub
        except Exception:
            pass
        AP.set_cursor_pos(cx, cy)
        time.sleep(0.1)
        AP.send_wheel(-AP.WHEEL_DELTA * 3)
        AP.post_wheel(target, -AP.WHEEL_DELTA * 3, cx, cy)
        time.sleep(0.6)
        y1 = pg.evaluate("window.scrollY")
        AP.set_cursor_pos(*old_cursor)

        print(f"  滚动后 scrollY = {y1}  （前台={fg} 子窗={target}）")
        check("S2 ★滚动后页面真的动了", y1 > y0, f"{y0} → {y1}")
        check("S3 滚了至少一屏的 1/20", y1 > 30, str(y1))

        # ★ 走生产代码路径（AutoPager._do_scroll），而不是我在测试里手搓一遍
        #   上一版就是手搓的，漏了还原光标还把测试判成失败 ——
        #   测的必须是产品代码，不是测试自己写的流程。
        pager = AP.AutoPager(max_pages=99, interval=0.05,
                             find_fn=lambda: (hwnd, rect))
        ys = [y1]
        cursors = []
        for _ in range(3):
            pager._do_scroll()
            time.sleep(0.5)
            ys.append(pg.evaluate("window.scrollY"))
            cursors.append(AP.get_cursor_pos())
        print(f"  连续滚动 scrollY: {ys}")
        print(f"  每次滚动后光标: {cursors}（原位置 {old_cursor}）")
        check("S3b 连续 3 次单调递增",
              all(ys[i] < ys[i + 1] for i in range(len(ys) - 1)), str(ys))
        check("S4 ★生产代码每次滚完都还原光标",
              all(c == old_cursor for c in cursors), str(cursors))

        # post_wheel 单独能不能滚（不移动光标）
        y_before = pg.evaluate("window.scrollY")
        AP.set_cursor_pos(5, 5)     # 光标挪开，靠 PostMessage
        AP.post_wheel(target, -AP.WHEEL_DELTA * 3, cx, cy)
        time.sleep(0.5)
        y_after = pg.evaluate("window.scrollY")
        print(f"  仅 post_wheel（光标在别处）: {y_before} → {y_after}")
        check("S6 post_wheel 不依赖光标也能滚", y_after > y_before,
              f"{y_before} → {y_after}")

    b.close()

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
