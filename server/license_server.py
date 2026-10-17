#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sphdl 授权上报服务端（作者侧，运行在 VPS）。

设计原则（2026-10-16 定）
------------------------
1. **零第三方依赖**：只用 Python 标准库 → 不动 VPS 的 pip 环境，
   对现有服务（bot/cloudflared/zongheng）零风险。
2. **独立端口 + 独立 systemd**：默认 3010，与现有 3001/3002/3003/8788/8791 隔离。
3. **隐私最小化**（PIPL 合规）：
   - 客户端只传 code_hash / device_hash（SHA256 截断），**不传原始硬件 ID**
   - 服务端**不存完整 IP**，只存 /24 段（如 1.2.3.x）用于判断"是否同一网络"
   - 不存 User-Agent、不存任何可直接定位个人的信息
4. **上报永远不阻断客户端**：所有异常都返回 200，客户端侧失败静默。

接口
----
POST /api/report     激活上报（客户端→服务端）
    body: {"code": "<授权码>", "device": "<设备码>", "version": "2.0.19", "ts": 123}
    resp: {"ok": true, "first": true, "devices": 1}

GET  /api/stats      统计（需 X-Auth 头 = 管理令牌）
GET  /api/suspect    可疑列表：一码多机 / 一机多码（需 X-Auth）
GET  /health         健康检查（无需鉴权）

启动
----
    python3 server.py                       # 默认 127.0.0.1:3010
    python3 server.py --host 0.0.0.0 --port 3010
    SPHDL_ADMIN_TOKEN=xxx python3 server.py  # 设管理令牌
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("SPHDL_DB", BASE_DIR / "data" / "license.db"))
ADMIN_TOKEN = os.environ.get("SPHDL_ADMIN_TOKEN", "")
MAX_BODY = 4096                     # 上报体上限，防滥用
_lock = threading.Lock()


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}",
          flush=True)


# ---------------- 存储 ----------------

def db_init():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code_hash   TEXT NOT NULL,
                device_hash TEXT NOT NULL,
                version     TEXT,
                ip24        TEXT,
                first_seen  TEXT NOT NULL,
                last_seen   TEXT NOT NULL,
                seen_count  INTEGER NOT NULL DEFAULT 1,
                UNIQUE(code_hash, device_hash)
            )""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_code ON reports(code_hash)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_dev ON reports(device_hash)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                k TEXT PRIMARY KEY, v TEXT)""")


def h16(s: str) -> str:
    """SHA256 前 16 位 —— 与客户端算法一致。

    存哈希而非明文：泄露了也无法还原授权码/设备码，但仍可做唯一性比对。
    """
    return hashlib.sha256((s or "").strip().upper().encode("utf-8")).hexdigest()[:16]


def ip24(addr: str) -> str:
    """把 IP 截断成 /24 段（IPv4）或 /64 段（IPv6），用于"是否同一网络"判断。

    不存完整 IP —— 最小化个人信息。
    """
    if not addr:
        return ""
    if ":" in addr:                       # IPv6
        return ":".join(addr.split(":")[:4]) + "::"
    p = addr.split(".")
    return ".".join(p[:3]) + ".x" if len(p) == 4 else addr


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def record(code: str, device: str, version: str, ip: str) -> dict:
    """记录一条上报。返回 {first, devices, count}。"""
    ch, dh = h16(code), h16(device)
    ts = now_iso()
    i24 = ip24(ip)
    with _lock, sqlite3.connect(DB_PATH) as c:
        row = c.execute(
            "SELECT seen_count FROM reports WHERE code_hash=? AND device_hash=?",
            (ch, dh)).fetchone()
        if row:
            c.execute("""UPDATE reports SET last_seen=?, seen_count=seen_count+1,
                         version=?, ip24=? WHERE code_hash=? AND device_hash=?""",
                      (ts, version, i24, ch, dh))
            first = False
        else:
            c.execute("""INSERT INTO reports
                         (code_hash, device_hash, version, ip24,
                          first_seen, last_seen, seen_count)
                         VALUES (?,?,?,?,?,?,1)""",
                      (ch, dh, version, i24, ts, ts))
            first = True
        n = c.execute("SELECT COUNT(DISTINCT device_hash) FROM reports "
                      "WHERE code_hash=?", (ch,)).fetchone()[0]
    return {"first": first, "devices": n}


def stats() -> dict:
    with sqlite3.connect(DB_PATH) as c:
        total = c.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        codes = c.execute("SELECT COUNT(DISTINCT code_hash) FROM reports").fetchone()[0]
        devs = c.execute("SELECT COUNT(DISTINCT device_hash) FROM reports").fetchone()[0]
        multi = c.execute("""SELECT COUNT(*) FROM (
            SELECT code_hash FROM reports
            GROUP BY code_hash HAVING COUNT(DISTINCT device_hash)>1)""").fetchone()[0]
        recent = c.execute("""SELECT first_seen FROM reports
                              ORDER BY id DESC LIMIT 1""").fetchone()
    return {"total_reports": total, "distinct_codes": codes,
            "distinct_devices": devs, "codes_multi_device": multi,
            "last_report": recent[0] if recent else None}


def suspects() -> dict:
    """可疑清单：一码多机（转卖）+ 一机多码（收集码）。"""
    with sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        multi_dev = [dict(r) for r in c.execute("""
            SELECT code_hash, COUNT(DISTINCT device_hash) AS devices,
                   COUNT(*) AS rows_, MIN(first_seen) AS first_seen,
                   MAX(last_seen) AS last_seen,
                   GROUP_CONCAT(DISTINCT ip24) AS ips
            FROM reports GROUP BY code_hash
            HAVING devices > 1 ORDER BY devices DESC""")]
        multi_code = [dict(r) for r in c.execute("""
            SELECT device_hash, COUNT(DISTINCT code_hash) AS codes,
                   GROUP_CONCAT(DISTINCT code_hash) AS code_hashes
            FROM reports GROUP BY device_hash
            HAVING codes > 1 ORDER BY codes DESC""")]
    return {"one_code_many_devices": multi_dev, "one_device_many_codes": multi_code}


# ---------------- HTTP ----------------

class Handler(BaseHTTPRequestHandler):
    server_version = "sphdl-license/1.0"

    def _send(self, code: int, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _authed(self) -> bool:
        if not ADMIN_TOKEN:
            return False
        return self.headers.get("X-Auth", "") == ADMIN_TOKEN

    def _client_ip(self) -> str:
        """取真实客户端 IP（Cloudflare 隧道场景）。

        ★ 为什么需要这个：走 Cloudflare 隧道时，`client_address[0]` 恒为
          127.0.0.1（本机回环），真实客户 IP 在 `CF-Connecting-IP` 头里。
          不取这个头的话，「同网络判断」功能完全失效（全是 127.0.0.x）。

        隐私：取到后仍会在 record() 里被 ip24() 截断成 /24 段，
              **不落完整 IP**。
        安全：只信任 Cloudflare 的固定头；若有多个值取第一个。
        """
        for h in ("CF-Connecting-IP", "X-Forwarded-For", "X-Real-IP"):
            v = (self.headers.get(h) or "").strip()
            if v:
                return v.split(",")[0].strip()
        return self.client_address[0] if self.client_address else ""

    def do_GET(self):                                   # noqa: N802
        path = urlparse(self.path).path
        if path == "/health":
            self._send(200, {"ok": True, "service": "sphdl-license"})
        elif path == "/api/stats":
            if not self._authed():
                self._send(403, {"ok": False, "err": "forbidden"})
            else:
                self._send(200, stats())
        elif path == "/api/suspect":
            if not self._authed():
                self._send(403, {"ok": False, "err": "forbidden"})
            else:
                self._send(200, suspects())
        else:
            self._send(404, {"ok": False, "err": "not found"})

    def do_POST(self):                                  # noqa: N802
        path = urlparse(self.path).path
        if path != "/api/report":
            self._send(404, {"ok": False, "err": "not found"})
            return
        # ★ 无论发生什么，都尽量返回 200 —— 上报绝不能影响客户端体验
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_BODY:
                self._send(200, {"ok": False, "err": "bad length"})
                return
            raw = self.rfile.read(n)
            try:
                data = json.loads(raw.decode("utf-8", errors="replace"))
            except Exception:
                self._send(200, {"ok": False, "err": "bad json"})
                return
            code = str(data.get("code") or "")[:128]
            device = str(data.get("device") or "")[:128]
            version = str(data.get("version") or "")[:32]
            if not code:
                self._send(200, {"ok": False, "err": "no code"})
                return
            ip = self._client_ip()
            r = record(code, device, version, ip)
            self._send(200, {"ok": True, **r})
        except Exception as e:                          # noqa: BLE001
            log(f"report 异常（已容忍）: {e}")
            self._send(200, {"ok": False, "err": "internal"})

    def log_message(self, fmt, *args):
        # 只记路径与状态，不记 IP / UA（隐私最小化）
        try:
            log(f"{self.command} {urlparse(self.path).path} → {args[1]}")
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description="sphdl 授权上报服务端")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3010)
    ap.add_argument("--init-only", action="store_true", help="只建库退出")
    a = ap.parse_args()

    db_init()
    log(f"数据库: {DB_PATH}")
    if a.init_only:
        log("已建库，退出")
        return 0
    if not ADMIN_TOKEN:
        log("⚠️  未设 SPHDL_ADMIN_TOKEN → /api/stats 与 /api/suspect 将不可访问")
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    log(f"监听 http://{a.host}:{a.port}（Ctrl-C 退出）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
