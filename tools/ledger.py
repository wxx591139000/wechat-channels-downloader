#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发码台账（作者专用，绝不随包分发）——「码 → 客户」追溯。

为什么需要（2026-10-16 用户场景：闲鱼卖给陌生人）
------------------------------------------------
设备绑定防的是「码被复用」；但如果是「整个 zip + 码被转发」，
绑定拦不住，此时唯一的追责手段就是**台账**：

  市面上出现某码 → 反查是谁泄露的 → 闲鱼举报 / 停止售后
  同一设备码被多个"客户"申请 → 识别批量转卖 → 拒绝 + 拉黑
  有人冒充老客户要换绑 → 查台账确认身份

一句话：**绑定是「锁」，台账是「眼睛」。只有锁没眼睛，转卖者换个
说法你也不知道；有眼睛没锁，你知道是谁卖的但拦不住。两个一起才完整。**

存储
----
`licenses.jsonl`（append-only，每行一条 JSON）：
  默认放 `E:\\myHermesWorkSpace\\_archives\\keys\\licenses.jsonl`
  （与密钥备份同目录，方便统一保管；**仓库外 → 天然不入库**）

⚠️ 含客户联系方式（PII）：绝不入库、绝不放进交付包、绝不随软件分发。
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# 默认台账路径（与密钥备份同目录）。可用环境变量 SPHDL_LEDGER 覆盖。
DEFAULT_LEDGER = Path(r"E:\myHermesWorkSpace\_archives\keys\licenses.jsonl")


def ledger_path() -> Path:
    env = os.environ.get("SPHDL_LEDGER")
    return Path(env) if env else DEFAULT_LEDGER


def append(code: str, tier: int, tier_label: str, days: int = 0,
           customer: str = "", contact: str = "", device: str = "",
           price: str = "", note: str = "", channel: str = "",
           path: Path = None) -> bool:
    """追加一条发码记录。返回是否写入成功。

    失败**不抛异常**（不能因为台账写不了就发不出码），但会打印告警，
    避免"以为记了其实没记"。
    """
    p = path or ledger_path()
    rec = {
        "code": code,
        "tier": tier,
        "tier_label": tier_label,
        "days": days,                       # 0 = 永久
        "customer": customer,
        "contact": contact,
        "device": device,                   # ver=2 绑定版才有；ver=1 为空
        "price": price,
        "note": note,
        "channel": channel,                 # 如「闲鱼」「微信」
        "issued": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except Exception as e:                   # noqa: BLE001
        print(f"⚠️  台账写入失败（授权码仍然有效，请手工补录）：{e}",
              file=sys.stderr, flush=True)
        print(f"    应记录：{json.dumps(rec, ensure_ascii=False)}",
              file=sys.stderr, flush=True)
        return False


def load(path: Path = None) -> list:
    """读取全部台账记录（坏行跳过并告警）。"""
    p = path or ledger_path()
    if not p.exists():
        return []
    out = []
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:                    # noqa: BLE001
            print(f"⚠️  台账第 {i} 行无法解析，已跳过", file=sys.stderr)
    return out


def find_by_code(code: str, path: Path = None) -> list:
    """按授权码反查（用于「市面上出现某码」的溯源）。

    支持容错匹配：去掉横杠、统一大写后比较。
    """
    norm = (code or "").replace("-", "").upper()
    return [r for r in load(path)
            if (r.get("code") or "").replace("-", "").upper() == norm]


def find_by_device(device: str, path: Path = None) -> list:
    """按设备码反查（用于识别「同一设备被多个客户申请」的批量转卖）。"""
    norm = (device or "").replace("-", "").upper()
    if not norm:
        return []
    return [r for r in load(path)
            if (r.get("device") or "").replace("-", "").upper() == norm]


def stats(path: Path = None) -> dict:
    """统计：总数、各档位分布、各渠道分布。"""
    recs = load(path)
    st = {"total": len(recs), "by_tier": {}, "by_channel": {},
          "by_date": {}, "devices": {}}
    for r in recs:
        lb = r.get("tier_label") or str(r.get("tier", "?"))
        st["by_tier"][lb] = st["by_tier"].get(lb, 0) + 1
        ch = r.get("channel") or "(未填)"
        st["by_channel"][ch] = st["by_channel"].get(ch, 0) + 1
        d = (r.get("issued") or "")[:10]
        if d:
            st["by_date"][d] = st["by_date"].get(d, 0) + 1
        dv = r.get("device")
        if dv:
            st["devices"][dv] = st["devices"].get(dv, 0) + 1
    return st
