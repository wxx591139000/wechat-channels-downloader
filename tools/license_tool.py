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


def cmd_gen(args):
    tier = TIERS.get(str(args.tier).lower())
    if tier is None:
        log(f"❌ 未知档位: {args.tier}（可选 500/1000/2000/20000）")
        return 1
    if not lic.license_available():
        log("❌ 未找到校验密钥，先生成: python tools/license_tool.py init")
        return 1
    if args.note:
        log(f"# 客户/备注: {args.note}")
    label = lic.label_of_tier(tier)
    codes = []
    for _ in range(max(1, args.count)):
        code = lic.make_code(tier, days=args.days)
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
                     note=args.note, channel=args.channel)
        p = L.ledger_path()
        log(f"📒 已登记台账 {len(codes)} 条 → {p}")
        if not args.customer and not args.note:
            log("   ⚠️ 未填 --customer/--note：建议补上，否则出事查不到是谁")
    log(f"（档位: {label}｜有效期: "
        f"{'永久' if not args.days else str(args.days) + ' 天自首次激活'}）")
    return 0


def cmd_ledger(args):
    """台账查询：list / find / device / stats。"""
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
    p.add_argument("--device", default="", help="设备码（ver=2 绑定版用，暂未启用）")
    p.add_argument("--no-ledger", action="store_true",
                   help="不登记台账（默认登记；仅测试时用）")
    p.set_defaults(func=cmd_gen)

    p = sub.add_parser("ledger", help="查台账（追溯谁泄露了码）")
    p.add_argument("action", nargs="?", default="list",
                   choices=["list", "find", "device", "stats"],
                   help="list=列表（默认）/ find=按码反查 / "
                        "device=按设备码查 / stats=统计")
    p.add_argument("value", nargs="?", default="",
                   help="find/device 时的查询值（授权码或设备码）")
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
