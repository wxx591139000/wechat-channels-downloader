#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sphdl 发码后台（手机可用的 Web 管理台）。

部署：VPS /opt/sphdl-admin/，端口 3011，域名 sphdl-admin.zhuanlu.xyz
依赖：仅 Python 标准库（不装任何第三方包，不动 VPS 的 pip 环境）

功能：
  ① 发码 —— 选产品/档位，填客户信息，可选填设备码（生成绑定版）
  ② 查客户 —— 按昵称/联系方式/授权码检索
  ③ 转卖追踪 —— 一码多机 / 一机多码

安全：
  · 全站需登录（口令存 .admin_token，Cookie 会话）
  · 只监听 127.0.0.1（对外由 Cloudflare 隧道转发）
  · 授权密钥从环境变量 SPHDL_LIC_SECRET 读取（不落磁盘明文）

数据：
  · licenses.jsonl  —— 发码台账（与本地 tools/ledger.py 同格式，可互相同步）
  · 上报数据在 sphdl-license 的库里（同机读取）
"""
import hashlib
import hmac
import html
import json
import os
import secrets
import sys
import sqlite3
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
DATA.mkdir(parents=True, exist_ok=True)
LEDGER = DATA / "licenses.jsonl"
TOKEN_FILE = BASE / ".admin_token"
LIC_DB = Path("/opt/sphdl-license/data/license.db")

PORT = int(os.environ.get("SPHDL_ADMIN_PORT", "3011"))
BIND = os.environ.get("SPHDL_ADMIN_BIND", "127.0.0.1")

# ---- 授权码算法（与 sphdl/license.py 保持一致）----
CODE_PREFIX = "SPHDL"
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
PAYLOAD_LEN_V1, MAC_LEN, V1, V2, V3 = 5, 10, 1, 2, 3
V2_PAYLOAD_LEN, V3_PAYLOAD_LEN = 6, 10
MSG_V1, MSG_V2, MSG_V3 = b"SPHDLv1", b"SPHDLv2", b"SPHDLv3"
PRODUCT_VIDEO, PRODUCT_MP = 1, 2
TIER_TO_CODE = {500: 1, 1000: 2, 2000: 3, 20000: 4}
CODE_TO_TIER = {v: k for k, v in TIER_TO_CODE.items()}
TIER_LABEL = {500: "轻量版（500 条）", 1000: "标准版（1000 条）",
              2000: "专业版（2000 条）", 20000: "旗舰版（2 万条）"}
PRODUCT_LABEL = {PRODUCT_VIDEO: "视频号下载", PRODUCT_MP: "公众号文章视频"}

SESSION = {}          # sid -> 过期时间戳
SESSION_TTL = 12 * 3600


def _secret() -> bytes:
    s = os.environ.get("SPHDL_LIC_SECRET", "").strip()
    return bytes.fromhex(s) if s else b""


def _mac(payload: bytes, ver: int) -> bytes:
    sec = _secret()
    if not sec:
        return b"\x00" * MAC_LEN
    pre = MSG_V3 if ver == V3 else (MSG_V2 if ver == V2 else MSG_V1)
    return hmac.new(sec, pre + payload, hashlib.sha256).digest()[:MAC_LEN]


def _b32enc(b: bytes) -> str:
    bits = "".join(f"{x:08b}" for x in b)
    while len(bits) % 5:
        bits += "0"
    return "".join(ALPHABET[int(bits[i:i + 5], 2)] for i in range(0, len(bits), 5))


def _group(code: str) -> str:
    body = code[len(CODE_PREFIX) + 1:] if code.startswith(CODE_PREFIX) else code
    return "-".join(body[i:i + 4] for i in range(0, len(body), 4))


def make_code(tier: int, product: int = PRODUCT_VIDEO, days: int = 0,
              device: str = "") -> str:
    """生成授权码。device 非空 → ver=3 绑定码。

    ⚠️ 关于 device 的格式（v2.0.19 抗硬件变动改造后的关键约束）：

      绑定校验要"4 项取 2"，就必须知道**客户机的 4 个硬件分量**。
      但手机后台只能拿到客户发来的**设备码展示串**（16 位十六进制），
      它已经是四项拼接后的哈希，**无法反推分量**。

      因此这里采用**对称降级**：把设备码串本身当作"唯一分量"绑进去。
      客户端校验时若只拿到设备码串（无分量）同样按这个方式算，
      两边一致 → 校验通过。代价是不能享受"换单项仍通过"的宽容度。

      ★ 想要真正的"4 项取 2"，需要让客户端把分量也告诉作者：
        客户端「授权管理」页可以显示一行「绑定码串」（四项首字节的十六进制，
        如 163145d5），作者填这个即可。见 _bind_token()。
    """
    if not _secret():
        raise RuntimeError("未配置授权密钥（SPHDL_LIC_SECRET）")
    tier_code = TIER_TO_CODE.get(tier, 0) if product == PRODUCT_VIDEO else 0
    days_step = min(255, max(0, int(days) // 8))
    rand = secrets.token_bytes(2)
    if device:
        dev4 = _bind4_from_token(device)
        payload = bytes([V3, tier_code, days_step, product]) + dev4 + rand
        blob = payload + _mac(payload, V3)
    else:
        payload = bytes([V2, tier_code, days_step, product]) + rand
        blob = payload + _mac(payload, V2)
    return f"{CODE_PREFIX}-{_group(f'{CODE_PREFIX}-' + _b32enc(blob))}"


def _bind4_from_token(token: str) -> bytes:
    """把作者拿到的设备信息转成 4 字节绑定值。

    支持两种输入（v2.0.19）：
      ① 8 位十六进制「绑定码串」如 163145d5 → **直接解析为 4 个分量首字节**
         （客户端「授权管理」页会显示这个，能享受"4 项取 2"宽容）
      ② 设备码展示串如 1594-8EBB-7A1F-B22F → 整串哈希取 4B（严格比对）
    """
    t = (token or "").strip().replace("-", "").replace(" ", "").lower()
    # ① 8 位纯十六进制 = 分量首字节串
    if len(t) == 8:
        try:
            return bytes.fromhex(t)
        except ValueError:
            pass
    # ② 其他（设备码展示串）→ 整串哈希取 4 字节
    return hashlib.sha256(t.upper().encode("utf-8")).digest()[:4]


def code_hash(code: str) -> str:
    return hashlib.sha256((code or "").strip().upper().encode()).hexdigest()[:16]


def dev_hash(device: str) -> str:
    return hashlib.sha256((device or "").strip().upper().encode()).hexdigest()[:16]


# ---------------------------------------------------------------- 台账
def ledger_add(rec: dict):
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def ledger_all() -> list:
    if not LEDGER.exists():
        return []
    out = []
    for ln in LEDGER.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln:
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
    return out


def vps_suspect() -> dict:
    """读上报库，算一码多机 / 一机多码。"""
    if not LIC_DB.exists():
        return {"error": "上报库不存在", "one_code_many": [], "one_dev_many": []}
    try:
        c = sqlite3.connect(f"file:{LIC_DB}?mode=ro", uri=True, timeout=5)
        rows = list(c.execute(
            "SELECT code_hash, device_hash, version, first_seen, last_seen,"
            " seen_count FROM reports"))
        c.close()
    except Exception as e:
        return {"error": f"读取失败: {e}", "one_code_many": [], "one_dev_many": []}

    by_code, by_dev = {}, {}
    for ch, dh, ver, fs, ls, cnt in rows:
        by_code.setdefault(ch, set()).add(dh)
        by_dev.setdefault(dh, set()).add(ch)
    one_code = [{"code_hash": k, "devices": len(v)}
                for k, v in by_code.items() if len(v) > 1]
    one_dev = [{"device_hash": k, "codes": len(v), "code_hashes": sorted(v)}
               for k, v in by_dev.items() if len(v) > 1]
    return {"total": len(rows), "distinct_codes": len(by_code),
            "distinct_devices": len(by_dev),
            "one_code_many": sorted(one_code, key=lambda x: -x["devices"]),
            "one_dev_many": sorted(one_dev, key=lambda x: -x["codes"]),
            "error": ""}


# ---------------------------------------------------------------- 页面
CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
     background:#f5f7fa;color:#1f2937;font-size:15px;line-height:1.6}
.wrap{max-width:760px;margin:0 auto;padding:12px}
header{background:#1e3a5f;color:#fff;padding:14px 16px;border-radius:0 0 12px 12px;
       margin:-12px -12px 14px;display:flex;align-items:center;justify-content:space-between}
header h1{font-size:17px;font-weight:600}
header a{color:#c9d8e8;font-size:13px;text-decoration:none;margin-left:12px}
nav{display:flex;gap:8px;margin-bottom:14px}
nav a{flex:1;text-align:center;padding:10px 4px;background:#fff;border-radius:10px;
      text-decoration:none;color:#374151;font-size:14px;border:1px solid #e5e7eb}
nav a.on{background:#1e3a5f;color:#fff;border-color:#1e3a5f;font-weight:600}
.card{background:#fff;border-radius:12px;padding:16px;margin-bottom:12px;
      box-shadow:0 1px 3px rgba(0,0,0,.06)}
label{display:block;font-size:13px;color:#6b7280;margin:10px 0 4px}
input,select{width:100%;padding:11px 12px;border:1px solid #d1d5db;border-radius:9px;
       font-size:15px;background:#fff;-webkit-appearance:none}
input:focus,select:focus{outline:none;border-color:#1e3a5f}
button{width:100%;padding:13px;border:none;border-radius:10px;background:#1e3a5f;
       color:#fff;font-size:16px;font-weight:600;margin-top:14px;cursor:pointer}
button:active{background:#152a46}
button.gray{background:#e5e7eb;color:#374151}
.code{font-family:Consolas,monospace;font-size:17px;font-weight:700;color:#0f766e;
      background:#f0fdfa;padding:14px;border-radius:10px;word-break:break-all;
      text-align:center;margin:12px 0;border:1px dashed #5eead4}
.ok{background:#f0fdf4;border-left:4px solid #22c55e;padding:10px 12px;
    border-radius:6px;color:#166534;font-size:14px}
.err{background:#fef2f2;border-left:4px solid #ef4444;padding:10px 12px;
     border-radius:6px;color:#991b1b;font-size:14px}
.warn{background:#fffbeb;border-left:4px solid #f59e0b;padding:10px 12px;
      border-radius:6px;color:#92400e;font-size:14px}
.item{border-bottom:1px solid #f3f4f6;padding:12px 0;font-size:14px}
.item:last-child{border-bottom:none}
.item b{color:#111827}
.meta{color:#6b7280;font-size:12.5px;margin-top:3px}
.mono{font-family:Consolas,monospace;font-size:12.5px;word-break:break-all}
.tag{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11.5px;
     background:#eef2ff;color:#3730a3;margin-right:6px}
.tag.red{background:#fee2e2;color:#991b1b}
.tag.green{background:#dcfce7;color:#166534}
h3{font-size:15px;margin:4px 0 10px;color:#111827}
"""


def page(title: str, body: str, nav: str = "", flash: str = "") -> str:
    return f"""<!DOCTYPE html><html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>{html.escape(title)}</title><style>{CSS}</style></head><body>
<div class="wrap">
<header><h1>🔑 sphdl 发码后台</h1><a href="/logout">退出</a></header>
{flash}
{nav}
{body}
</div></body></html>"""


NAV = """<nav>
<a href="/" class="{0}">发码</a>
<a href="/customers" class="{1}">查客户</a>
<a href="/suspect" class="{2}">转卖追踪</a>
</nav>"""


class H(BaseHTTPRequestHandler):
    server_version = "sphdl-admin/1.0"

    def log_message(self, fmt, *a):
        sys.stderr.write("[%s] %s\n" % (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"), fmt % a))

    # ---------- 工具 ----------
    def _send(self, code, text, ctype="text/html; charset=utf-8",
              headers=None):
        b = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def _redirect(self, to, cookie=None):
        self.send_response(303)
        self.send_header("Location", to)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def _sess_ok(self) -> bool:
        ck = self.headers.get("Cookie", "")
        for part in ck.split(";"):
            k, _, v = part.strip().partition("=")
            if k == "sid":
                exp = SESSION.get(v)
                if exp and exp > time.time():
                    return True
                SESSION.pop(v, None)
        return False

    def _need_login(self):
        self._send(200, page("登录", f"""
<div class="card"><h3>请输入管理口令</h3>
<form method="post" action="/login">
<label>口令</label><input type="password" name="pw" autofocus>
<button>登录</button></form>
<p class="meta" style="margin-top:10px">口令在服务器 /opt/sphdl-admin/.admin_token</p>
</div>"""))

    def _token(self) -> str:
        try:
            return TOKEN_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            return ""

    # ---------- 路由 ----------
    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        if p == "/health":
            return self._send(200, json.dumps({"ok": True}), "application/json")
        if p == "/login":
            return self._need_login()
        if p == "/logout":
            return self._redirect("/login", "sid=; Path=/; Max-Age=0")
        if not self._sess_ok():
            return self._need_login()
        q = parse_qs(u.query)
        if p == "/":
            return self._page_new(q)
        if p == "/customers":
            return self._page_customers(q)
        if p == "/suspect":
            return self._page_suspect()
        self._send(404, page("404", "<div class='card'>页面不存在</div>"))

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8", errors="replace")
        form = {k: (v[0] if v else "") for k, v in parse_qs(raw).items()}
        if u.path == "/login":
            pw = form.get("pw", "")
            if pw and hmac.compare_digest(pw, self._token()):
                sid = secrets.token_urlsafe(24)
                SESSION[sid] = time.time() + SESSION_TTL
                return self._redirect(
                    "/", f"sid={sid}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL}")
            return self._send(200, page("登录", "<div class='card'>"
                "<div class='err'>口令错误</div>"
                "<form method='post' action='/login'>"
                "<label>口令</label><input type='password' name='pw' autofocus>"
                "<button>登录</button></form></div>"))
        if not self._sess_ok():
            return self._need_login()
        if u.path == "/gen":
            return self._do_gen(form)
        self._send(404, "not found")

    # ---------- 发码页 ----------
    def _page_new(self, q):
        flash = ""
        code = q.get("code", [""])[0]
        if code:
            flash = (f"<div class='card'><div class='ok'>✅ 已生成并登记台账</div>"
                     f"<div class='code' id='c'>{html.escape(code)}</div>"
                     f"<button onclick=\"navigator.clipboard.writeText("
                     f"document.getElementById('c').innerText);this.innerText='已复制'\">"
                     f"复制授权码</button>"
                     f"<p class='meta'>把这个码发给客户，在软件「🔑 授权管理」粘贴激活</p>"
                     f"</div>")
        opts = "".join(f"<option value={t}>{l}</option>"
                       for t, l in TIER_LABEL.items())
        body = f"""
<div class="card"><h3>发码</h3>
<form method="post" action="/gen">
<label>产品</label>
<select name="product">
<option value="1">视频号下载（4 档）</option>
<option value="2">公众号文章视频（不分档）</option>
</select>
<label>档位（仅视频号有效）</label>
<select name="tier">{opts}</select>
<label>客户昵称 *</label><input name="customer" required
       placeholder="闲鱼买家昵称 / 微信备注名">
<label>联系方式</label><input name="contact" placeholder="闲鱼:xxx888">
<label>渠道</label>
<select name="channel">
<option>闲鱼</option><option>微信</option><option>朋友</option><option>其他</option>
</select>
<label>成交价</label><input name="price" inputmode="decimal" placeholder="199">
<label>设备码（选填 → 生成设备绑定版）</label>
<input name="device" placeholder="4770-0AEB-18B6-1AB4" autocapitalize="characters">
<button>生成授权码</button>
</form></div>"""
        return self._send(200, page("发码", body,
                                    NAV.format("on", "", ""), flash))

    def _do_gen(self, form):
        try:
            product = int(form.get("product") or PRODUCT_VIDEO)
            tier = int(form.get("tier") or 20000)
            if product == PRODUCT_MP:
                tier = 0
            code = make_code(tier, product=product,
                             device=form.get("device", "").strip())
        except Exception as e:                              # noqa: BLE001
            return self._send(200, page("发码", f"<div class='card'>"
                f"<div class='err'>生成失败：{html.escape(str(e))}</div>"
                f"<a href='/'><button class='gray'>返回</button></a></div>"))
        rec = {
            "code": code,
            "tier": tier,
            "tier_label": (TIER_LABEL.get(tier, "公众号视频版（不分档）")
                           if product == PRODUCT_VIDEO
                           else "公众号视频版（不分档）"),
            "product": product,
            "days": 0,
            "customer": form.get("customer", "").strip(),
            "contact": form.get("contact", "").strip(),
            "channel": form.get("channel", "").strip(),
            "price": form.get("price", "").strip(),
            "device": form.get("device", "").strip(),
            "note": "", "features": product,
            "source": "web-admin",
            "issued": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        ledger_add(rec)
        self._redirect("/?code=" + code.replace("-", "%2D"))

    # ---------- 查客户 ----------
    def _page_customers(self, q):
        kw = (q.get("q", [""])[0] or "").strip()
        rows = ledger_all()
        if kw:
            k = kw.lower()
            rows = [r for r in rows if k in json.dumps(r, ensure_ascii=False).lower()]
        rows = sorted(rows, key=lambda r: r.get("issued", ""), reverse=True)
        items = []
        for r in rows[:200]:
            tags = []
            if r.get("device"):
                tags.append("<span class='tag green'>绑定版</span>")
            if r.get("product") == PRODUCT_MP:
                tags.append("<span class='tag'>公众号</span>")
            items.append(f"""<div class="item">
<b>{html.escape(r.get('customer') or '（未填昵称）')}</b>
<span class="meta">　{r.get('tier_label','')}</span><br>
<span class="meta">{html.escape(r.get('contact',''))}　
{html.escape(r.get('channel',''))}　¥{html.escape(str(r.get('price','')))}　
{r.get('issued','')}</span><br>
<span class="mono">{html.escape(r.get('code',''))}</span>
{''.join(tags)}</div>""")
        body = f"""
<div class="card"><h3>查客户（共 {len(rows)} 条）</h3>
<form method="get" action="/customers">
<input name="q" value="{html.escape(kw)}" placeholder="昵称 / 联系方式 / 授权码 / 部分匹配">
<button>搜索</button></form></div>
<div class="card">{''.join(items) or '<p class="meta">没有记录</p>'}</div>"""
        return self._send(200, page("查客户", body, NAV.format("", "on", "")))

    # ---------- 转卖追踪 ----------
    def _page_suspect(self):
        s = vps_suspect()
        led = {code_hash(r.get("code", "")): r for r in ledger_all() if r.get("code")}
        if s.get("error"):
            # 上报库不存在/读不到时：仍然展示区块结构（显示"暂无数据"），
            # 不要把整页变成一条报错——否则用户会以为后台坏了。
            body = (f"<div class='card'><div class='warn'>⚠ 上报数据暂不可用："
                    f"{html.escape(s['error'])}</div>"
                    f"<p class='meta' style='margin-top:8px'>"
                    f"数据来自激活上报（/opt/sphdl-license/data/license.db）。"
                    f"若服务未启动或还没有任何客户端上报，这里就会是空的。</p></div>"
                    f"<div class='card'><h3>① 一码多机 —— 强转卖信号</h3>"
                    f"<p class='meta'>暂无数据</p></div>"
                    f"<div class='card'><h3>② 一机多码 —— 收集授权码</h3>"
                    f"<p class='meta'>暂无数据</p></div>")
            return self._send(200, page("转卖追踪", body, NAV.format("", "", "on")))

        def who(ch):
            r = led.get(ch)
            if not r:
                return "<span class='meta'>台账无此码</span>"
            return (f"<b>{html.escape(r.get('customer') or '未填')}</b>"
                    f"<span class='meta'>　{html.escape(r.get('tier_label',''))}　"
                    f"{html.escape(r.get('contact',''))}</span>")

        blocks = [f"""<div class="card"><h3>概览</h3>
<p>上报 {s['total']} 条　｜　不同授权码 {s['distinct_codes']} 个　｜　
不同设备 {s['distinct_devices']} 台</p></div>"""]

        ic = s["one_code_many"]
        if ic:
            its = "".join(f"<div class='item'><span class='tag red'>⚠ 一码 {x['devices']} 机</span>"
                          f"{who(x['code_hash'])}<br>"
                          f"<span class='mono meta'>{x['code_hash']}</span></div>"
                          for x in ic)
            blocks.append(f"<div class='card'><h3>① 一码多机 —— 强转卖信号</h3>{its}</div>")
        else:
            blocks.append("<div class='card'><h3>① 一码多机</h3>"
                          "<div class='ok'>✅ 没有发现</div></div>")

        idv = s["one_dev_many"]
        if idv:
            its = "".join(f"<div class='item'><span class='tag red'>⚠ 一机 {x['codes']} 码</span>"
                          f"<span class='mono meta'>{html.escape(x['device_hash'])}</span></div>"
                          for x in idv)
            blocks.append(f"<div class='card'><h3>② 一机多码 —— 收集授权码</h3>{its}</div>")
        else:
            blocks.append("<div class='card'><h3>② 一机多码</h3>"
                          "<div class='ok'>✅ 没有发现</div></div>")

        body = "".join(blocks)
        return self._send(200, page("转卖追踪", body, NAV.format("", "", "on")))


def main():
    if not TOKEN_FILE.exists():
        TOKEN_FILE.write_text(secrets.token_urlsafe(18), encoding="utf-8")
        os.chmod(TOKEN_FILE, 0o600)
        print(f"已生成管理口令 → {TOKEN_FILE}")
    if not _secret():
        print("⚠️ 未配置 SPHDL_LIC_SECRET，无法生成授权码（仍可查询）")
    srv = ThreadingHTTPServer((BIND, PORT), H)
    print(f"发码后台监听 http://{BIND}:{PORT}（Ctrl-C 退出）")
    print(f"台账: {LEDGER}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("bye")


if __name__ == "__main__":
    main()
