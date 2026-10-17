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
PAYLOAD_LEN_V1, MAC_LEN, V1, V2, V3, V4 = 5, 10, 1, 2, 3, 4
V2_PAYLOAD_LEN, V3_PAYLOAD_LEN, V4_PAYLOAD_LEN = 6, 10, 7
MSG_V1, MSG_V2, MSG_V3, MSG_V4 = b"SPHDLv1", b"SPHDLv2", b"SPHDLv3", b"SPHDLv4"
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
    pre = (MSG_V4 if ver == V4 else MSG_V3 if ver == V3
           else MSG_V2 if ver == V2 else MSG_V1)
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
              device: str = "", max_devices=None) -> str:
    """生成授权码。

    device 非空 → ver=3 绑定码（死绑单机）
    max_devices 非 None → ver=4 限台数码（靠服务器统计执行上限）
    都不给 → ver=2 普通码（不限台数）

    ⚠️ 关于 device 的格式（v2.0.19 抗硬件变动改造后的关键约束）：

      绑定校验要"4 项取 2"，就必须知道**客户机的 4 个硬件分量**。
      但手机后台只能拿到客户发来的**设备码展示串**（16 位十六进制），
      它已经是四项拼接后的哈希，**无法反推分量**。

      因此这里采用**对称降级**：把设备码串本身当作"唯一分量"绑进去。
      客户端校验时若只拿到设备码串（无分量）同样按这个方式算，
      两边一致 → 校验通过。代价是不能享受"换单项仍通过"的宽容度。

      ★ 想要真正的"4 项取 2"，需要让客户端把分量也告诉作者：
        客户端「授权管理」页可以显示一行「绑定串」（四项首字节的十六进制，
        如 163145d5），作者填这个即可。见 _bind_token()。
    """
    if not _secret():
        raise RuntimeError("未配置授权密钥（SPHDL_LIC_SECRET）")
    tier_code = TIER_TO_CODE.get(tier, 0) if product == PRODUCT_VIDEO else 0
    days_step = min(255, max(0, int(days) // 8))
    rand = secrets.token_bytes(2)
    if device:
        dev4 = _bind4_from_token(device)
        # 绑定码保持 ver=3 不变（载荷结构与台数码不同，不能混）
        payload = bytes([V3, tier_code, days_step, product]) + dev4 + rand
        blob = payload + _mac(payload, V3)
    elif max_devices is not None:
        md = min(255, max(0, int(max_devices)))
        payload = bytes([V4, tier_code, days_step, product, md]) + rand
        blob = payload + _mac(payload, V4)
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
        # ★ v2.1.4：改档位（升级/降级/续期）
        if p == "/upgrade":
            return self._page_upgrade(q)
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
        # ★ v2.1.4：改档位提交
        if u.path == "/upgrade":
            return self._do_upgrade(form)
        # ★ v2.1.5：改名 / 作废 / 恢复
        if u.path == "/edit_customer":
            return self._do_edit_customer(form)
        if u.path == "/void":
            return self._do_void(form, voided=True)
        if u.path == "/restore":
            return self._do_void(form, voided=False)
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
<label>允许激活的电脑台数（★ 默认 1 台，防转卖）</label>
<select name="max_devices">
<option value="1" selected>1 台（默认·限单机）</option>
<option value="">不限台数</option>
<option value="2">2 台</option>
<option value="3">3 台</option>
<option value="5">5 台</option>
<option value="10">10 台</option>
</select>
<p class="meta" style="margin-top:4px">
默认限 1 台：客户换电脑会激活失败，需要你手动放宽。<br>
想卖「家里几台都能用」就选 2~3 台；老客户可改用「查客户 → 改档位」放宽。</p>
<label>设备码 / 绑定串（选填 → 死绑单机，与台数二选一）</label>
<input name="device" placeholder="163145d5 或 1594-8EBB-7A1F-B22F" autocapitalize="characters">
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
            dev = form.get("device", "").strip()
            md_raw = (form.get("max_devices") or "").strip()
            # 台数与死绑二选一：填了设备码就死绑（优先），否则看台数
            md = None
            if not dev and md_raw:
                md = int(md_raw)
            code = make_code(tier, product=product, device=dev,
                             max_devices=md)
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
            "max_devices": md,
            "note": "", "features": product,
            "source": "web-admin",
            "issued": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        ledger_add(rec)
        self._redirect("/?code=" + code.replace("-", "%2D"))

    # ---------- 查客户 ----------
    def _page_customers(self, q):
        kw = (q.get("q", [""])[0] or "").strip()
        # ★ v2.1.5：先归一化台账 —— 把编辑事件合进原记录、算出作废状态，
        #   否则改了名之后列表还显示旧名、作废了也看不出来。
        rows = self._merged_rows()
        if kw:
            # ★ v2.1.5：只搜「合并后的当前值」，不搜历史事件里的旧值。
            #   否则客户改名后用旧名还能搜到（_merged_rows 已把编辑合并进
            #   行本身，事件行不在此列表里，所以直接搜当前字段即可）。
            k = kw.lower()
            rows = [r for r in rows
                    if k in json.dumps(
                        {kk: vv for kk, vv in r.items() if not kk.startswith("_")},
                        ensure_ascii=False).lower()]
        rows = sorted(rows, key=lambda r: r.get("issued", ""), reverse=True)
        items = []
        for r in rows[:200]:
            tags = []
            if r.get("_voided"):
                tags.append("<span class='tag red'>🚫 已作废</span>")
            if r.get("device"):
                tags.append("<span class='tag green'>绑定版</span>")
            if r.get("max_devices"):
                tags.append(f"<span class='tag'>{r['max_devices']} 台</span>")
            if r.get("product") == PRODUCT_MP:
                tags.append("<span class='tag'>公众号</span>")
            if r.get("renewed_from"):
                tags.append("<span class='tag green'>已升级</span>")
            ec = html.escape(r.get("code", ""))
            items.append(f"""<div class="item">
<b>{html.escape(r.get('customer') or '（未填昵称）')}</b>
<span class="meta">　{r.get('tier_label','')}</span><br>
<span class="meta">{html.escape(r.get('contact',''))}　
{html.escape(r.get('channel',''))}　¥{html.escape(str(r.get('price','')))}　
{r.get('issued','')}</span><br>
<span class="mono">{ec}</span>
{''.join(tags)}
<div style="margin-top:8px">
<a href="/upgrade?code={ec}" style="display:inline-block;padding:6px 14px;
   background:#1e3a5f;color:#fff;border-radius:8px;text-decoration:none;
   font-size:13px">⚙ 管理（改档/改名/作废）</a>
</div></div>""")
        body = f"""
<div class="card"><h3>查客户（共 {len(rows)} 条）</h3>
<form method="get" action="/customers">
<input name="q" value="{html.escape(kw)}" placeholder="昵称 / 联系方式 / 授权码 / 部分匹配">
<button>搜索</button></form>
<p class="meta" style="margin-top:8px">
点「⚙ 管理」可以：改档位、改客户名、作废/恢复授权码。</p></div>
<div class="card">{''.join(items) or '<p class="meta">没有记录</p>'}</div>"""
        return self._send(200, page("查客户", body, NAV.format("", "on", "")))

    def _merged_rows(self):
        """把台账归一化成一"行"一客户（v2.1.5）。

        台账里有四类记录：
          · 原始发码记录（有 code、无 event）
          · renewed_from（升级产生的新码，也算一条正常记录）
          · customer_edit / voided / restored / superseded（事件，不是客户行）
        本函数返回"每个授权码一行"，并把最新编辑与作废状态合并进去。
        """
        all_rows = ledger_all()
        # ① 收集每个码的最新编辑 / 作废状态
        edits, status = {}, {}
        for r in all_rows:
            c = (r.get("code") or "").strip().upper()
            if not c:
                continue
            ev = r.get("event")
            if ev == "customer_edit":
                edits[c] = r
            elif ev == "voided":
                status[c] = True
            elif ev == "restored":
                status[c] = False
        # ② 收集客户行（原始记录 + 升级产生的新码记录）
        out = []
        for r in all_rows:
            ev = r.get("event")
            if ev is not None:
                continue                       # 事件行不算客户
            if not r.get("code"):
                continue
            row = dict(r)
            c = (row.get("code") or "").strip().upper()
            e = edits.get(c)
            if e:
                for k in ("customer", "contact", "channel"):
                    if e.get(k) is not None:
                        row[k] = e.get(k)
            row["_voided"] = bool(status.get(c))
            out.append(row)
        return out

    # ---------- 改档位（升级/降级/续期，v2.1.4） ----------
    def _find_row(self, code):
        """按授权码找台账原文，并**应用后续的客户信息编辑事件**（v2.1.5）。

        台账是 append-only：客户改名不覆盖原行，而是追加一条
        customer_edit 事件。这里把最新一次编辑合并到返回的记录上，
        这样界面和升级流程看到的都是最新客户信息。
        """
        code = (code or "").strip()
        if not code:
            return None
        target = code.upper()
        base = None
        latest_edit = None
        for r in ledger_all():
            if (r.get("code") or "").strip().upper() != target:
                continue
            ev = r.get("event")
            if ev == "customer_edit":
                latest_edit = r            # 后出现的覆盖前面的
            elif ev is None and base is None:
                base = dict(r)             # 原始发码记录
        if base is None:
            return None
        if latest_edit:
            for k in ("customer", "contact", "channel"):
                if latest_edit.get(k) is not None:
                    base[k] = latest_edit.get(k)
            base["_edited_at"] = latest_edit.get("issued")
        return base

    def _page_upgrade(self, q):
        """改档位 / 改客户名 / 作废 / 恢复（v2.1.5）。

        用户要求：
          · 客户名称可编辑
          · 改档位处支持作废授权码
          · 作废后还能恢复

        ⚠️ 作废只是**台账标记**，不改变码本身 —— 已经激活的软件照常能用。
           界面上必须写清楚，避免误以为"作废=拦住"。
        """
        code = (q.get("code", [""])[0] or "").strip()
        old = self._find_row(code)
        if not old:
            return self._send(200, page("改档位", f"<div class='card'>"
                f"<div class='err'>找不到这个授权码：{html.escape(code)}</div>"
                f"<a href='/customers'><button class='gray'>返回查客户</button></a>"
                f"</div>", NAV.format("", "on", "")))

        prod = int(old.get("product") or PRODUCT_VIDEO)
        cur_tier = int(old.get("tier") or 0)
        cur_md = old.get("max_devices")
        cur_name = old.get("customer") or ""
        st = self._status_of(code)          # active / voided
        opts = "".join(
            f"<option value='{t}'{' selected' if (prod == PRODUCT_VIDEO and t == cur_tier) else ''}>"
            f"{lbl}</option>" for t, lbl in TIER_LABEL.items())
        if prod == PRODUCT_MP:
            opts = "<option value='0' selected>公众号视频版（不分档）</option>"
        md_opts = "".join(
            f"<option value='{v}'{' selected' if (cur_md == (int(v) if v else None) or (not v and not cur_md)) else ''}>{t}</option>"
            for v, t in (("", "不限台数"), ("1", "1 台"), ("2", "2 台"),
                         ("3", "3 台"), ("5", "5 台"), ("10", "10 台")))

        # 作废状态横幅 + 作废/恢复按钮
        if st == "voided":
            vb = ("<div class='err' style='margin-bottom:12px'>"
                  "🚫 <b>此授权码已作废</b>（仅后台标记）<br>"
                  "<span style='font-size:13px'>注意：已经激活的软件仍然可以正常使用，"
                  "作废只用于台账记录，不会让客户端失效。</span></div>")
            act_btn = (f"<form method='post' action='/restore' style='margin-top:10px'>"
                       f"<input type='hidden' name='code' value='{html.escape(code)}'>"
                       f"<button style='background:#15803d'>♻ 恢复此授权码</button>"
                       f"</form>")
        else:
            vb = ("<div class='ok' style='margin-bottom:12px'>✅ <b>正常</b>"
                  "（未作废）</div>")
            act_btn = (f"<form method='post' action='/void' style='margin-top:10px' "
                       f"onsubmit=\"return confirm('确认作废这个授权码？\\n\\n"
                       f"作废只是后台标记，已经激活的软件仍然能用。\\n"
                       f"如需真正拦住，需要额外开发联网校验。')\">"
                       f"<input type='hidden' name='code' value='{html.escape(code)}'>"
                       f"<button style='background:#b91c1c'>🚫 作废此授权码</button>"
                       f"</form>")

        body = f"""
<div class="card"><h3>客户 · {html.escape(cur_name or '未填昵称')}</h3>
{vb}
<div class="ok" style="margin-bottom:12px">
原档位：<b>{html.escape(old.get('tier_label',''))}</b>　
原价格：¥{html.escape(str(old.get('price','')))}　
台数：{('不限' if not cur_md else str(cur_md)+' 台')}<br>
<span class="mono" style="font-size:12px">{html.escape(code)}</span>
</div>

<!-- ★ 客户名称编辑（v2.1.5） -->
<form method="post" action="/edit_customer">
<input type="hidden" name="code" value="{html.escape(code)}">
<label>客户名称（可直接修改）</label>
<input name="customer" value="{html.escape(cur_name)}" placeholder="客户昵称">
<label>联系方式</label>
<input name="contact" value="{html.escape(old.get('contact',''))}">
<label>渠道</label>
<input name="channel" value="{html.escape(old.get('channel',''))}">
<button class="gray" style="background:#4b5563">保存客户信息</button>
</form>
</div>

<div class="card"><h3>改档位 / 续期</h3>
<form method="post" action="/upgrade">
<input type="hidden" name="code" value="{html.escape(code)}">
<label>新档位</label>
<select name="tier">{opts}</select>
<label>允许台数（默认沿用客户原来的设置）</label>
<select name="max_devices">{md_opts}</select>
<label>本次收款（升级收费 / 降级可填 0）</label>
<input name="price" inputmode="decimal" placeholder="如 100">
<label>备注（可选）</label>
<input name="note" placeholder="如 补差价升级">
<button>生成新授权码</button>
</form>
<p class="meta" style="margin-top:10px">
生成新码后发给客户，粘贴即可生效，<b>不用重装</b>。
客户信息与台数自动沿用。旧码标记「已升级」，历史保留。</p>
</div>

<div class="card"><h3>作废 / 恢复</h3>
<p class="meta" style="margin-bottom:8px">
作废仅在本台账标记状态，<b>不会让客户的软件失效</b>（软件离线工作）。
用于登记"这个码已退款/已弃用"，方便你日后查账。</p>
{act_btn}
</div>

<div class="card"><a href="/customers"><button class="gray">返回查客户</button></a></div>"""
        return self._send(200, page("客户管理", body, NAV.format("", "on", "")))

    # ---------- 作废状态（台账事件驱动） ----------
    def _status_of(self, code: str) -> str:
        """查某码当前状态：active / voided。

        ★ 为什么用台账事件而不是改原记录：
          台账是 append-only（只追加不改写），历史不可篡改。
          作废/恢复各追加一条事件，取该码**最后一条状态事件**为准。
        """
        code_u = (code or "").strip().upper()
        st = "active"
        for r in ledger_all():
            if (r.get("code") or "").strip().upper() != code_u:
                continue
            ev = r.get("event")
            if ev == "voided":
                st = "voided"
            elif ev == "restored":
                st = "active"
        return st

    def _do_void(self, form, voided=True):
        """作废 / 恢复（追加事件，不改原记录）。"""
        code = (form.get("code") or "").strip()
        if not self._find_row(code):
            return self._redirect("/customers")
        try:
            ledger_add({
                "event": "voided" if voided else "restored",
                "code": code,
                "issued": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
        except Exception:
            pass
        self._redirect("/upgrade?code=" + code.replace("-", "%2D"))

    def _do_edit_customer(self, form):
        """改客户名称/联系方式/渠道（v2.1.5）。

        ★ 台账 append-only → 不改原行，追加一条 customer_edit 事件，
          里面写明新值；查询时以最新一条 customer_edit 为准。
        """
        code = (form.get("code") or "").strip()
        old = self._find_row(code)
        if not old:
            return self._redirect("/customers")
        try:
            ledger_add({
                "event": "customer_edit",
                "code": code,
                "customer": form.get("customer", "").strip(),
                "contact": form.get("contact", "").strip(),
                "channel": form.get("channel", "").strip(),
                "issued": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
        except Exception:
            pass
        self._redirect("/upgrade?code=" + code.replace("-", "%2D"))

    def _do_upgrade(self, form):
        """处理改档位：沿用客户信息，生成新码，标记旧码。"""
        old_code = (form.get("code") or "").strip()
        old = self._find_row(old_code)
        if not old:
            return self._redirect("/customers")
        try:
            prod = int(old.get("product") or PRODUCT_VIDEO)
            tier = int(form.get("tier") or 0)
            if prod == PRODUCT_MP:
                tier = 0
            md_raw = (form.get("max_devices") or "").strip()
            md = int(md_raw) if md_raw else None
            dev = (old.get("device") or "").strip()   # 绑定版沿用原绑定
            new_code = make_code(tier, product=prod,
                                 device=dev if dev else "",
                                 max_devices=None if dev else md)
        except Exception as e:                              # noqa: BLE001
            return self._send(200, page("改档位", f"<div class='card'>"
                f"<div class='err'>生成失败：{html.escape(str(e))}</div>"
                f"<a href='/customers'><button class='gray'>返回</button></a>"
                f"</div>", NAV.format("", "on", "")))

        rec = {
            "code": new_code,
            "tier": tier,
            "tier_label": (TIER_LABEL.get(tier, "公众号视频版（不分档）")
                           if prod == PRODUCT_VIDEO
                           else "公众号视频版（不分档）"),
            "product": prod,
            "days": 0,
            "customer": old.get("customer", ""),      # ★ 沿用
            "contact": old.get("contact", ""),        # ★ 沿用
            "channel": old.get("channel", ""),        # ★ 沿用
            "price": form.get("price", "").strip(),
            "device": dev,
            "max_devices": md if not dev else None,
            "note": form.get("note", "").strip(),
            "features": prod,
            "source": "web-admin-upgrade",
            "renewed_from": old_code,                 # ★ 指向旧码，形成历史链
            "old_tier_label": old.get("tier_label", ""),
            "issued": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        ledger_add(rec)
        # 旧码标记为已升级（保留记录，不删；台账本来就是 append-only）
        try:
            self._mark_superseded(old_code, new_code)
        except Exception:
            pass
        self._redirect("/?code=" + new_code.replace("-", "%2D"))

    def _mark_superseded(self, old_code: str, new_code: str):
        """在台账里给旧码补一条 superseded_by 记录（append-only，不改旧行）。"""
        try:
            ledger_add({
                "event": "superseded",
                "code": old_code,
                "superseded_by": new_code,
                "issued": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
        except Exception:
            pass

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
