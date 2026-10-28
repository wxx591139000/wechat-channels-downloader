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
import re
import secrets
import sys
import sqlite3
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
DATA.mkdir(parents=True, exist_ok=True)
LEDGER = DATA / "licenses.jsonl"
TOKEN_FILE = BASE / ".admin_token"
LIC_DB = Path("/opt/sphdl-license/data/license.db")
# ★ v2.2.3：话术库（预定义套路词 + 各版本网盘链接，可编辑）
SNIPPETS = DATA / "snippets.json"
# ★ v2.2.4：版本更新要点来源（本仓库 CHANGELOG 同步到 VPS 后自动解析）
CHANGELOG = DATA / "CHANGELOG.md"

PORT = int(os.environ.get("SPHDL_ADMIN_PORT", "3011"))
BIND = os.environ.get("SPHDL_ADMIN_BIND", "127.0.0.1")

# ---- 授权码算法（与 sphdl/license.py 保持一致）----
CODE_PREFIX = "SPHDL"
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
PAYLOAD_LEN_V1, MAC_LEN, V1, V2, V3, V4 = 5, 10, 1, 2, 3, 4
V2_PAYLOAD_LEN, V3_PAYLOAD_LEN, V4_PAYLOAD_LEN = 6, 10, 7
MSG_V1, MSG_V2, MSG_V3, MSG_V4 = b"SPHDLv1", b"SPHDLv2", b"SPHDLv3", b"SPHDLv4"
MSG_V5 = b"SPHDLv5"          # ★ v2.6.0：能力位码型
PRODUCT_VIDEO, PRODUCT_MP = 1, 2
# ★ v2.6.0：抖音独立产品线 + 点赞增强能力位
PRODUCT_DOUYIN = 3
V5 = 5                       # ver=5：带能力位（caps）的码型
CAP_LIKE_STATS = 1 << 0      # 点赞增强包
CAP_LABELS = {CAP_LIKE_STATS: "点赞增强包"}
TIER_TO_CODE = {500: 1, 1000: 2, 2000: 3, 20000: 4}
CODE_TO_TIER = {v: k for k, v in TIER_TO_CODE.items()}
TIER_LABEL = {500: "轻量（500 条）", 1000: "标准（1000 条）",
              2000: "旗舰（2000 条）", 20000: "至尊（2 万条）"}
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
    pre = (MSG_V5 if ver == V5 else MSG_V4 if ver == V4 else MSG_V3 if ver == V3
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


def code_label(tier: int, product: int, caps: int = 0,
               max_devices=None) -> str:
    """生成「复制用」的码说明文案（★ v2.6.0 客户需求）。

    客户原话：
      「发码里点复制授权码时，应该是『轻量版2台授权码：XXXX』
        『公众号视频授权码：XXXX』『点赞增强包授权码：XXXX』格式，
        这样我粘贴时方便」

    返回形如：
        「点赞增强包授权码：」
        「轻量版2台授权码：」
        「公众号视频授权码：」
        「抖音批量下载授权码：」
    """
    if product == PRODUCT_DOUYIN:
        return "抖音批量下载授权码："
    if product == PRODUCT_MP:
        return "公众号视频授权码："
    # 视频号：档位 + 台数
    tier_txt = TIER_LABEL.get(tier, f"{tier}条")
    short = (tier_txt.split("（")[0].split("(")[0].strip()
             or f"{tier}条")
    if caps:
        # 带能力位 → 以能力名为主（客户买的是增值功能）
        cap_txt = "、".join(n for b, n in CAP_LABELS.items() if caps & b) \
            or "增值功能"
        return f"{cap_txt}授权码："
    if max_devices:
        return f"{short}{int(max_devices)}台授权码："
    return f"{short}授权码："


def make_code(tier: int, product: int = PRODUCT_VIDEO, days: int = 0,
              device: str = "", max_devices=None, caps: int = 0) -> str:
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
    # ★ v2.6.0：能力位（点赞增强包）→ 走 ver=5
    #   载荷: ver(5) | tier | days//8 | product | caps | maxdev | rand(2)
    if caps:
        if device:
            raise ValueError(
                "暂不支持「设备绑定码 + 点赞增强」组合，请改用台数限制")
        md = min(255, max(0, int(max_devices or 0)))
        payload = bytes([V5, tier_code, days_step, product, int(caps), md]) + rand
        blob = payload + _mac(payload, V5)
        return f"{CODE_PREFIX}-{_group(f'{CODE_PREFIX}-' + _b32enc(blob))}"
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


def h16(s: str) -> str:
    """SHA256 前 16 位 —— 与客户端 / license_server 算法一致。

    ⚠️ 归一化必须与 `license_server.h16()` 完全一致（strip + upper），
    否则用授权码算出的 hash 对不上上报记录，查客户页就永远显示"未激活"。
    """
    return hashlib.sha256((s or "").strip().upper()
                          .encode("utf-8")).hexdigest()[:16]


def _devices_of_code(code: str) -> list:
    """查某个授权码在**上报库**里激活过哪些设备（v2.1.9）。

    ★ 为什么需要（用户报障 2026-10-19）：「查客户」里看不到客户实际激活的设备码。
      根因是**两套数据源各查各的**：
        · 查客户页读 licenses.jsonl —— device 字段是**发码时手填的**（基本都留空）
        · 客户实际激活的设备在 license.db —— 由客户端激活时自动上报
      两者从不关联 → 查客户页什么都不显示，用户以为这功能没做。

    返回 [{device_hash, version, first_seen, last_seen, seen_count}, ...]
    失败一律返回 []（绝不抛异常 —— 上报库出问题不能让查客户页整页报错）。
    """
    if not code:
        return []
    try:
        if not LIC_DB.exists():
            return []
        ch = h16(code)
        c = sqlite3.connect(f"file:{LIC_DB}?mode=ro", uri=True, timeout=5)
        rows = list(c.execute(
            "SELECT device_hash, version, first_seen, last_seen, seen_count "
            "FROM reports WHERE code_hash = ? ORDER BY first_seen", (ch,)))
        c.close()
        return [{"device_hash": r[0], "version": r[1], "first_seen": r[2],
                 "last_seen": r[3], "seen_count": r[4]} for r in rows]
    except Exception:
        return []


DEFAULT_TEMPLATE = "请您查收，满意的话帮忙给个红花，遇到问题的话联系我。"


def snippets_load() -> dict:
    """读取话术库（模板 + 各版本网盘链接）。

    ★ 为什么要有（2026-10-21 用户需求）：
      客户下单后要手工拼消息："套路词 + 对应版本的百度网盘链接"，
      每次都要翻网盘复制粘贴，容易贴错版本、贴漏提取码。
      这里把「套路词」和「各版本网盘信息」存成可编辑的配置，
      发码时选一下版本就能一键复制完整话术。

    结构：
      {
        "template": "请您查收，满意的话帮忙给个红花，遇到问题的话联系我。",
        "versions": [
          {"ver": "2.2.2",
           "filename": "微信视频号下载器-v2.2.2-win64-portable.zip",
           "link": "https://pan.baidu.com/s/xxx?pwd=abcd",
           "pwd": "abcd"}
        ]
      }
    """
    blank = {"template": DEFAULT_TEMPLATE, "versions": []}
    try:
        if not SNIPPETS.exists():
            return blank
        d = json.loads(SNIPPETS.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            return blank
        out = {
            "template": str(d.get("template") or DEFAULT_TEMPLATE),
            "versions": [],
        }
        for v in (d.get("versions") or []):
            if not isinstance(v, dict):
                continue
            ver = str(v.get("ver") or "").strip()
            if not ver:
                continue
            link = str(v.get("link") or "").strip()
            chan = str(v.get("channel") or "").strip() or channel_of(link)
            out["versions"].append({
                "ver": ver,
                "channel": chan,
                "filename": str(v.get("filename") or "").strip(),
                "link": link,
                "pwd": str(v.get("pwd") or "").strip(),
                # ★ v2.2.4：本版相比上一版的主要升级内容（可编辑，空则从 CHANGELOG 取）
                "notes": str(v.get("notes") or "").strip(),
            })
        # 版本号倒序；同版本内按渠道名排序（稳定）
        out["versions"].sort(
            key=lambda x: (_ver_key(x["ver"]), x["channel"]), reverse=True)
        return out
    except Exception:
        return blank


def _ver_key(v: str):
    """版本号排序键（"2.2.10" 要排在 "2.2.9" 之后，不能按字符串排）。"""
    parts = []
    for seg in str(v).replace("-", ".").split("."):
        parts.append(int(seg) if seg.isdigit() else 0)
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


def snippets_save(d: dict):
    """写回话术库（原子写，避免写一半断电把文件弄坏）。"""
    tmp = SNIPPETS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(SNIPPETS)


def channel_of(link: str) -> str:
    """从链接判断是哪个网盘（用于同一版本多网盘并存时的区分）。"""
    lk = (link or "").lower()
    if "pan.quark.cn" in lk:
        return "夸克"
    if "baidu.com" in lk:
        return "百度"
    if "drive.uc.cn" in lk:
        return "UC"
    if "aliyundrive" in lk or "alipan" in lk:
        return "阿里"
    if "115.com" in lk:
        return "115"
    return "其他"


def changelog_notes(ver: str) -> str:
    """从 CHANGELOG.md 里抽出某个版本的更新要点（v2.2.4）。

    ★ 为什么要有：用户希望发码时能直接看到「这个版本比上一版改了什么」，
      并且能一键复制发给老客户。手写一遍重复劳动，而 CHANGELOG 里本来就有。

    解析规则（对应本仓库 CHANGELOG 的固定格式）：
        ## [2.2.3] 2026-10-23 — 公众号下载修复 + 交互反馈
        ### 修复（3 个客户报障）
        1. **★ 公众号视频下载失败（403 Forbidden）**
           ...
        ## [2.2.2] ...        ← 到下一个 ## [ 为止

    返回去掉 markdown 装饰的纯文本（客户看得懂的版本）。
    """
    if not CHANGELOG.exists():
        return ""
    try:
        txt = CHANGELOG.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    v = str(ver or "").strip().lstrip("vV")
    if not v:
        return ""
    # 找该版本段落（到下一个 "## [" 或文件末尾）
    pat = re.compile(r"^##\s*\[" + re.escape(v) + r"\][^\n]*\n(.*?)(?=^##\s*\[|\Z)",
                     re.S | re.M)
    m = pat.search(txt)
    if not m:
        return ""
    body = m.group(1)

    out = []
    for line in body.splitlines():
        ln = line.rstrip()
        if not ln.strip():
            if out and out[-1] != "":
                out.append("")
            continue
        # 跳过「已知限制」这类技术段落？保留 —— 客户也需要知道
        ln = re.sub(r"^#{1,6}\s*", "", ln)          # 去标题井号
        ln = ln.replace("**", "").replace("`", "")   # 去加粗/行内代码
        ln = re.sub(r"^\s*[-*]\s+", "· ", ln)        # 列表符号统一
        ln = re.sub(r"^\s*", "", ln)
        out.append(ln)
    # 去掉尾部空行
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out)


def version_notes(ver: str) -> str:
    """取某版本的更新要点：话术库里手填的优先，没填/不在库里则用 CHANGELOG。

    ★ 注意「不在库里」也要回退到 CHANGELOG ——
      话术库只存"正在卖"的几个版本，历史版本（如 2.2.2 已下架）
      在库里没有条目，但客户问"这版改了什么"时仍要能答上。
    """
    v = str(ver or "").strip().lstrip("vV")
    try:
        d = snippets_load()
        for item in d["versions"]:
            if item["ver"] == v and item.get("notes"):
                return item["notes"]
    except Exception:
        pass
    return changelog_notes(v)


def parse_share_text(text: str) -> dict:
    """从网盘「分享文字」里自动解析出 版本号/文件名/链接/提取码。

    ★ 为什么要有（2026-10-21 用户需求）：
      百度网盘每次生成的分享文字是固定格式，手工拆成 4 个字段太麻烦、
      还容易填错版本号。这里一次粘贴全部解析出来。

    支持两个网盘（2026-10-21 加夸克）：

    【百度网盘】
      通过网盘分享的文件：微信视频号下载器-v2.1.8-win64-portable.zip
      链接: https://pan.baidu.com/s/1sAqh5NZiMPGdceG4z_TEPA?pwd=bfcr 提取码: bfcr

    【夸克网盘】
      我用夸克网盘给你分享了「微信视频号下载器-v2.2.2-win64-portable.zip」，
      点击链接或复制整段内容，打开「夸克网盘APP」即可获取。
      /~f3fa3bgctl~/:/          ← ★ 这行不是链接，别抓它
      链接：https://pan.quark.cn/s/4402de3baafd?pwd=xsSS
      提取码：xsSS

    ▲ 这个函数必须与实际粘贴内容**逐字符核对过**再改，别凭想象改正则。
    """
    out = {"ver": "", "filename": "", "link": "", "pwd": ""}
    if not text:
        return out
    t = str(text).replace("\r\n", "\n").replace("\r", "\n").strip()

    # ---- ① 文件名 ----
    #   a) 夸克风格：书名号里带扩展名的（「微信视频号下载器-v2.2.2-...zip」）
    m = re.search(r"[「『]([^」』\n]+?\.(?:zip|rar|7z|exe|tar\.gz))[」』]", t, re.I)
    if m:
        out["filename"] = m.group(1).strip()
    #   b) 百度风格：「通过网盘分享的文件：X」
    if not out["filename"]:
        m = re.search(
            r"(?:通过网盘分享的文件|分享的文件|分享文件|文件名称|文件名)\s*[:：]\s*"
            r"([^\n]+?)\s*$",
            t, re.M)
        if m:
            out["filename"] = m.group(1).strip()
    #   c) 兜底：任意位置像安装包文件名的片段
    if not out["filename"]:
        m = re.search(r"([^\s/\\:：「」『』]+\.(?:zip|rar|7z|exe|tar\.gz))", t, re.I)
        if m:
            out["filename"] = m.group(1).strip()

    # ---- ② 链接 ----
    #   ★ 夸克那行 `/~f3fa3bgctl~:/` 不是 http 链接，这里的 \bhttps?:// 不会误抓。
    #   支持：pan/yun.baidu.com、pan.quark.cn、drive.uc.cn（夸克同源）
    m = re.search(
        r"(https?://(?:(?:pan|yun)\.baidu\.com|pan\.quark\.cn|drive\.uc\.cn)"
        r"/(?:s/[\w\-]+|share/init\?surl=[\w\-]+)"
        r"(?:\?[^\s\"'<>]*)?)", t)
    if not m:
        # 兜底：任意 http(s) 链接（其他网盘也能用）
        m = re.search(r"(https?://[^\s\"'<>]+)", t)
    if m:
        out["link"] = m.group(1).strip().rstrip("，,。；;")
        # 去掉链接尾部粘上的中文/标点（如 "?pwd=xsSS提取码"）
        out["link"] = re.split(r"(?<=[A-Za-z0-9])(?=[\u4e00-\u9fff])",
                               out["link"])[0]

    # ---- ③ 提取码 ----
    #   优先从链接里的 pwd= 取；再找「提取码: xxxx」
    if out["link"]:
        mp = re.search(r"[?&]pwd=([A-Za-z0-9]{2,12})", out["link"])
        if mp:
            out["pwd"] = mp.group(1)
    if not out["pwd"]:
        mp = re.search(r"(?:提取码|访问码|密码|口令)\s*[:：]?\s*([A-Za-z0-9]{4,12})", t)
        if mp:
            out["pwd"] = mp.group(1)

    # ---- ④ 版本号：从文件名里抽 v2.1.8 ----
    src = out["filename"] or t
    mv = re.search(r"[vV]\s*(\d+(?:\.\d+){1,3})", src)
    if mv:
        out["ver"] = mv.group(1)
    else:
        # 文件名里没 v 前缀时，退一步找形如 2.1.8 的片段
        mv = re.search(r"(?<![\d.])(\d+\.\d+(?:\.\d+)*)(?![\d.])", src)
        if mv:
            out["ver"] = mv.group(1)

    return out


def snippet_text(ver: str, channel: str = "") -> str:
    """拼出某个版本+渠道的完整话术（模板 + 网盘信息）。

    ★ channel 为空时取该版本的第一条（用于兼容旧调用）。
    """
    d = snippets_load()
    cands = [v for v in d["versions"] if v["ver"] == ver]
    if channel:
        cands = [v for v in cands if v["channel"] == channel] or cands
    item = cands[0] if cands else None
    if not item:
        return ""
    lines = [d["template"].strip()]
    if item["filename"]:
        lines.append(f"通过网盘分享的文件：{item['filename']}")
    if item["link"]:
        link = item["link"]
        if item["pwd"] and "pwd=" not in link:
            link = f"{link}?pwd={item['pwd']}"
        if item["pwd"]:
            lines.append(f"链接: {link} 提取码: {item['pwd']}")
        else:
            lines.append(f"链接: {link}")
    return "\n".join([x for x in lines if x])


def label_from_ledger(code: str) -> str:
    """按授权码反查台账，得出「XX授权码：」前缀（v2.6.4）。

    ★ 为什么需要（2026-10-28 事故）：
      `/?code=XXX` 这个结果页（_page_new）只拿到 code 字符串，
      **拿不到 tier/product/caps**（那是 _do_gen 的局部变量）。
      我上一版直接在 _page_new 里写 `code_label(tier, product, caps, md)`
      → `NameError: name 'tier' is not defined` → 页面 500
      → Cloudflare 报 502 Bad Gateway（手机端最明显）。
      正确做法：按 code 去台账查它当初是用什么参数发的。
    """
    try:
        for r in ledger_all():
            if r.get("code") == code:
                return code_label(
                    int(r.get("tier") or 0),
                    int(r.get("product") or PRODUCT_VIDEO),
                    int(r.get("caps") or 0),
                    r.get("max_devices"))
    except Exception:
        pass
    return "授权码："          # 查不到就退回最朴素的写法，不能崩


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
/* ★ v2.6.2：复选框必须单独恢复原生外观 ——
   上面那条 `input{...-webkit-appearance:none}` 会把 checkbox 的**勾选标记**
   一起干掉，导致点上去看不到 ✓（客户报障）。
   属性选择器 (0,1,1) 优先级高于元素选择器 (0,0,1)，覆盖生效。
   accent-color 让勾用品牌色。 */
input[type=checkbox]{
  -webkit-appearance:auto;appearance:auto;
  width:22px;height:22px;min-width:22px;padding:0;margin:0;
  border:none;border-radius:5px;background:transparent;
  accent-color:#1e3a5f;vertical-align:middle;cursor:pointer;
  flex:0 0 auto;
}
/* 增值功能勾选卡：整行可点，选中后高亮 */
.capbox{display:flex;align-items:center;gap:10px;
  padding:11px 12px;border:1px solid #d1d5db;border-radius:9px;
  background:#fff;cursor:pointer;margin:6px 0 8px;
  font-size:14px;color:#111827;font-weight:500;line-height:1.35}
.capbox:active{background:#f8fafc}
.capbox .capdesc{color:#6b7280;font-size:12.5px;font-weight:400}
.capbox:has(input:checked){border-color:#0f766e;background:#f0fdfa;
  box-shadow:0 0 0 3px rgba(15,118,110,.10)}
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
/* ★ v2.2.3 话术复制区 */
.snip{background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;
      padding:12px;margin:10px 0;font-size:13.5px;color:#334155;
      white-space:pre-wrap;word-break:break-all;line-height:1.7;
      max-height:260px;overflow:auto}
.row2{display:flex;gap:8px}
.row2>*{flex:1}
.btn2{padding:13px;border:none;border-radius:10px;background:#0f766e;color:#fff;
      font-size:15px;font-weight:600;cursor:pointer;width:100%}
.btn2:active{background:#0b5750}
.btn2.gray{background:#e5e7eb;color:#374151}
.btn2:disabled{background:#cbd5e1;color:#64748b}
.ver-item{border:1px solid #e5e7eb;border-radius:10px;padding:11px 12px;
          margin-bottom:8px;font-size:13px}
.ver-item b{color:#0f766e;font-size:14px}
.ver-item .lk{font-family:Consolas,monospace;font-size:11.5px;color:#64748b;
              word-break:break-all;display:block;margin-top:3px}
.del{background:#fee2e2;color:#991b1b;border:none;border-radius:7px;
     padding:7px 12px;font-size:13px;cursor:pointer;width:auto}
"""


# ★ v2.2.3：复制到剪贴板（带降级）
#   navigator.clipboard 只在 HTTPS / localhost 可用；
#   手机通过隧道访问时若被降级成 http 会失败 → 必须有 execCommand 兜底
JS_COPY = """
function cp(txt, btn){
  var done=function(){
    if(btn){var old=btn.innerText;btn.innerText='✅ 已复制';
      setTimeout(function(){btn.innerText=old;},1400);}
  };
  try{
    if(navigator.clipboard && window.isSecureContext){
      navigator.clipboard.writeText(txt).then(done).catch(function(){fb();});
    }else{fb();}
  }catch(e){fb();}
  function fb(){
    var ta=document.createElement('textarea');
    ta.value=txt;
    ta.style.cssText='position:fixed;left:-9999px;top:0';
    document.body.appendChild(ta);
    ta.focus();ta.select();
    try{document.execCommand('copy');done();}catch(e){alert('复制失败，请长按文字手动复制');}
    document.body.removeChild(ta);
  }
}
"""


# ★ v2.2.3：粘贴即解析脚本（独立常量，避免与 f-string 的 {} 冲突）
JS_PASTE = r"""<script>
/* ★ 粘贴即解析（前端即时反馈，不用等提交）
   与服务端 parse_share_text() 同一套规则；服务端也有解析兜底，
   前端失效（老浏览器）时仍能保存。 */
function autoParse(){
  var el=document.getElementById('raw');
  var t=(el&&el.value)||'';
  var msg=document.getElementById('pmsg');
  if(!t.trim()){msg.innerText='粘进来后下面 4 栏会自动填好，确认无误再点保存。';return;}
  t=t.replace(/\r\n/g,'\n').replace(/\r/g,'\n');

  // 文件名（夸克在「」里 / 百度在「通过网盘分享的文件：」后）
  var fn='';
  var m=t.match(/[「『]([^」』\n]+?\.(?:zip|rar|7z|exe|tar\.gz))[」』]/i);
  if(m){fn=m[1].trim();}
  if(!fn){
    var mb=t.match(/(?:通过网盘分享的文件|分享的文件|分享文件|文件名称|文件名)\s*[:：]\s*([^\n]+?)\s*$/m);
    if(mb){fn=mb[1].trim();}
  }
  if(!fn){
    var m2=t.match(/([^\s\/\\:：「」『』]+\.(?:zip|rar|7z|exe|tar\.gz))/i);
    if(m2)fn=m2[1].trim();
  }

  // 链接
  var lk='';
  var ml=t.match(/(https?:\/\/(?:(?:pan|yun)\.baidu\.com|pan\.quark\.cn|drive\.uc\.cn)\/(?:s\/[\w\-]+|share\/init\?surl=[\w\-]+)(?:\?[^\s"'<>]*)?)/);
  if(!ml){ml=t.match(/(https?:\/\/[^\s"'<>]+)/);}
  if(ml){
    lk=ml[1].trim().replace(/[，,。；;]$/,'');
    // 去掉链接尾部粘上的中文（如 "?pwd=xsSS提取码"）
    var cut=lk.search(/[\u4e00-\u9fff]/);
    if(cut>0)lk=lk.slice(0,cut);
  }

  // 提取码
  var pw='';
  if(lk){
    var mp=lk.match(/[?&]pwd=([A-Za-z0-9]{2,12})/);
    if(mp)pw=mp[1];
  }
  if(!pw){
    var mp2=t.match(/(?:提取码|访问码|密码|口令)\s*[:：]?\s*([A-Za-z0-9]{4,12})/);
    if(mp2)pw=mp2[1];
  }

  // 版本号（从文件名抽 v2.1.8）
  var ver='';
  var src=fn||t;
  var mv=src.match(/[vV]\s*(\d+(?:\.\d+){1,3})/);
  if(mv){ver=mv[1];}
  else{
    var mv2=src.match(/(?<![\d.])(\d+\.\d+(?:\.\d+)*)(?![\d.])/);
    if(mv2)ver=mv2[1];
  }

  if(fn)document.getElementById('f_fn').value=fn;
  if(lk)document.getElementById('f_lk').value=lk;
  if(pw)document.getElementById('f_pw').value=pw;
  if(ver)document.getElementById('f_ver').value=ver;

  var got=[];
  if(ver)got.push('版本 '+ver);
  if(fn)got.push('文件名');
  if(lk)got.push('链接');
  if(pw)got.push('提取码 '+pw);
  msg.innerHTML = got.length
    ? '✅ 已识别：'+got.join(' · ')+'（可手动改）'
    : '⚠️ 没识别出来，请手动填下面 4 栏';
}
</script>"""


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
<a href="/snippets" class="{3}">话术</a>
</nav>"""


def nav_on(idx: int) -> str:
    """生成导航高亮参数（4 项，索引 0~3）。

    ★ 用函数而不是到处手写 format 参数 ——
      加导航项时只改这里，不会漏掉某个页面。
    """
    return NAV.format(*["on" if i == idx else "" for i in range(4)])


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

    def _send_json(self, obj, code: int = 200):
        """发 JSON 响应（客户端 API 用）。"""
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass

    def _redirect(self, to, cookie=None, ok=""):
        """303 重定向。ok 非空时把提示塞进 URL（目标页读 q['ok'] 显示）。"""
        if ok:
            sep = "&" if "?" in to else "?"
            to = f"{to}{sep}ok={quote(ok)}"
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
        # ★ v2.3.0：客户端查询吊销名单（方案 B）
        #   放在登录校验**之前** —— 客户端没有后台会话。
        #   只暴露「哪些码被作废」，不含客户名/价格等敏感信息。
        if u.path == "/api/revoked":
            return self._api_revoked(parse_qs(u.query))
            return self._api_revoked()
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
        # ★ v2.2.3：话术库（模板 + 各版本网盘链接，可编辑）
        if p == "/snippets":
            return self._page_snippets(q)
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
        # ★ v2.2.3：话术库保存 / 版本增删
        if u.path == "/snippets_save":
            return self._do_snippets_save(form)
        self._send(404, "not found")

    # ---------- 发码页 ----------
    def _page_new(self, q):
        flash = ""
        code = q.get("code", [""])[0]
        if code:
            sn = snippets_load()
            vers = sn["versions"]
            # 版本下拉：默认选第一个（版本号最大 + 渠道排序靠前的）
            # value 用 "版本||渠道"，让同一版本的百度/夸克能分别选
            vopts = "".join(
                f"<option value=\"{html.escape(v['ver'] + '||' + v.get('channel',''))}\""
                f"{' selected' if i == 0 else ''}>"
                f"v{html.escape(v['ver'])}"
                f"{' · ' + html.escape(v['channel']) if v.get('channel') else ''}"
                f"{' — ' + html.escape(v['filename'][:24]) if v['filename'] else ''}"
                f"</option>"
                for i, v in enumerate(vers))

            if vers:
                snip_block = f"""
<div style="margin-top:14px;padding-top:14px;border-top:1px dashed #cbd5e1">
  <label style="margin-top:0">② 发给客户的话术（选版本 → 一键复制）</label>
  <select id="ver" onchange="snipUpd()">{vopts}</select>
  <div class="snip" id="snip">{html.escape(snippet_text(vers[0]['ver']))}</div>
  <div class="row2">
    <button type="button" class="btn2" id="bc" onclick="copyBoth()">
      📋 复制话术 + 授权码</button>
    <button type="button" class="btn2 gray" onclick="copySnip()">
      只复制话术</button>
  </div>
  <p class="meta" style="margin-top:8px">
    「复制话术 + 授权码」= 套路词 + 网盘链接 + 授权码，一次性粘给客户<br>
    要改套路词或换网盘链接 → <a href="/snippets">点这里编辑话术库</a></p>

  <!-- ★ v2.2.4：本版相比上一版的主要升级内容 -->
  <details id="notesBox" style="margin-top:10px">
    <summary style="cursor:pointer;color:#0f766e;font-weight:600;
      font-size:13.5px">📋 这一版改了什么（可复制发给老客户）</summary>
    <div class="snip" id="notes" style="margin-top:8px"></div>
    <button type="button" class="btn2 gray" onclick="copyNotes()"
      style="margin-top:8px">复制更新说明</button>
  </details>
</div>"""
            else:
                snip_block = """
<div style="margin-top:14px;padding-top:14px;border-top:1px dashed #cbd5e1">
  <div class="warn">⚠️ 话术库还没有版本链接，无法一键复制话术。
  <a href="/snippets">去添加版本和网盘链接</a></div>
</div>"""

            # ★ v2.6.0：复制时带「XX授权码：」前缀，作者粘贴更顺
            # ★ v2.6.4：这里**没有** tier/product/caps 局部变量 ——
            #   必须按 code 去台账反查，否则 NameError → 500 → CF 502
            _lbl = label_from_ledger(code)
            flash = (f"<div class='card'><div class='ok'>✅ 已生成并登记台账</div>"
                     f"<label style='margin-top:10px'>① 授权码</label>"
                     f"<div class='code' id='c'>{html.escape(code)}</div>"
                     f"<button type='button' onclick=\"cp(document.getElementById('lbl')"
                     f".innerText,this)\">复制授权码</button>"
                     f"<div class='code' id='lbl' style='font-size:13px;"
                     f"background:#f1f5f9;color:#0f172a;margin-top:6px;"
                     f"padding:8px 10px;border-radius:8px;word-break:break-all'>"
                     f"{html.escape(_lbl + code)}</div>"
                     f"<p class='meta'>↑ 上面这行是「带说明的完整文案」，点复制就发这个"
                     f"（也可以只发下面那行纯码）</p>"
                     f"<div class='code' id='c2' style='font-size:12px;"
                     f"background:#f8fafc;color:#475569'>{html.escape(code)}</div>"
                     f"<p class='meta'>把码发给客户，在软件「🔑 授权管理」粘贴激活</p>"
                     f"{snip_block}"
                     f"</div>"
                     f"<script>{JS_COPY}"
                     f"const CODE={json.dumps(code)};"
                     f"const LBL={json.dumps(_lbl)};"
                     f"const SNIPS={json.dumps({(v['ver'] + '||' + v.get('channel','')): snippet_text(v['ver'], v.get('channel','')) for v in vers}, ensure_ascii=False)};"
                     f"const NOTES={json.dumps({v['ver']: (v.get('notes') or changelog_notes(v['ver'])) for v in vers}, ensure_ascii=False)};"
                     f"function notesUpd(){{var v=document.getElementById('ver').value.split('||')[0];"
                     f"var el=document.getElementById('notes');"
                     f"if(el)el.innerText=NOTES[v]||'（这一版没有记录更新要点）';}}"
                     f"function copyNotes(){{var v=document.getElementById('ver').value.split('||')[0];"
                     f"cp(NOTES[v]||'','');}}"
                     f"function snipUpd(){{notesUpd();var v=document.getElementById('ver').value;"
                     f"document.getElementById('snip').innerText=SNIPS[v]||'';"
                     f"notesUpd();}}"
                     f"function copySnip(){{var v=document.getElementById('ver').value;"
                     f"cp(SNIPS[v]||'','');}}"
                     f"function copyBoth(){{var v=document.getElementById('ver').value;"
                     f"cp((SNIPS[v]||'')+'\\n\\n'+LBL+CODE,'');}}"
                     f"try{{notesUpd();}}catch(e){{}}"
                     f"</script>")
        opts = "".join(f"<option value={t}>{l}</option>"
                       for t, l in TIER_LABEL.items())
        body = f"""
<div class="card"><h3>发码</h3>
<form method="post" action="/gen">
<label>产品</label>
<select name="product">
<option value="1">视频号下载（4 档）</option>
<option value="2">公众号文章视频（不分档）</option>
<option value="3">抖音批量下载（不分档）</option>
</select>
<label>增值功能（可多选，★会生成 ver=5 码）</label>
<label class="capbox">
  <input type="checkbox" name="cap_like" value="1">
  <span>点赞增强包<br><span class="capdesc">点赞/转发/推荐展示 + 按点赞排序</span></span>
</label>
<p class="meta" style="margin-top:-4px">
勾选后与视频号基础授权**叠加使用**（客户可以同时激活两个码）。<br>
⚠️ 与「设备码死绑」二选一：能力位码只支持台数限制。</p>
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
                                    nav_on(0), flash))

    def _do_gen(self, form):
        try:
            product = int(form.get("product") or PRODUCT_VIDEO)
            tier = int(form.get("tier") or 20000)
            if product in (PRODUCT_MP, PRODUCT_DOUYIN):
                tier = 0
            dev = form.get("device", "").strip()
            md_raw = (form.get("max_devices") or "").strip()
            # 台数与死绑二选一：填了设备码就死绑（优先），否则看台数
            md = None
            if not dev and md_raw:
                md = int(md_raw)
            # ★ v2.6.0：增值能力位（点赞增强包）
            caps = 0
            if form.get("cap_like"):
                caps |= CAP_LIKE_STATS
            if caps and dev:
                raise ValueError(
                    "「设备码死绑」与「增值功能（能力位）」不能同时使用，"
                    "请改用台数限制")
            code = make_code(tier, product=product, device=dev,
                             max_devices=md, caps=caps)
        except Exception as e:                              # noqa: BLE001
            return self._send(200, page("发码", f"<div class='card'>"
                f"<div class='err'>生成失败：{html.escape(str(e))}</div>"
                f"<a href='/'><button class='gray'>返回</button></a></div>"))
        rec = {
            "code": code,
            "tier": tier,
            "tier_label": (
                TIER_LABEL.get(tier, "视频号")
                if product == PRODUCT_VIDEO and not caps
                else ("点赞增强包" if caps
                      else ("抖音批量下载" if product == PRODUCT_DOUYIN
                            else "公众号视频版（不分档）"))),
            "caps": caps,
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
            # ★ v2.1.9：显示**实际激活**的设备（来自上报库，不是发码时手填的）
            devs = _devices_of_code(r.get("code", ""))
            if devs:
                limit = r.get("max_devices") or 0
                used = len(devs)
                if limit:
                    cls = "red" if used > limit else "green"
                    tags.append(f"<span class='tag {cls}'>已激活 {used}/{limit} 台</span>")
                else:
                    tags.append(f"<span class='tag green'>已激活 {used} 台</span>")
            ec = html.escape(r.get("code", ""))
            # 设备明细（折叠显示，避免列表太长）
            dev_block = ""
            if devs:
                lines = []
                for d in devs:
                    lines.append(
                        f"<div class='mono meta' style='margin-left:14px'>"
                        f"└ 设备 {html.escape(d['device_hash'])}　"
                        f"{html.escape(str(d.get('first_seen',''))[:16])}　"
                        f"v{html.escape(str(d.get('version','')))}"
                        f"（上报 {d.get('seen_count',1)} 次）</div>")
                dev_block = (
                    "<details style='margin-top:6px'>"
                    f"<summary class='meta' style='cursor:pointer'>"
                    f"查看激活设备（{len(devs)} 台）</summary>"
                    + "".join(lines) + "</details>")
            else:
                dev_bind = (r.get("device") or "").strip()
                if dev_bind:
                    dev_block = ("<div class='meta' style='margin-top:6px'>"
                                 f"绑定串：<span class='mono'>{html.escape(dev_bind)}"
                                 "</span>（发码时填的，尚未激活上报）</div>")
                else:
                    dev_block = ("<div class='meta' style='margin-top:6px;color:#999'>"
                                 "尚未激活（客户还没粘码激活，或网络不通没上报）</div>")
            items.append(f"""<div class="item">
<b>{html.escape(r.get('customer') or '（未填昵称）')}</b>
<span class="meta">　{r.get('tier_label','')}</span><br>
<span class="meta">{html.escape(r.get('contact',''))}　
{html.escape(r.get('channel',''))}　¥{html.escape(str(r.get('price','')))}　
{r.get('issued','')}</span><br>
<span class="mono">{ec}</span>
{''.join(tags)}
{dev_block}
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
        return self._send(200, page("查客户", body, nav_on(1)))

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
                f"</div>", nav_on(1)))

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
        # ★ 台数下拉必须**准确预选客户原来的台数**，否则升级会静默放宽限制。
        #
        #   事故（2026-10-17）：dan 原本是 ver=4「限 1 台」，升级 20000 后变成
        #   ver=2「不限台数」—— 防护反而变松。修好预选 + 兜底：
        #   如果老记录读不到台数，**宁可沿用 1 台**，也不要默认"不限"。
        cur_md_n = int(cur_md) if cur_md else None
        md_choices = ((1, "1 台（默认·限单机）"), (2, "2 台"), (3, "3 台"),
                      (5, "5 台"), (10, "10 台"), (0, "不限台数"))
        # 老记录若台数为空，按 1 台预选（安全侧），而不是"不限"
        if cur_md_n is None:
            cur_md_n = 1
        md_opts = "".join(
            f"<option value='{v}'{' selected' if v == cur_md_n else ''}>{t}</option>"
            for v, t in md_choices)

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

        # ★ v2.1.9：实际激活的设备（来自上报库）
        _devs = _devices_of_code(code)
        if _devs:
            _rows = "".join(
                f"<div class='mono meta'>└ {html.escape(d['device_hash'])}　"
                f"{html.escape(str(d.get('first_seen',''))[:16])}　"
                f"v{html.escape(str(d.get('version','')))}"
                f"（上报 {d.get('seen_count',1)} 次）</div>"
                for d in _devs)
            _lim = cur_md or 0
            _warn = ""
            if _lim and len(_devs) > _lim:
                _warn = (f"<div class='err' style='margin-top:6px'>⚠ 已激活 "
                         f"{len(_devs)} 台，超过允许的 {_lim} 台</div>")
            _dev_html = (f"<div class='card'><h3>实际激活设备（{len(_devs)} 台）</h3>"
                         f"{_rows}{_warn}"
                         f"<p class='meta' style='margin-top:8px'>"
                         f"数据来自客户端激活上报。同一台电脑重复激活不会重复计数。</p></div>")
        else:
            _dev_html = ("<div class='card'><h3>实际激活设备</h3>"
                         "<p class='meta'>尚未激活 —— 客户还没粘码，"
                         "或者激活时网络不通没能上报。</p></div>")

        body = f"""
<div class="card"><h3>客户 · {html.escape(cur_name or '未填昵称')}</h3>
{vb}
<div class="ok" style="margin-bottom:12px">
原档位：<b>{html.escape(old.get('tier_label',''))}</b>　
原价格：¥{html.escape(str(old.get('price','')))}　
台数：{('不限' if not cur_md else str(cur_md)+' 台')}<br>
<span class="mono" style="font-size:12px">{html.escape(code)}</span>
</div>

<!-- ★ 实际激活设备（v2.1.9：打通上报库） -->
{_dev_html}

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
        return self._send(200, page("客户管理", body, nav_on(1)))

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

    def _api_revoked(self, q=None):
        """返回被作废的授权码清单（客户端用，无需登录）。

        支持 `?codes=A,B,C` 只返回这些码的状态（减少传输量）。
        不传 codes 则返回全量名单（便于排查）。

        ★ 方案 B（2026-10-26）：客户要求「作废后立刻停止」。
          原来客户端纯本地 HMAC 校验，作废只改台账，客户端完全不知道。

        安全边界：
          · 只返回 code + 减少信息量的 reason
          · **不含**客户名、价格、渠道、备注
          · 作废码本身就是一串无意义字符，泄露无害
          · 不限速也无所谓（数据量小、无敏感信息）

        客户端行为（见 sphdl/revocation.py）：
          · 启动 / 每 30 分钟拉一次
          · 命中 → 立即降档 + 停正在下载的任务 + 醒目提示
          · 服务器不可达 → 用本地缓存（7 天宽限），再不行就放行
        """
        try:
            rows = self._merged_rows()
        except Exception as e:                      # noqa: BLE001
            return self._send_json({"ok": False, "error": str(e),
                                    "revoked": []})
        out = []
        for r in rows:
            if not r.get("_voided"):
                continue
            c = (r.get("code") or "").strip()
            if not c:
                continue
            out.append({
                "code": c,
                # 只给一个粗粒度原因，不带客户信息
                "reason": "授权码已作废",
            })
        # 客户端可以只问自己关心的码（省钱省流量）
        want = ""
        try:
            want = ((q or {}).get("codes", [""])[0] or "").strip()
        except Exception:
            want = ""
        if want:
            asked = {c.strip() for c in want.split(",") if c.strip()}
            out = [it for it in out if it["code"] in asked]
        return self._send_json({
            "ok": True,
            "revoked": out,
            "count": len(out),
            "ts": int(time.time()),
            # 客户端用来判断这份名单有多新
            "ledger_mtime": int(os.path.getmtime(LEDGER)) if LEDGER.exists() else 0,
        })

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
            dev = (old.get("device") or "").strip()   # 绑定版沿用原绑定
            # ★ 台数兜底（2026-10-17 修）：前端没传时**沿用旧码的台数**，
            #   绝不静默变成"不限"。事故：dan 限 1 台升级后变不限台数。
            if md_raw == "":
                md = old.get("max_devices")
                md = int(md) if md else None
            else:
                md = int(md_raw)
                if md == 0:
                    md = None                        # 0 = 用户显式选了"不限"
            new_code = make_code(tier, product=prod,
                                 device=dev if dev else "",
                                 max_devices=None if dev else md)
        except Exception as e:                              # noqa: BLE001
            return self._send(200, page("改档位", f"<div class='card'>"
                f"<div class='err'>生成失败：{html.escape(str(e))}</div>"
                f"<a href='/customers'><button class='gray'>返回</button></a>"
                f"</div>", nav_on(1)))

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
    # ---------- 话术库（v2.2.3） ----------
    def _page_snippets(self, q):
        d = snippets_load()
        ok = q.get("ok", [""])[0]
        flash = f"<div class='card'><div class='ok'>✅ {html.escape(ok)}</div></div>" if ok else ""

        # 版本列表（带删除）
        items = ""
        for i, v in enumerate(d["versions"]):
            chan = v.get("channel") or ""
            # ★ tag 先算好，不能写在 f-string 里（反斜杠会触发 SyntaxError）
            tag_html = ('<span class="tag">' + html.escape(chan) + '</span>'
                        if chan else "")
            ver_esc = html.escape(v["ver"])
            chan_esc = html.escape(chan)
            del_js = ("document.getElementById('dv').value='" + ver_esc
                      + "';document.getElementById('dc').value='" + chan_esc
                      + "';document.getElementById('df').submit()")
            # 更新要点：手填的优先，否则从 CHANGELOG 抽
            nt = v.get("notes") or changelog_notes(v["ver"])
            if nt:
                notes_html = ("<details style='margin-top:6px'>"
                              "<summary style='cursor:pointer;color:#0f766e;"
                              "font-size:12.5px'>本版更新要点（"
                              + ("手填" if v.get("notes") else "CHANGELOG")
                              + "）</summary>"
                              "<div style='white-space:pre-wrap;font-size:12px;"
                              "color:#475569;background:#f8fafc;border-radius:7px;"
                              "padding:8px 10px;margin-top:4px;"
                              "max-height:200px;overflow:auto'>"
                              + html.escape(nt) + "</div></details>")
            else:
                notes_html = ("<div style='margin-top:6px;font-size:12px;"
                              "color:#94a3b8'>（暂无更新要点，可手填或同步 CHANGELOG）</div>")
            items += (
                "<div class='ver-item'>"
                "<b>v" + ver_esc + "</b>" + tag_html
                + "<span class='lk'>"
                + html.escape(v['filename'] or '(未填文件名)') + "</span>"
                + "<span class='lk'>"
                + html.escape(v['link'] or '(未填链接)')
                + ('  提取码 ' + html.escape(v['pwd']) if v['pwd'] else "")
                + "</span>"
                # ★ v2.2.4：本版更新要点（折叠显示，避免列表太长）
                + notes_html
                + "<div style='margin-top:8px;display:flex;gap:8px'>"
                + "<button type='button' class='del' onclick=\"" + del_js + "\">"
                + "删除</button></div></div>"
            )
        if not items:
            items = "<div class='warn'>还没有任何版本，先在下面添加一个。</div>"

        body = f"""
<div class="card"><h3>① 套路词（每次发码都会带上）</h3>
<form method="post" action="/snippets_save">
<input type="hidden" name="act" value="save_template">
<textarea name="template" rows="3" style="width:100%;padding:11px 12px;
  border:1px solid #d1d5db;border-radius:9px;font-size:15px;
  font-family:inherit;line-height:1.6">{html.escape(d['template'])}</textarea>
<p class="meta">例如：请您查收，满意的话帮忙给个红花，遇到问题的话联系我。</p>
<button>保存套路词</button>
</form></div>

<div class="card"><h3>② 已保存的版本（{len(d['versions'])} 个）</h3>
{items}
</div>

<div class="card"><h3>③ 添加 / 更新版本（粘贴即自动解析）</h3>
<form method="post" action="/snippets_save" id="vf">
<input type="hidden" name="act" value="save_version">
<label>把网盘生成的分享文字整段粘进来 ↓（<b>百度网盘 / 夸克网盘</b> 都支持）</label>
<textarea id="raw" name="raw" rows="4" placeholder="【百度】通过网盘分享的文件：微信视频号下载器-v2.1.8-win64-portable.zip
链接: https://pan.baidu.com/s/1sAqh5NZiMPGdceG4z_TEPA?pwd=bfcr 提取码: bfcr

【夸克】我用夸克网盘给你分享了「微信视频号下载器-v2.2.2-win64-portable.zip」
链接：https://pan.quark.cn/s/4402de3baafd?pwd=xsSS
提取码：xsSS"
 oninput="autoParse()" style="width:100%;padding:11px 12px;
  border:1px solid #d1d5db;border-radius:9px;font-size:13.5px;
  font-family:Consolas,monospace;line-height:1.7"></textarea>
<p class="meta" id="pmsg">粘进来后下面 4 栏会自动填好，确认无误再点保存。</p>

<label>版本号 *（自动识别，可改）</label>
<input name="ver" id="f_ver" required placeholder="2.1.8">
<label>网盘文件名（自动识别，可改）</label>
<input name="filename" id="f_fn" placeholder="微信视频号下载器-v2.1.8-win64-portable.zip">
<label>分享链接 *（自动识别，可改）</label>
<input name="link" id="f_lk" placeholder="https://pan.baidu.com/s/1xxxx?pwd=abcd">
<label>提取码（自动识别，可改）</label>
<input name="pwd" id="f_pw" placeholder="bfcr">
<label>本版更新要点（留空 = 自动从 CHANGELOG 提取）</label>
<textarea name="notes" id="f_nt" rows="4" placeholder="留空会自动从 CHANGELOG.md 里这个版本的段落提取；
也可以自己改写成人话，比如：
· 修好了公众号视频下载失败的问题
· 解析时会显示进度，不会以为卡死"
 style="width:100%;padding:11px 12px;border:1px solid #d1d5db;
  border-radius:9px;font-size:13.5px;font-family:inherit;line-height:1.7"></textarea>
<button>保存这个版本</button>
</form>
<p class="meta" style="margin-top:8px">
同一版本号会覆盖旧的；链接里已带 <code>?pwd=</code> 时提取码可留空。</p>
</div>

<form method="post" action="/snippets_save" id="df" style="display:none">
<input type="hidden" name="act" value="del_version">
<input type="hidden" name="ver" id="dv">
<input type="hidden" name="channel" id="dc">
</form>

{JS_PASTE}
"""
        return self._send(200, page("话术库", body, nav_on(3), flash))

    def _do_snippets_save(self, form):
        act = (form.get("act") or "").strip()
        d = snippets_load()
        try:
            if act == "save_template":
                tpl = (form.get("template") or "").strip()
                d["template"] = tpl or DEFAULT_TEMPLATE
                snippets_save(d)
                return self._redirect("/snippets", None,
                                      ok="套路词已保存")
            if act == "save_version":
                # ★ v2.2.3：若前端没填全但粘贴了原始分享文字 → 服务端再解析一次兜底
                raw_text = (form.get("raw") or "").strip()
                ver = (form.get("ver") or "").strip().lstrip("vV")
                fn = (form.get("filename") or "").strip()
                link = (form.get("link") or "").strip()
                pwd = (form.get("pwd") or "").strip()
                if raw_text and (not ver or not link):
                    parsed = parse_share_text(raw_text)
                    ver = ver or parsed["ver"]
                    fn = fn or parsed["filename"]
                    link = link or parsed["link"]
                    pwd = pwd or parsed["pwd"]
                if not ver:
                    raise ValueError("版本号不能为空（可粘贴分享文字自动识别）")
                notes = (form.get("notes") or "").strip()
                if not notes:
                    # 没手填就自动从 CHANGELOG 抽（用户不用重复劳动）
                    notes = changelog_notes(ver)
                item = {"ver": ver, "filename": fn, "link": link,
                        "pwd": pwd, "notes": notes}
                if not item["link"]:
                    raise ValueError("分享链接不能为空（可粘贴分享文字自动识别）")
                # ★ 按「版本号 + 网盘渠道」去重：
                #   同一版本可以在百度、夸克各存一份（客户可能只认某一个），
                #   同版本同渠道才覆盖。
                chan = channel_of(link)
                item["channel"] = chan
                d["versions"] = [
                    v for v in d["versions"]
                    if not (v.get("ver") == ver
                            and (v.get("channel") or channel_of(v.get("link", ""))) == chan)
                ]
                d["versions"].append(item)
                snippets_save(d)
                return self._redirect("/snippets", None,
                                      ok=f"v{ver}（{chan}）已保存")
            if act == "del_version":
                ver = (form.get("ver") or "").strip()
                chan = (form.get("channel") or "").strip()
                if chan:
                    d["versions"] = [
                        v for v in d["versions"]
                        if not (v["ver"] == ver
                                and (v.get("channel") or channel_of(v.get("link", ""))) == chan)
                    ]
                else:
                    d["versions"] = [v for v in d["versions"] if v["ver"] != ver]
                snippets_save(d)
                return self._redirect("/snippets", None,
                                      ok=f"v{ver}{('（' + chan + '）') if chan else ''} 已删除")
        except Exception as e:                              # noqa: BLE001
            return self._send(200, page("话术库", f"<div class='card'>"
                f"<div class='err'>保存失败：{html.escape(str(e))}</div>"
                f"<a href='/snippets'><button class='gray'>返回</button></a>"
                f"</div>", nav_on(3)))
        return self._redirect("/snippets", None)

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
            return self._send(200, page("转卖追踪", body, nav_on(2)))

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
        return self._send(200, page("转卖追踪", body, nav_on(2)))


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
