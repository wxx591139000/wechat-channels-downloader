"""话术页「一键复制网盘链接」验证（v2.8.3）。

═══ 客户需求（2026-10-30）═══
「有些已购用户希望给他发网盘链接，但并不涉及发码，
 希望有个独立于『发码』的单独路径可以让我直接选择不同的网盘链接并一键复制。」

═══ 现状调查 ═══
后台**已有** `/snippets`（话术库）能保存「版本 + 多个网盘链接 + 提取码」，
但：
  · 复制按钮只在**发码结果页**（`复制话术+授权码`），必须先生成一个码
  · 话术页本身**没有复制按钮** → 已购用户补发链接时无路可走

═══ 本版改动 ═══
话术页每个版本卡片新增两个按钮（不涉及发码）：
  · 📋 复制链接      = 只有网盘链接（+提取码，若链接没带 pwd=）
  · 📄 复制完整话术  = 版本号 + 渠道 + 文件名 + 下载链接

覆盖：
S1  ★话术页有 copyLink / copyFull 按钮
S2  ★页面注入了 VERS 数据（JSON，含 link/pwd/filename）
S3  复制链接含提取码（当 pwd 非空且链接不带 pwd=）
S4  链接已带 pwd= 时不重复追加提取码
S5  完整话术含版本号/渠道/文件名/链接
S6  空链接时 alert 提示，不复制空串
S7  ★路由冒烟：/snippets 200（不是 5xx）
S8  原有「删除」按钮仍在（没破坏）
S9  发码页的「复制话术+授权码」仍在（原路径没坏）
S10 复用已有 cp() 函数（带降级兜底）
"""
import re
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


src = (ROOT / "server" / "sphdl_admin.py").read_text(encoding="utf-8")

print("=" * 74)
print("S1-S6 源码检查")
print("=" * 74)
i = src.find("def _page_snippets")
j = src.find("\n    def ", i + 20)
page_snip = src[i:j] if j > i else src[i:i + 12000]
print(f"  话术页函数 {len(page_snip)} 字符")

check("S1a 有「复制链接」按钮", "copyLink(" in page_snip)
check("S1b 有「复制完整话术」按钮", "copyFull(" in page_snip)
check("S2 ★注入了 VERS 数据", "var VERS = " in page_snip and "_vers_js" in page_snip)
check("S2b 数据含 link/pwd/filename",
      '"link"' in src and '"pwd"' in src and '"filename"' in src)
check("S3 复制链接含提取码逻辑", "提取码：" in page_snip)
check("S4 ★链接带 pwd= 时不重复追加", 'indexOf("pwd=") < 0' in page_snip)
check("S5 完整话术含版本号与渠道",
      '"微信视频号下载器 v"' in page_snip and "网盘" in page_snip)
check("S5b 完整话术含文件名", '"文件："' in page_snip)
check("S6 空链接 alert 提示", "还没填网盘链接" in page_snip)
check("S10 复用 cp() 函数", "cp(t," in page_snip)

print()
print("=" * 74)
print("S8-S9 不破坏原有功能")
print("=" * 74)
check("S8 删除按钮仍在", "del_js" in page_snip and "删除</button>" in page_snip)
check("S9 发码页「复制话术+授权码」仍在",
      "复制话术 + 授权码" in src)
check("S9b 话术库保存路由仍在", '"/snippets_save"' in src)

print()
print("=" * 74)
print("S7 路由冒烟（起真实服务）")
print("=" * 74)
import importlib.util
import json
import threading
import time
import urllib.request
from http.server import HTTPServer

tmp = Path(tempfile.mkdtemp(prefix="admin_"))
spec = importlib.util.spec_from_file_location(
    "sphdl_admin_test", ROOT / "server" / "sphdl_admin.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# 指到临时目录并造数据
mod.DATA = tmp
mod.LEDGER = tmp / "licenses.jsonl"
mod.SNIPPETS = tmp / "snippets.json"
mod.TOKEN_FILE = tmp / ".admin_token"
mod.TOKEN_FILE.write_text("testtoken", encoding="utf-8")
mod.SNIPPETS.write_text(json.dumps({
    "template": "请您查收",
    "versions": [
        {"ver": "2.8.2", "channel": "夸克",
         "filename": "微信视频号下载器-v2.8.2-win64-portable.zip",
         "link": "https://pan.quark.cn/s/abc123", "pwd": "x1y2"},
        {"ver": "2.8.2", "channel": "百度",
         "filename": "微信视频号下载器-v2.8.2-win64-portable.zip",
         "link": "https://pan.baidu.com/s/xyz?pwd=auto", "pwd": "auto"},
    ],
}, ensure_ascii=False), encoding="utf-8")

srv = HTTPServer(("127.0.0.1", 0), mod.H)
port = srv.server_address[1]
th = threading.Thread(target=srv.serve_forever, daemon=True)
th.start()


# ★ 会话 Cookie 名是 sid、值来自 SESSION 字典（伪造 cookie 无效）。
#   这里直接把 sid 塞进 SESSION，模拟已登录。
import uuid
sid = uuid.uuid4().hex
mod.SESSION[sid] = time.time() + 3600


def req(path, headers=None):
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                               headers=headers or {})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


import time
try:
    st, body = req("/snippets", {"Cookie": f"sid={sid}"})
    print(f"  GET /snippets → HTTP {st}  {len(body)} 字节")
    check("S7a ★/snippets 返回 200（不是 5xx）", st == 200, str(st))
    check("S7b 页面含两个复制按钮",
          "复制链接" in body and "复制完整话术" in body)
    check("S7c 页面含 VERS 数据",
          "var VERS =" in body and "pan.quark.cn" in body)
    check("S7d 页面含两个网盘版本",
          "夸克" in body and "百度" in body)
    # JSON 里 pwd 是否正确注入
    m = re.search(r"var VERS = (\[.*?\]);", body, re.S)
    ok_json = False
    if m:
        try:
            vs = json.loads(m.group(1))
            # 按 channel 找，不依赖排序（页面按 版本倒序+渠道名 排，百度在夸克前）
            by_ch = {v.get("channel"): v for v in vs}
            ok_json = (len(vs) == 2
                       and by_ch.get("夸克", {}).get("pwd") == "x1y2"
                       and "pwd=auto" in by_ch.get("百度", {}).get("link", "")
                       and "v2.8.2-win64" in by_ch.get("夸克", {}).get("filename", ""))
        except Exception as e:
            print(f"    JSON 解析失败: {e}")
    check("S7e ★VERS 是合法 JSON 且字段正确", ok_json,
          m.group(1)[:80] if m else "未匹配到")
finally:
    srv.shutdown()

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
