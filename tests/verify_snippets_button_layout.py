"""话术页复制按钮：改小 + 同一行（v2.8.5）。

═══ 客户需求（2026-10-30）═══
「把这 2 个按钮改得小一些，并放在同一行。」

═══ 为什么按钮会被挤到下一行 ═══
`.btn2` 的 CSS 是 `width:100%`（原本用于「复制话术+授权码」那种整行大按钮）。
我第一版只加了内联 `style='padding:6px 12px'`，**width:100% 没被覆盖**
→ 每个按钮都撑满一行 → 视觉上就是"各占一行"。

修法：新增 `.mini` 类，`width:auto!important` + 小内边距 +
`white-space:nowrap`，三个按钮（复制链接/完整话术/删除）挤在同一行。

═══ 验证方式（不靠肉眼）═══
用 Playwright 读 `getBoundingClientRect()`，比较三个按钮的 **top 值**：
  · top 相同（±2px）= 同一行
  · 宽高比之前小  = 确实变小了

覆盖：
L1  ★三个按钮 top 相同 → 同一行
L2  ★按钮高度变小（≤ 34px，原来约 44px）
L3  ★按钮宽度不再 100%（< 卡片宽度 60%）
L4  三个按钮水平不重叠（left 递增）
L5  ★改小后复制功能仍正常（点完剪贴板有内容）
L6  无 JS 异常
L7  手机视口也同行
"""
import json
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


import importlib.util
from http.server import HTTPServer

tmp = Path(tempfile.mkdtemp(prefix="btn_"))
spec = importlib.util.spec_from_file_location("adm", ROOT / "server" / "sphdl_admin.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

mod.DATA = tmp
mod.LEDGER = tmp / "l.jsonl"
mod.SNIPPETS = tmp / "s.json"
mod.TOKEN_FILE = tmp / "tok"
mod.TOKEN_FILE.write_text("t", encoding="utf-8")
mod.SNIPPETS.write_text(json.dumps({
    "template": "请您查收",
    "versions": [
        {"ver": "2.8.4", "channel": "百度",
         "filename": "微信视频号下载器-v2.8.4-win64-portable.zip",
         "link": "https://pan.baidu.com/s/BT?pwd=aa11", "pwd": "aa11"},
    ],
}, ensure_ascii=False), encoding="utf-8")

srv = HTTPServer(("127.0.0.1", 0), mod.H)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
sid = uuid.uuid4().hex
mod.SESSION[sid] = time.time() + 3600
URL = f"http://127.0.0.1:{port}/snippets"
print(f"后台已起: {URL}")

from playwright.sync_api import sync_playwright

errors = []
with sync_playwright() as pw:
    b = pw.chromium.launch(channel="msedge", headless=True)
    ctx = b.new_context(permissions=["clipboard-read", "clipboard-write"],
                        viewport={"width": 1280, "height": 900})
    ctx.add_cookies([{"name": "sid", "value": sid,
                      "domain": "127.0.0.1", "path": "/"}])
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.goto(URL, wait_until="domcontentloaded")
    time.sleep(0.6)

    print()
    print("=" * 74)
    print("L1-L4 桌面视口：三个按钮的几何位置")
    print("=" * 74)
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
    for r in rects:
        print(f"  {r['txt']:12s} top={r['top']:4d} left={r['left']:4d} "
              f"{r['w']}x{r['h']}")
    check("L1a 找到三个按钮", len(rects) == 3, str(len(rects)))
    if len(rects) == 3:
        tops = [r["top"] for r in rects]
        print(f"  三个 top: {tops}  极差 {max(tops) - min(tops)}px")
        check("L1b ★三个按钮在同一行（top 极差 ≤ 2px）",
              max(tops) - min(tops) <= 2, str(max(tops) - min(tops)))
        hs = [r["h"] for r in rects]
        print(f"  高度: {hs}")
        check("L2 ★按钮变小了（高度 ≤ 34px；原来 btn2 约 44px）",
              all(h <= 34 for h in hs), str(hs))
        ws = [r["w"] for r in rects]
        card_w = pg.evaluate(
            "document.querySelector('.ver-actions').getBoundingClientRect().width")
        print(f"  宽度: {ws}  容器宽 {card_w:.0f}")
        check("L3 ★不再 width:100%（每个都远小于容器）",
              all(w < card_w * 0.6 for w in ws),
              f"max={max(ws)} vs {card_w*0.6:.0f}")
        lefts = [r["left"] for r in rects]
        check("L4 水平依次排列不重叠",
              all(lefts[i] < lefts[i + 1] for i in range(len(lefts) - 1)),
              str(lefts))

    print()
    print("=" * 74)
    print("L5-L6 改小后功能仍正常")
    print("=" * 74)
    btn = None
    for x in pg.query_selector_all("button"):
        if (x.get_attribute("onclick") or "").startswith("copyLink("):
            btn = x
            break
    check("L5a 找到复制链接按钮", btn is not None)
    if btn:
        errors.clear()
        btn.click()
        time.sleep(0.6)
        cb = pg.evaluate("navigator.clipboard.readText()")
        print(f"  剪贴板: {cb!r}")
        print(f"  按钮文字: {btn.inner_text()!r}")
        check("L5b ★复制功能仍正常", bool(cb and "BT" in cb), (cb or "")[:60])
        check("L6 无 JS 异常", not errors, str(errors[:2]))

    print()
    print("=" * 74)
    print("L7 手机视口")
    print("=" * 74)
    pg.set_viewport_size({"width": 390, "height": 844})
    time.sleep(0.4)
    rects_m = pg.evaluate("""() => {
      const out = [];
      for (const btn of document.querySelectorAll('.ver-actions button')) {
        const r = btn.getBoundingClientRect();
        out.push({txt: btn.innerText.trim(), top: Math.round(r.top),
                  h: Math.round(r.height)});
      }
      return out;
    }""")
    for r in rects_m:
        print(f"  {r['txt']:12s} top={r['top']:4d} h={r['h']}")
    tops_m = [r["top"] for r in rects_m]
    if tops_m:
        # 手机窄，允许换行（wrap），但至少前两个（复制类）应同行
        first_two = tops_m[:2] if len(tops_m) >= 2 else tops_m
        print(f"  前两个 top: {first_two}")
        check("L7 ★手机视口下前两个复制按钮同行",
              len(first_two) == 2 and abs(first_two[0] - first_two[1]) <= 2,
              str(first_two))

    b.close()

srv.shutdown()

print()
print("=" * 74)
print("源码检查")
print("=" * 74)
src = (ROOT / "server" / "sphdl_admin.py").read_text(encoding="utf-8")
check("S1 有 .mini 样式", ".mini{width:auto!important" in src.replace(" ", ""))
# 实际写法：class='btn2 mini' / class='btn2 gray mini' / class='del mini'
n_mini = (src.count("class='btn2 mini'")
          + src.count("class='btn2 gray mini'")
          + src.count("class='del mini'"))
print(f"  带 mini 类的按钮: {n_mini} 个")
check("S2 ★三个按钮都带 mini 类", n_mini == 3, str(n_mini))
check("S3 用 .ver-actions 容器", "class='ver-actions'" in src)

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
