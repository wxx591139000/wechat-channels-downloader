#!/usr/bin/env python3
"""授权码工具（**作者专用，绝不随客户包分发**）。

用法（在 sphdl/ 下，用 venv 的 python 运行）：

    python tools/license_tool.py init                # 生成授权密钥（本地 + _lic_key.py）
    python tools/license_tool.py secret              # 显示/备份当前密钥（hex）
    python tools/license_tool.py gen 500             # 生成「轻量版 500 条」档授权码
    python tools/license_tool.py gen 1000 --days 365 # 一年期「标准版 1000 条」
    python tools/license_tool.py gen 20000           # 「旗舰版 2 万条」
    python tools/license_tool.py gen 2000 --count 5 --note "客户A"   # 批量 5 个
    python tools/license_tool.py verify SPHDL-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX
    python tools/license_tool.py selftest            # 往返/篡改/容错自检

发码并登记台账（★推荐，作者侧追溯，见 tools/ledger.py 与本目录文档）
    python tools/license_tool.py gen 20000 \
        --customer "闲鱼昵称" --contact "闲鱼:xxx888" \
        --channel "闲鱼" --price "199" --note "旗舰版永久"
    # 台账文件：E:\myHermesWorkSpace\_archives\keys\licenses.jsonl（仓库外，不入库）

查台账（追溯谁泄露了码）
    python tools/license_tool.py ledger list      # 全部记录
    python tools/license_tool.py ledger stats     # 统计（含重复设备码告警）
    python tools/license_tool.py ledger find  SPHDL-XXXX-...   # ★按码反查是谁
    python tools/license_tool.py ledger device  A3F8-...      # 按设备码查

密钥与分发约定
  · 密钥 = 仓库根 `.license_secret`（32 字节 hex，**已 .gitignore，绝不入库**）
  · 构建时 release.py 把密钥混淆后注入 `sphdl/_lic_key.py`（同样不入库），
    构建结束自动清理；客户包内只有校验用密钥，**无法自己生成码**
  · 源码仓库公开也不影响安全：没有 `.license_secret` 就产不出有效码
  · 备份提醒：`.license_secret` 丢了 → 已发出的码在**新构建**里将全部失效
    （旧包不受影响），请务必备份到密码管理器/U盘

档位参数（v2.0.17 定稿：4 档全部确定数值，**没有「不限量」**）
  500    = 轻量版
  1000   = 标准版
  2000   = 专业版
  20000  = 旗舰版（2 万条，经实测的容量上限）
"""
import argparse
import json
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # sphdl/
sys.path.insert(0, str(ROOT))

from sphdl import license as lic                        # noqa: E402
sys.path.insert(0, str(ROOT / "tools"))
import ledger as L                                      # noqa: E402

SECRET_FILE = ROOT / ".license_secret"
KEY_FILE = ROOT / "sphdl" / "_lic_key.py"
TIERS = {"500": 500, "1000": 1000, "2000": 2000, "20000": 20000,
         "2万": 20000, "旗舰": 20000, "旗舰版": 20000,
         "轻量版": 500, "标准版": 1000, "专业版": 2000}


def log(msg):
    print(msg, flush=True)


def cmd_init(args):
    if SECRET_FILE.exists() and not args.force:
        log(f"ℹ️  密钥已存在：{SECRET_FILE}（要重新生成请加 --force，"
            "注意：换密钥会让已发出的授权码全部失效）")
        return
    secret = secrets.token_bytes(32)
    SECRET_FILE.write_text(secret.hex(), encoding="utf-8")
    KEY_FILE.write_text(_key_module(secret), encoding="utf-8")
    log(f"✅ 已生成授权密钥：{SECRET_FILE}")
    log(f"✅ 已写入校验密钥：{KEY_FILE}（不入库，release.py 会重新注入）")
    log(f"🔑 密钥（hex，请备份到密码管理器/U盘）：\n   {secret.hex()}")
    log("⚠️  丢失密钥 = 无法再生成可用授权码（已发出的码在新构建里全部失效）")


def _key_module(secret: bytes) -> str:
    blob = lic.obfuscate(secret)
    body = ", ".join(str(b) for b in blob)
    return ('"""构建期注入的授权校验密钥（不入库；由 tools/license_tool.py init '
            '或 release.py 生成）。"""\n\n'
            f"K = bytes([{body}])\n")


def cmd_secret(args):
    if not SECRET_FILE.exists():
        log(f"❌ 未找到 {SECRET_FILE}，先运行: python tools/license_tool.py init")
        return 1
    log(f"密钥文件: {SECRET_FILE}")
    log(f"密钥(hex): {SECRET_FILE.read_text(encoding='utf-8').strip()}")
    log(f"内置校验密钥: {KEY_FILE} {'存在' if KEY_FILE.exists() else '缺失'}"
        f"（{'一致' if KEY_FILE.exists() and _same() else '需重新 init'}）")
    return 0


def _same() -> bool:
    try:
        from sphdl import _lic_key
        return lic._unobfuscate(_lic_key.K) == bytes.fromhex(
            SECRET_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        return False


def lib_caps_txt(caps: int) -> str:
    """能力位转中文（v2.6.0）。"""
    try:
        from sphdl import license as _L
        names = []
        for bit, name in _L.CAP_LABELS.items():
            if caps & bit:
                names.append(name)
        return "、".join(names) or f"未知({caps})"
    except Exception:
        return str(caps)


def cmd_gen(args):
    # 先解析产品线（v2.0.19：视频号 4 档 / 公众号 1 个不分档）
    flags = 0
    for f in (getattr(args, "feature", None) or []):
        k = f.strip().lower()
        if k in ("mp", "mpvideo", "公众号", "公众号视频", "article"):
            flags = lic.PRODUCT_MP_ARTICLE
        elif k in ("vc", "channel", "视频号"):
            flags = flags or lic.PRODUCT_VIDEO
        elif k in ("all", "全部"):
            log("❌ v2.0.19 起不再有「视频号+公众号」组合码：两类产品分开卖，"
                "请分别用 --feature vc / --feature mp 发两个码")
            return 1
        else:
            log(f"❌ 未知功能: {f}（可选 vc/视频号、mp/公众号）")
            return 1

    is_mp = (flags == lic.PRODUCT_MP_ARTICLE)

    if is_mp:
        # 公众号产品不分档：档位参数可以是 0 / mp / 任意值，统一按 0 处理
        tier = 0
        label = "公众号视频版（不分档）"
    else:
        tier = TIERS.get(str(args.tier).lower())
        if tier is None:
            log(f"❌ 未知档位: {args.tier}（可选 500/1000/2000/20000）")
            return 1
        label = lic.label_of_tier(tier)

    if not lic.license_available():
        log("❌ 未找到校验密钥，先生成: python tools/license_tool.py init")
        return 1
    if args.note:
        log(f"# 客户/备注: {args.note}")

    # ★ v2.6.0：能力位（增值功能）—— like=点赞增强包
    #   ★ 必须在 for 循环**外面**算一次（放循环里会破坏循环体缩进 —— 踩过）
    caps = 0
    _CAP_MAP = {"like": lic.CAP_LIKE_STATS}
    for c in (getattr(args, "caps", None) or []):
        key = str(c).strip().lower()
        if key not in _CAP_MAP:
            raise SystemExit(f"未知能力位: {c}（可选 {sorted(_CAP_MAP)}）")
        caps |= _CAP_MAP[key]

    codes = []
    for _ in range(max(1, args.count)):
        code = lic.make_code(tier, days=args.days, product=flags,
                             device=(args.device or ""),
                             max_devices=args.max_devices,
                             caps=caps)
        codes.append(code)
        log(code)
    # 台账登记（作者侧追溯，见 tools/ledger.py）
    if args.no_ledger:
        log("（--no-ledger：本次未登记台账）")
    else:
        for code in codes:
            L.append(code, tier, label, days=args.days,
                     customer=args.customer, contact=args.contact,
                     device=args.device, price=args.price,
                     note=args.note, channel=args.channel,
                     features=flags, max_devices=args.max_devices)
        p = L.ledger_path()
        log(f"📒 已登记台账 {len(codes)} 条 → {p}")
        if not args.customer and not args.note:
            log("   ⚠️ 未填 --customer/--note：建议补上，否则出事查不到是谁")
    feat_txt = lic.PRODUCT_LABELS.get(flags, "视频号下载")
    # ★ v2.0.19：码型说明要准确反映实际生成的码（ver=1/2/3/4）
    if caps:
        ver_txt = (f"ver=5（含能力位：{lib_caps_txt(caps)}）")
    elif args.device:
        ver_txt = "ver=3（死绑单机）"
    elif args.max_devices is not None:
        ver_txt = (f"ver=4（限 {args.max_devices} 台）"
                   if args.max_devices > 0 else "ver=4（不限台数）")
    elif flags:
        ver_txt = "ver=2（不限台数）"
    else:
        ver_txt = "ver=1（老码型）"
    log(f"（档位: {label}｜功能: {feat_txt}｜有效期: "
        f"{'永久' if not args.days else str(args.days) + ' 天自首次激活'}"
        f"｜码型: {ver_txt}）")
    return 0


def _vps_query(path: str, base: str, token: str, timeout: float = 12.0):
    """向 VPS 授权服务查询（作者侧）。失败返回 (None, 错误说明)。

    ★ 关于 User-Agent：域名走 Cloudflare 隧道，Cloudflare 默认会拦
      非浏览器 UA（返回 `403 error code: 1010` —— 这是 Cloudflare 的
      Browser Integrity Check，请求根本没到 VPS）。
      这里带一个明确的自家 UA 并附 Accept 头，让请求被当作正常 API 调用；
      如果 Cloudflare 侧仍然拦，需要在 CF 后台为该路径配 WAF 跳过规则。
    """
    import urllib.error
    import urllib.request
    url = base.rstrip("/") + path
    headers = {
        "X-Auth": token,
        "User-Agent": "sphdl-track/1.0 (author tool)",
        "Accept": "application/json",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")), ""
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        if e.code == 403 and "1010" in body:
            return None, ("被 Cloudflare 拦截（error code 1010）—— 请求未到 VPS。\n"
                          "   解决：Cloudflare 后台 → 安全性 → WAF → 为 "
                          "/api/suspect 与 /api/stats 加跳过规则；\n"
                          "   或临时用 SSH 隧道查询：\n"
                          "     ssh -N -L 3010:127.0.0.1:3010 vps-aliyun &\n"
                          "     python tools/license_tool.py ledger track "
                          "--vps http://127.0.0.1:3010")
        return None, f"HTTP {e.code} {body}"
    except Exception as e:                              # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def _code_hash(code: str) -> str:
    """授权码哈希（必须与服务端/客户端算法一致：strip+upper 后 sha256 前16）。"""
    import hashlib
    return hashlib.sha256((code or "").strip().upper().encode("utf-8")
                          ).hexdigest()[:16]


def _cmd_track(args):
    """★ 盗版追踪：把 VPS 上报数据与本地台账关联，输出可执行报告。

    用法：
        python tools/license_tool.py ledger track
        python tools/license_tool.py ledger track --token <管理令牌>

    令牌也可用环境变量 SPHDL_ADMIN_TOKEN 提供，或放在
    ~/.sphdl_admin_token（本机私密文件）。

    报告内容：
        · 每个已发出的码 → 出现在几台设备上（>1 = 疑似转卖）
        · 每台设备 → 激活过几个码（>1 = 有人在收集码）
        · 前者会带上台账里的客户信息，能直接找到是谁流出去的
    """
    base = args.vps or "https://license.zhuanlu.xyz"
    token = args.token or os.environ.get("SPHDL_ADMIN_TOKEN", "")
    if not token:
        tf = Path.home() / ".sphdl_admin_token"
        if tf.exists():
            token = tf.read_text(encoding="utf-8").strip()
    if not token:
        log("❌ 需要 VPS 管理令牌。三种提供方式（任选其一）：")
        log("   ① --token <令牌>")
        log("   ② 环境变量 SPHDL_ADMIN_TOKEN")
        log(f"   ③ 写入文件 {Path.home() / '.sphdl_admin_token'}")
        log("")
        log("   令牌在 VPS 上：cat /opt/sphdl-license/.admin_token")
        return 1

    log(f"🔍 查询 {base} …")
    sus, err = _vps_query("/api/suspect", base, token)
    if sus is None:
        log(f"❌ 查询失败：{err}")
        log("   检查：① VPS 服务在跑（systemctl is-active sphdl-license）")
        log("         ② 令牌是否正确  ③ 网络能否访问该域名")
        return 1
    stats, err2 = _vps_query("/api/stats", base, token)

    # ---- 台账建索引：code_hash → 台账记录 ----
    ledger_rows = L.load()
    by_hash = {}
    for r in ledger_rows:
        code = r.get("code") or ""
        if code:
            by_hash[_code_hash(code)] = r

    one_code = sus.get("one_code_many_devices") or []
    one_dev = sus.get("one_device_many_codes") or []

    print()
    print("=" * 78)
    print("授权激活追踪报告")
    print("=" * 78)
    if stats:
        print(f"上报总数 {stats.get('total_reports', 0)} 条 "
              f"｜不同授权码 {stats.get('distinct_codes', 0)} 个 "
              f"｜不同设备 {stats.get('distinct_devices', 0)} 台 "
              f"｜最后上报 {stats.get('last_report') or '无'}")
    print(f"台账记录 {len(ledger_rows)} 条（其中可匹配哈希 {len(by_hash)} 条）")
    print()

    # ---------- ① 一码多机：最需要关注的转卖信号 ----------
    print("【① 一码多机 —— 强转卖信号】")
    if not one_code:
        print("  ✅ 没有发现。所有授权码都只在一台设备上激活过。")
    else:
        for item in one_code:
            h = item.get("code_hash", "")
            n = item.get("devices", 0)
            rec = by_hash.get(h)
            print(f"  ⚠️  {h}  出现在 {n} 台设备上")
            if rec:
                print(f"      档位: {rec.get('tier_label', '?')}")
                print(f"      客户: {rec.get('customer') or '（未填）'}")
                print(f"      联系: {rec.get('contact') or '（未填）'}")
                print(f"      渠道: {rec.get('channel') or '（未填）'} "
                      f"｜价格: {rec.get('price') or '（未填）'}")
                print(f"      发码: {rec.get('issued', '?')}")
                print(f"      原码: {rec.get('code', '')}")
            else:
                print(f"      ⚠ 台账里没有这个哈希 → 该码不是本机发出的，"
                      f"或台账不全")
            print(f"      首次 {item.get('first_seen')}  "
                  f"｜最后 {item.get('last_seen')}")
            print()

    # ---------- ② 一机多码：有人在收集码 ----------
    print("【② 一机多码 —— 有人在收集/测试授权码】")
    if not one_dev:
        print("  ✅ 没有发现。")
    else:
        for item in one_dev:
            h = item.get("device_hash", "")
            n = item.get("codes", 0)
            print(f"  ⚠️  设备 {h} 激活过 {n} 个不同的码")
            codes = (item.get("code_hashes") or "").split(",")
            for ch in codes[:12]:
                ch = ch.strip()
                if not ch:
                    continue
                rec = by_hash.get(ch)
                who = (f"{rec.get('customer') or '未填'} / "
                       f"{rec.get('tier_label', '')}") if rec else "（台账无此码）"
                print(f"        {ch}  {who}")
            if len(codes) > 12:
                print(f"        …还有 {len(codes) - 12} 个")
            print()

    # ---------- ③ 已发出但从未激活的码 ----------
    print("【③ 已发出但从未激活的码】")
    reported = {(_code_hash(r.get("code", "")))
                for r in ledger_rows if r.get("code")}
    if stats:
        # 用一机多码/一码多机里出现的哈希反推；更准确的做法是服务端提供全量
        seen = set()
        for item in one_code:
            seen.add(item.get("code_hash", ""))
        for item in one_dev:
            for ch in (item.get("code_hashes") or "").split(","):
                if ch.strip():
                    seen.add(ch.strip())
        missing = [h for h in reported if h and h not in seen]
        if missing:
            for h in missing:
                rec = by_hash.get(h) or {}
                print(f"  · {rec.get('code', '?')[:26]}…  "
                      f"{rec.get('tier_label', '')[:16]:18s} "
                      f"{rec.get('customer') or '（未填）'}")
            print(f"  （共 {len(missing)} 个：可能是还没激活，或客户还没用）")
        else:
            print("  ✅ 台账里的码都已有激活记录。")
    print()

    print("=" * 78)
    print("处置建议")
    print("=" * 78)
    if one_code:
        print("  · 上表「一码多机」的客户 → 私聊确认是否换了电脑/重装系统")
        print("  · 若非本人换机 → 该码已外流，可停止后续售后支持")
        print("  · 保留本报告作为证据（闲鱼投诉/维权时用）")
    if one_dev:
        print("  · 「一机多码」多为测试或批量试用；若客户身份明确且非测试，")
        print("    提防是同行在收集你的授权码")
    if not one_code and not one_dev:
        print("  · 当前无异常，保持关注即可。")
    print()
    return 0


def cmd_ledger(args):
    """台账查询：list / find / device / stats / track。"""
    if args.action == "track":
        return _cmd_track(args)

    if args.action == "stats":
        st = L.stats()
        log(f"📒 台账: {L.ledger_path()}")
        log(f"总计 {st['total']} 条")
        if st["by_tier"]:
            log("  档位分布: " + "  ".join(
                f"{k}×{v}" for k, v in sorted(st["by_tier"].items())))
        if st["by_channel"]:
            log("  渠道分布: " + "  ".join(
                f"{k}×{v}" for k, v in sorted(st["by_channel"].items())))
        if st["by_date"]:
            log("  按日期: " + "  ".join(
                f"{k}×{v}" for k, v in sorted(st["by_date"].items())))
        dup = {d: n for d, n in st["devices"].items() if n > 1}
        if dup:
            log(f"  ⚠️ 重复设备码（同机多码，疑似转卖）：{dup}")
        return 0

    if args.action == "find":
        recs = L.find_by_code(args.value)
        if not recs:
            log(f"❌ 台账中未找到该码: {args.value}")
            log("   （可能是：① 不是本机发出的码 ② 台账被改过 ③ 码抄错了）")
            return 1
        for r in recs:
            log(json.dumps(r, ensure_ascii=False, indent=2))
        return 0

    if args.action == "device":
        recs = L.find_by_device(args.value)
        if not recs:
            log(f"未找到该设备码的记录: {args.value}")
            return 1
        if len(recs) > 1:
            log(f"⚠️ 该设备码有 {len(recs)} 条发码记录（同机多码，"
                "疑似转卖或有人冒用）：")
        for r in recs:
            log(json.dumps(r, ensure_ascii=False, indent=2))
        return 0

    # list
    recs = L.load()
    if not recs:
        log(f"📒 台账为空或不存在: {L.ledger_path()}")
        return 0
    log(f"📒 台账共 {len(recs)} 条（{L.ledger_path()}）")
    for r in recs:
        log(f"  {r.get('issued','?'):19s} | {r.get('tier_label','?'):8s} | "
            f"{r.get('code','?'):38s} | {r.get('customer') or '(未填)'}"
            f"{' | ' + r['device'] if r.get('device') else ''}"
            f"{' | ' + r['channel'] if r.get('channel') else ''}")
    return 0


def cmd_verify(args):
    info = lic.verify_code(args.code)
    if not info:
        log("❌ 无效授权码")
        return 1
    log(f"✅ 有效｜档位: {lic.label_of_tier(info['tier'])}｜"
        f"有效期: {'永久' if not info['days'] else str(info['days']) + ' 天自首次激活'}")
    return 0


def cmd_selftest(args):
    ok = True
    for name, passed, detail in lic.selftest():
        log(f"{'[PASS]' if passed else '[FAIL]'} {name} {detail}")
        ok = ok and passed
    log(f"RESULT: {'ALL PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="sphdl 授权码工具（作者专用）")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("init", help="生成授权密钥")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("secret", help="显示/核对密钥")
    p.set_defaults(func=cmd_secret)

    p = sub.add_parser("gen", help="生成授权码")
    p.add_argument("tier", help="500 / 1000 / 2000 / 20000（旗舰版）")
    p.add_argument("--days", type=int, default=0, help="有效天数（0=永久，粒度 8 天）")
    p.add_argument("--count", type=int, default=1, help="生成个数")
    p.add_argument("--note", default="", help="备注（仅打印 + 记台账）")
    # ---- 台账字段（作者侧追溯，见 tools/ledger.py）----
    p.add_argument("--customer", default="", help="客户名/昵称（记台账）")
    p.add_argument("--contact", default="", help="联系方式，如「闲鱼:abc123」「微信:zs」")
    p.add_argument("--channel", default="", help="销售渠道，如「闲鱼」「微信」")
    p.add_argument("--price", default="", help="成交价（记台账，便于统计）")
    p.add_argument("--device", default="",
                   help="★设备码（如 4770-0AEB-18B6-1AB4）或绑定串（8位如 163145d5）"
                        "→ 生成设备绑定码（ver=3，只能在该设备激活）")
    p.add_argument("--max-devices", type=int, default=None,
                   help="★允许激活的电脑台数（如 3）→ 生成限台数码（ver=4）。"
                        "靠激活时的服务器校验执行；不填=不限台数")
    p.add_argument("--no-ledger", action="store_true",
                   help="不登记台账（默认登记；仅测试时用）")
    p.add_argument("--feature", action="append", metavar="F",
                   help="功能位（可多次）：vc=视频号 / mp=公众号文章视频 / "
                        "all=两者。★不传则生成 ver=1 老码（仅视频号）")
    p.add_argument("--caps", action="append", metavar="C",
                   help="★v2.6.0 增值能力位（可多次）：like=点赞增强包。"
                        "传了就生成 ver=5 码，可与视频号码叠加使用")
    p.set_defaults(func=cmd_gen)

    p = sub.add_parser("ledger", help="查台账（追溯谁泄露了码）")
    p.add_argument("action", nargs="?", default="list",
                   choices=["list", "find", "device", "stats", "track"],
                   help="list=全部 find=按码查 device=按设备码查 "
                        "stats=统计 track=★VPS 追踪报告（查转卖）")
    p.add_argument("--value", default="",
                   help="find/device 的查询值（授权码或设备码）")
    p.add_argument("--token", default="",
                   help="track 用的 VPS 管理令牌（也可用环境变量 "
                        "SPHDL_ADMIN_TOKEN 或 ~/.sphdl_admin_token）")
    p.add_argument("--vps", default="",
                   help="track 用的服务地址（默认 https://license.zhuanlu.xyz）")
    p.set_defaults(func=cmd_ledger)

    p = sub.add_parser("verify", help="校验授权码")
    p.add_argument("code")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("selftest", help="自检")
    p.set_defaults(func=cmd_selftest)

    args = ap.parse_args()
    if not getattr(args, "func", None):
        ap.print_help()
        return 1
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
