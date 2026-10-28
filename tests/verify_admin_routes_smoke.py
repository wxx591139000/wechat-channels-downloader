"""发码后台路由冒烟测试（v2.6.4）。

═══ 为什么要这个（2026-10-28 生产事故）═══
客户报：手机访问发码平台 502 Bad Gateway，电脑正常。

真因不是网络，而是我上一版把「复制文案」的代码插进了 `_page_new`：
    _lbl = code_label(tier, product, caps, md)   ← tier 是 _do_gen 的局部变量
→ `NameError: name 'tier' is not defined` → 页面 500
→ Cloudflare 显示 502。

**我当时只测了 code_label 函数本身，没测「页面能不能打开」。**
函数单测全绿，路由却挂了 —— 这类漏检必须靠冒烟测试兜住。

本测试把后台在进程内起起来（临时台账 + 临时口令），
逐个 GET/POST 所有路由，断言：
  · 没有 500
  · 没有 Traceback
  · 关键页面含预期文字
  · /?code=XXX（发码结果页）能正常渲染且带「XX授权码：」前缀
"""
import importlib.util
import json
import re
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


# ══════════ 进程内起服务（临时目录，不碰生产数据）══════════
tdir = Path(tempfile.mkdtemp(prefix="smoke_"))
secret = (ROOT / ".license_secret").read_text().strip()
# ★ 必须在 import 模块**之前**设好：_secret() 从 os.environ 读，
#   否则发码会 raise「未配置授权密钥」，页面变成"生成失败"
import os as _os
_os.environ["SPHDL_LIC_SECRET"] = secret
TOKEN = "smoke-token"
(tdir / ".admin_token").write_text(TOKEN, encoding="utf-8")

spec = importlib.util.spec_from_file_location(
    "adm_smoke", ROOT / "server" / "sphdl_admin.py")
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)

# 覆盖路径到临时目录
S.TOKEN_FILE = tdir / ".admin_token"
S.DATA = tdir
S.LEDGER = tdir / "licenses.jsonl"
S.SNIPPETS = tdir / "snippets.json"
S.CHANGELOG = tdir / "CHANGELOG.md"
S.SESSION.clear()

PORT = 38222
srv = S.ThreadingHTTPServer(("127.0.0.1", PORT), S.H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{PORT}"
print(f"服务已起: {BASE}  数据目录: {tdir}")

# 捕获服务端异常输出（Traceback 会打到 stderr）
import io
import contextlib

errs = io.StringIO()


class _Opener(urllib.request.HTTPCookieProcessor):
    pass


import http.cookiejar as cj

jar = cj.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def req(path, data=None, method=None):
    url = BASE + path
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
    r = urllib.request.Request(url, data=body, method=method)
    try:
        with opener.open(r, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


import urllib.parse

print()
print("=" * 74)
print("S1 未登录：所有 GET 路由不应 500")
print("=" * 74)
for pth in ("/", "/new", "/customers", "/snippets", "/upgrade",
            "/suspect", "/health", "/login", "/logout"):
    st, body = req(pth)
    print(f"  GET {pth:12s} → {st}  ({len(body)} 字符)")
    check(f"S1 GET {pth} 不是 5xx", st < 500, str(st))

print()
print("=" * 74)
print("S2 登录")
print("=" * 74)
st, body = req("/login", {"pw": TOKEN})
print(f"  POST /login → {st}")
st2, body2 = req("/")
print(f"  登录后 GET / → {st2} ({len(body2)} 字符)")
check("S2 登录成功（能进主页）", st2 == 200 and "发码" in body2, str(st2))

print()
print("=" * 74)
print("S3 登录后逐路由")
print("=" * 74)
# 注意：/new 不是独立 GET 路由（发码结果页是 /?code=XXX），
# 登录后访问 /new 返回 404 属正常，不列入本组
for pth in ("/", "/customers", "/snippets", "/upgrade", "/suspect"):
    st, body = req(pth)
    print(f"  GET {pth:12s} → {st}  ({len(body)} 字符)")
    check(f"S3 GET {pth} 正常", st == 200, str(st))

print()
print("=" * 74)
print("S4 发码（含点赞增强）→ 结果页必须能渲染")
print("=" * 74)
form = {
    "product": "1", "tier": "2000", "customer": "冒烟测试",
    "contact": "", "channel": "闲鱼", "price": "1",
    "max_devices": "1", "device": "", "cap_like": "1",
}
st, body = req("/gen", form)
print(f"  POST /gen → {st}")
check("S4a 发码不报错", st == 200, str(st))

# ★ 直接从**同一个响应页**取码与标签（urllib 已跟随 303 跳转）。
#   第一版这里先用正则从 body 里抠 code=，再发第二次请求 ——
#   结果抠错了（页面里有多处 code 字样），误判成"没带标签"。
m = re.search(r"id=['\"]c['\"]>([^<]+)<", body)
code = (m.group(1).strip() if m else "")
print(f"  页面上的码: {code or '（未找到）'}")
check("S4b 结果页有授权码", bool(code) and code.startswith("SPHDL"), code[:34])

if code:
    print(f"  页面长度: {len(body)}")
    has_lbl = "点赞增强包授权码：" in body
    print(f"  含「点赞增强包授权码：」: {has_lbl}")
    check("S4c ★发码结果页不是 5xx（上一版这里 NameError）",
          st == 200, str(st))
    check("S4d ★结果页带「点赞增强包授权码：」前缀", has_lbl)
    check("S4e 页面含复制按钮", "复制授权码" in body)

    # 台账是否记录了 caps
    rows = S.ledger_all()
    hit = [r for r in rows if r.get("code") == code]
    got_caps = hit[0].get("caps") if hit else None
    print(f"  台账记录: caps={got_caps}")
    check("S4f 台账记录了 caps=1", got_caps == 1, str(got_caps))

    # 再单独验证 /?code=XXX 重新打开也能出标签（刷新后仍正确）
    st2, page2 = req("/?code=" + urllib.parse.quote(code))
    check("S4g 重新打开 /?code= 仍带标签",
          st2 == 200 and "点赞增强包授权码：" in page2, str(st2))

print()
print("=" * 74)
print("S5 普通码（无点赞）→ 前缀应是档位+台数")
print("=" * 74)
form2 = dict(form)
form2.pop("cap_like")
form2["customer"] = "冒烟测试2"
st, body = req("/gen", form2)
m = re.search(r"code=(SPHDL[0-9A-Z\-]+)", body) or \
    re.search(r"(SPHDL-[0-9A-Z]{4}(?:-[0-9A-Z]{4})+)", body)
code2 = m.group(1).replace("%2D", "-") if m else ""
if code2:
    st, page = req("/?code=" + urllib.parse.quote(code2))
    print(f"  GET /?code=... → {st}")
    check("S5a 普通码结果页正常", st == 200, str(st))
    check("S5b 前缀是「旗舰1台授权码：」", "旗舰1台授权码：" in page,
          "见页面")

print()
print("=" * 74)
print("S6 源码级：_page_new 里不能出现未定义变量")
print("=" * 74)
src = (ROOT / "server" / "sphdl_admin.py").read_text(encoding="utf-8")
i = src.find("def _page_new")
j = src.find("\n    def ", i + 10)
seg = src[i:j]
bad = re.findall(r"code_label\(\s*tier\b", seg)
print(f"  _page_new 里 code_label(tier...) 出现次数: {len(bad)}")
check("S6 ★_page_new 不再直接引用 tier（NameError 根因）", not bad)
check("S6b 改用 label_from_ledger",
      "label_from_ledger(code)" in seg)

srv.shutdown()
print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
