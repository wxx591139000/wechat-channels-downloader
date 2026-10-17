#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""档位隔离测试（v2.0.19）：公众号视频不占用视频号档位名额。

背景：客户质疑「50 个视频是不是抓少了」。实测那篇文章只有 3 个视频，
      但由此发现一个真实隐患：公众号视频原先与视频号共用 RecordStore
      的 max_records 上限，轻量版（500 条）客户若解析大量公众号视频，
      会把视频号的额度吃光，导致视频号再也抓不进来 —— 而公众号是
      **不分档的独立产品**，不该受此限制。

覆盖：
  C1 is_full 只统计视频号记录
  C2 公众号记录再多也不触发满档
  C3 下调档位时只裁视频号，公众号一条不动
  C4 满档时视频号被拒、公众号仍可入库
  C5 老数据（无 platform）按视频号计数（保持 v2.0.17 行为）

跑法：python tests/verify_quota_isolation.py
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


import tkinter as tk  # noqa: E402
rt = tk.Tk(); rt.withdraw(); rt.destroy()

import sphdl.gui as G  # noqa: E402


def make_store(path, mx):
    return G.RecordStore(path, max_records=mx)


def mk(i, plat):
    return G.Record(id=f"r{i}", title=f"{plat}{i}", url=f"http://x/{i}",
                    decode_key="", size=100, source="测试",
                    created=f"2026-10-17 10:00:{i:02d}",
                    platform=(plat if plat else None) or "视频号",
                    video_id=f"v{i}")


tmp = Path(tempfile.mkdtemp(prefix="quota_"))

# ---------- C1 ----------
print("=" * 74)
print("C1 is_full 只统计视频号记录")
print("=" * 74)
st = make_store(tmp / "a.json", mx=5)
for i in range(5):
    st.add(mk(i, "视频号"))
check("C1a 5 条视频号 → 满", st.is_full() is True)
for i in range(10):
    st.add(mk(100 + i, "公众号"))
check("C1b ★加 10 条公众号后…仍算满（视频号已满）", st.is_full() is True)
check("C1c 总条数 = 15", len(st.items) == 15, str(len(st.items)))

# ---------- C2 ----------
print("\n" + "=" * 74)
print("C2 ★公众号再多也不触发满档")
print("=" * 74)
st2 = make_store(tmp / "b.json", mx=5)
for i in range(50):
    st2.add(mk(i, "公众号"))
check("C2a 50 条公众号 → 未满", st2.is_full() is False, str(len(st2.items)))
check("C2b 仍能继续加公众号",
      st2.add(mk(999, "公众号")) is True)
# 视频号还能正常加 5 条
okn = sum(1 for i in range(7) if st2.add(mk(500 + i, "视频号")))
check("C2c ★视频号仍能加满 5 条（未被公众号占额）", okn == 5, f"成功 {okn} 条")
check("C2d 现在算满", st2.is_full() is True)

# ---------- C3 ----------
print("\n" + "=" * 74)
print("C3 ★下调档位只裁视频号，公众号一条不动")
print("=" * 74)
st3 = make_store(tmp / "c.json", mx=10)
for i in range(10):
    st3.add(mk(i, "视频号"))
for i in range(6):
    st3.add(mk(100 + i, "公众号"))
check("C3a 初始 10 视频号 + 6 公众号",
      len(st3.items) == 16, str(len(st3.items)))
removed = st3.set_max_records(3)
n_v = sum(1 for r in st3.items.values() if r.platform == "视频号")
n_m = sum(1 for r in st3.items.values() if r.platform == "公众号")
check("C3b 裁掉 7 条（10-3）", len(removed) == 7, f"实际 {len(removed)}")
check("C3c ★只剩 3 条视频号", n_v == 3, str(n_v))
check("C3d ★公众号 6 条全保住", n_m == 6, str(n_m))
check("C3e 被裁的都是视频号",
      all(r.platform == "视频号" for r in removed),
      str(set(r.platform for r in removed)))

# ---------- C4 ----------
print("\n" + "=" * 74)
print("C4 满档时：视频号被拒、公众号仍可入库")
print("=" * 74)
st4 = make_store(tmp / "d.json", mx=2)
st4.add(mk(1, "视频号"))
st4.add(mk(2, "视频号"))
check("C4a 视频号已满", st4.is_full() is True)
check("C4b ★视频号再加被拒", st4.add(mk(3, "视频号")) is False)
check("C4c ★公众号仍能入库", st4.add(mk(4, "公众号")) is True)
check("C4d 总条数 3", len(st4.items) == 3, str(len(st4.items)))

# ---------- C5 老数据兼容 ----------
print("\n" + "=" * 74)
print("C5 老数据（无 platform 字段）按视频号计数")
print("=" * 74)
p5 = tmp / "e.json"
old = [{"id": f"o{i}", "title": f"老{i}", "url": f"http://o/{i}",
        "decode_key": "", "size": 1, "source": "API直嗅",
        "created": "2026-10-16 09:00:00", "status": "waiting",
        "file": "", "error": "", "progress": 0, "is_image": False,
        "video_id": f"ov{i}", "renditions": [], "quality_note": ""}
       for i in range(5)]
p5.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
st5 = make_store(p5, mx=5)
check("C5a 老数据 5 条加载", len(st5.items) == 5, str(len(st5.items)))
check("C5b ★老数据按视频号算 → 满", st5.is_full() is True)
check("C5c 老数据 platform 默认为视频号",
      all(r.platform == "视频号" for r in st5.items.values()))

shutil.rmtree(tmp, ignore_errors=True)

print()
ok = sum(1 for _, p, _ in RESULTS if p)
print(f"RESULT: {'ALL PASS' if ok == len(RESULTS) else 'FAIL'} ({ok}/{len(RESULTS)})")
for n, p, _ in RESULTS:
    if not p:
        print("  未通过: " + n)
sys.exit(0 if ok == len(RESULTS) else 1)
