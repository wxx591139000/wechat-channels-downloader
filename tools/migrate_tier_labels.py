#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""台账档位名迁移：旧名 → v2.1.5 定稿新名（2026-10-19）。

用户要求：
  「旧的档位名称已经废弃了，要维护的是新档位名称」

★★ 关键：**按 tier 数值映射，不按名字匹配**

  语义坑（改错就全乱了）：
    「旗舰版（2 万条）」tier=20000  →  新名是「至尊」
    「专业版（2000 条）」tier=2000  →  新名是「旗舰」
    「旗舰」这个词从 20000 **降到了 2000**！
  如果按名字替换（旗舰版→旗舰），2 万条的码会变成「旗舰（2000 条）」，全错。

映射表（唯一事实源 = sphdl/config.py 的 MAX_RECORDS_LABELS）：
    500   → 轻量（500 条）
    1000  → 标准（1000 条）
    2000  → 旗舰（2000 条）
    20000 → 至尊（2 万条）
    0     → 公众号视频版（不分档）  ← 产品名，不是档位名，保持不变

用法：
    python3 migrate_tier_labels.py            # 预演（只打印，不改）
    python3 migrate_tier_labels.py --apply    # 真正写入（自动备份）
"""
import json
import shutil
import sys
import time
from pathlib import Path

LEDGER = Path("/opt/sphdl-admin/data/licenses.jsonl")

# ★ 按 tier 数值 → 新档位名（不要按旧名匹配）
TIER_TO_LABEL = {
    500: "轻量（500 条）",
    1000: "标准（1000 条）",
    2000: "旗舰（2000 条）",
    20000: "至尊（2 万条）",
    0: "公众号视频版（不分档）",
}

def main():
    apply = "--apply" in sys.argv
    if not LEDGER.exists():
        print(f"❌ 台账不存在: {LEDGER}")
        return 1

    lines = LEDGER.read_text(encoding="utf-8").splitlines()
    out, changes, skipped = [], [], []

    for ln in lines:
        ln_s = ln.strip()
        if not ln_s:
            continue
        try:
            r = json.loads(ln_s)
        except Exception:
            out.append(ln)
            skipped.append(("解析失败", ln_s[:40]))
            continue

        # 事件行（customer_edit / voided / restored / superseded）不动
        if r.get("event"):
            out.append(ln_s)
            continue

        old_label = r.get("tier_label") or ""
        tier = r.get("tier")

        # 只处理视频号档位（有明确 tier 数值且在新档位表里）
        if tier is None or int(tier) not in TIER_TO_LABEL:
            out.append(ln_s)
            continue

        new_label = TIER_TO_LABEL[int(tier)]
        if old_label == new_label:
            out.append(ln_s)          # 已经是新名，不动
            continue

        # 需要迁移
        changes.append({
            "code": (r.get("code") or "")[:28],
            "customer": (r.get("customer") or "")[:14],
            "tier": tier,
            "old": old_label,
            "new": new_label,
        })
        if apply:
            r["tier_label"] = new_label
            r["tier_label_migrated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            out.append(json.dumps(r, ensure_ascii=False))
        else:
            out.append(ln_s)

    print("=" * 74)
    print(f"台账档位名迁移{'（预演，不写入）' if not apply else '（实际写入）'}")
    print("=" * 74)
    print(f"总行数: {len(lines)}  需迁移: {len(changes)}  跳过: {len(skipped)}")
    print()
    if changes:
        print(f"{'tier':>6}  {'旧名':<18} → {'新名'}")
        print("-" * 74)
        seen = set()
        for c in changes:
            key = (c["tier"], c["old"])
            if key in seen:
                continue
            seen.add(key)
            print(f"{c['tier']:>6}  {c['old']:<18} → {c['new']}")
        print()
        print("逐个明细：")
        for c in changes:
            print(f"  {c['customer']:<16} tier={c['tier']:>5}  "
                  f"{c['old']:<18} → {c['new']}")
    else:
        print("✅ 无需迁移（已经是新档位名）")

    if apply and changes:
        bak = LEDGER.with_suffix(f".jsonl.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(LEDGER, bak)
        LEDGER.write_text("\n".join(out) + "\n", encoding="utf-8")
        print()
        print(f"✅ 已写入 {LEDGER}")
        print(f"✅ 备份 {bak}")
    elif not apply and changes:
        print()
        print("→ 这是预演。确认无误后加 --apply 真正写入")

    return 0


if __name__ == "__main__":
    sys.exit(main())
