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
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # sphdl/
sys.path.insert(0, str(ROOT))

from sphdl import license as lic                        # noqa: E402

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
    for _ in range(max(1, args.count)):
        log(lic.make_code(tier, days=args.days))
    log(f"（档位: {lic.label_of_tier(tier)}｜有效期: "
        f"{'永久' if not args.days else str(args.days) + ' 天自首次激活'}）")
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
    p.add_argument("--note", default="", help="备注（客户名，仅打印）")
    p.set_defaults(func=cmd_gen)

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
