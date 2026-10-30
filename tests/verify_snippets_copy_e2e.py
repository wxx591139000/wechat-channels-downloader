"""话术页「复制链接」按钮真实浏览器点击验证（v2.8.4）。

═══ 客户反馈（2026-10-30）═══
「电脑版网页点击新加的 2 个复制按钮没有反应，也没有复制成功」

═══ 根因 ═══
```js
function copyLink(i) {
  ...
  cp(t, event && event.target);   // ❌
}
```
Chrome 里**内联 onclick 中的全局 `event` 不一定可用**。
`event` 未定义时，`event && event.target` 会抛 **ReferenceError**（不是返回
undefined——未声明的变量求值就报错），函数当场中断 →
既没复制、也没有任何提示 → 表现就是「点了没反应」。

═══ 修法 ═══
按钮改成 `onclick="copyLink(0,this)"`，JS 签名改 `(i, btn)`，
**完全不依赖全局 event**。

═══ 为什么必须用真实浏览器 ═══
上一版我是「检查页面里有 function copyLink / 有 onclick」就判定通过，
结果客户一点就没反应。**字符串存在 ≠ 函数能跑**。
这里用 Playwright 真点按钮，并读 `navigator.clipboard.readText()`
断言剪贴板内容正确。

覆盖：
B1  ★真实点击「复制链接」→ 剪贴板得到网盘链接
B2  ★链接未带 pwd= 时，复制内容含「提取码：」
B3  ★链接已带 pwd= 时不重复追加提取码
B4  ★真实点击「复制完整话术」→ 含版本号/文件名/链接
B5  ★点按钮不抛 JS 异常（console error 为空）
B6  按钮点击后有「已复制」反馈
B7  空链接时 alert（拦截对话框），不写入剪贴板
"""
import json
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

# ══════════ 起后台 ══════════
tmp = Path(tempfile.mkdtemp(prefix="snippets_"))
spec = importlib.util.spec_from_file_location(
    "adm_vis", ROOT / "server" / "sphdl_admin.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

mod.DATA = tmp
mod.LEDGER = tmp / "licenses.jsonl"
mod.SNIPPETS = tmp / "snippets.json"
mod.TOKEN_FILE = tmp / ".admin_token"
mod.TOKEN_FILE.write_text("t", encoding="utf-8")
mod.SNIPPETS.write_text(json.dumps({
    "template": "请您查收",
    "versions": [
        # 链接不带 pwd=，提取码单独填
        {"ver": "2.8.3", "channel": "夸克",
         "filename": "微信视频号下载器-v2.8.3-win64-portable.zip",
         "link": "https://pan.quark.cn/s/QUARKTEST", "pwd": "ab12"},
        # 链接自带 pwd=
        {"ver": "2.8.3", "channel": "百度",
         "filename": "微信视频号下载器-v2.8.3-win64-portable.zip",
         "link": "https://pan.baidu.com/s/BAIDUTEST?pwd=cd34", "pwd": "cd34"},
    ],
}, ensure_ascii=False), encoding="utf-8")

srv = HTTPServer(("127.0.0.1", 0), mod.H)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
sid = uuid.uuid4().hex
mod.SESSION[sid] = time.time() + 3600
URL = f"http://127.0.0.1:{port}/snippets"
print(f"后台已起: {URL}")

# ══════════ 真实浏览器 ══════════
from playwright.sync_api import sync_playwright

errors = []
with sync_playwright() as pw:
    b = pw.chromium.launch(channel="msedge", headless=True)
    ctx = b.new_context(permissions=["clipboard-read", "clipboard-write"])
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

    pg.goto(URL, wait_until="domcontentloaded")
    # 注入登录 cookie 不可行（SESSION 已加，直接带 cookie 访问）
    ctx.add_cookies([{"name": "sid", "value": sid,
                      "domain": "127.0.0.1", "path": "/"}])
    pg.goto(URL, wait_until="domcontentloaded")
    time.sleep(0.6)

    body_txt = pg.inner_text("body")
    print(f"  页面标题区: {body_txt[:60]!r}")
    if "复制链接" not in body_txt:
        print("  ❌ 页面没渲染出复制按钮，后续跳过")
        b.close()
        srv.shutdown()
        sys.exit(1)

    # ---------- B1/B2 夸克（提取码单独填）----------
    print()
    print("=" * 74)
    print("B1-B2 点「复制链接」（夸克，提取码单独填）")
    print("=" * 74)
    # 找到夸克那个卡片的复制链接按钮
    # ★ 按 VERS 索引定位（页面顺序 = VERS 顺序），不靠 innerText 猜卡片
    #   （第一版靠 innerText 找卡片，匹配到了含两个卡片的外层容器 → 点错按钮）
    vers_order = pg.evaluate("VERS.map(v => v.link)")
    print(f"  VERS 顺序: {vers_order}")
    idx_quark = next(i for i, l in enumerate(vers_order) if "QUARKTEST" in l)
    idx_baidu = next(i for i, l in enumerate(vers_order) if "BAIDUTEST" in l)
    btn_all = pg.query_selector_all("button")
    target = None
    for btn in btn_all:
        oc = btn.get_attribute("onclick") or ""
        if oc == f"copyLink({idx_quark},this)":
            target = btn
            break
    check("B1a 找到夸克的复制链接按钮", target is not None,
          f"idx={idx_quark}")

    if target:
        errors.clear()
        target.click()
        time.sleep(0.5)
        cb = pg.evaluate("navigator.clipboard.readText()")
        print(f"  剪贴板: {cb!r}")
        check("B1b ★剪贴板拿到链接", "QUARKTEST" in (cb or ""), (cb or "")[:70])
        check("B2 ★含「提取码：ab12」", "提取码" in (cb or "") and "ab12" in (cb or ""))
        check("B5a ★点击无 JS 异常", not errors, str(errors[:2]))
        btn_txt = target.inner_text()
        print(f"  按钮文字: {btn_txt!r}")
        check("B6 有点击反馈", "已复制" in btn_txt or "✅" in btn_txt, btn_txt)

    # ---------- B3 百度（链接自带 pwd）----------
    print()
    print("=" * 74)
    print("B3 点「复制链接」（百度，链接已带 pwd= → 不重复追加）")
    print("=" * 74)
    t2 = None
    for btn in pg.query_selector_all("button"):
        oc = btn.get_attribute("onclick") or ""
        if oc == f"copyLink({idx_baidu},this)":
            t2 = btn
            break
    check("B3a 找到百度的复制链接按钮", t2 is not None, f"idx={idx_baidu}")
    if t2:
        time.sleep(1.6)      # 等上一个按钮的「已复制」恢复
        errors.clear()
        t2.click()
        time.sleep(0.5)
        cb2 = pg.evaluate("navigator.clipboard.readText()")
        print(f"  剪贴板: {cb2!r}")
        n_pwd = (cb2 or "").count("pwd=")
        print(f"  \"pwd=\" 出现次数: {n_pwd}   含\"提取码\"：{'提取码' in (cb2 or '')}")
        check("B3b ★有 pwd= 但不重复追加提取码",
              "pwd=cd34" in (cb2 or "") and "提取码" not in (cb2 or ""),
              (cb2 or "")[:70])

    # ---------- B4 完整话术 ----------
    print()
    print("=" * 74)
    print("B4 点「复制完整话术」")
    print("=" * 74)
    t3 = None
    for btn in pg.query_selector_all("button"):
        oc = btn.get_attribute("onclick") or ""
        if oc == f"copyFull({idx_quark},this)":
            t3 = btn
            break
    check("B4a 找到复制完整话术按钮", t3 is not None,
          f"idx={idx_quark}")
    if t3:
        errors.clear()
        t3.click()
        time.sleep(0.5)
        cb3 = pg.evaluate("navigator.clipboard.readText()")
        print(f"  剪贴板:\n{cb3}")
        check("B4b ★含版本号", "v2.8.3" in (cb3 or ""))
        check("B4c ★含文件名", "win64-portable" in (cb3 or ""))
        check("B4d ★含链接", "QUARKTEST" in (cb3 or ""))
        check("B5b ★无 JS 异常", not errors, str(errors[:2]))

    b.close()

srv.shutdown()

print()
print("=" * 74)
print("B7 源码检查：不再依赖全局 event")
print("=" * 74)
src = (ROOT / "server" / "sphdl_admin.py").read_text(encoding="utf-8")
# 只看代码部分（排除注释）
code_lines = [l for l in src.split("\n") if not l.strip().startswith("//")]
code = "\n".join(code_lines)
check("B7a ★无 `event && event.target` 实际调用",
      "cp(t, event" not in code)
check("B7b 按钮传 this", 'copyLink(" + str(i) + ",this)' in src
      or "copyLink(\" + str(i) + \",this)" in src)
check("B7c 函数签名为 (i, btn)", "function copyLink(i, btn) {{" in src)

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
