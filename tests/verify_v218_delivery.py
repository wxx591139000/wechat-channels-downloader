#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2.0.18 交付前综合验证：修复点 + 文档一致性 + 密钥纪律。

与文档口径分离 —— 全部从**源码/实际行为**取证。
跑法（在 sphdl/ 下）：python tests/verify_v218_delivery.py
"""
import sys
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


def read(p):
    return Path(p).read_text(encoding="utf-8", errors="replace")


addon = read(ROOT / "sphdl" / "addon.py")
gui = read(ROOT / "sphdl" / "gui.py")
usage = read(ROOT / "usage_instructions_v20.txt")
changelog = read(ROOT / "CHANGELOG.md")
init = read(ROOT / "sphdl" / "__init__.py")
pitfalls = read(REPO / "docs" / "PITFALLS.md")

# ---------- 1. 版本号 ----------
print("=" * 74)
print("V 版本号一致性")
print("=" * 74)
check("V1 __init__.py = 2.0.18", '__version__ = "2.0.18"' in init)
check("V2 CHANGELOG 有 [2.0.18] 段", "## [2.0.18]" in changelog)
check("V3 CHANGELOG 保留 [2.0.17] 段（历史不丢）", "## [2.0.17]" in changelog)
v = re.search(r'__version__ = "([\d.]+)"', init).group(1)
check("V4 版本号格式合法", re.fullmatch(r"\d+\.\d+\.\d+", v) is not None,
      f"v={v}")

# ---------- 2. 故障一：seen 释放 ----------
print("\n" + "=" * 74)
print("F1 故障一修复：seen 释放链路完整")
print("=" * 74)
check("F1a addon 定义 release_seen", "def release_seen(self, video_ids):" in addon)
check("F1b addon 定义 clear_seen", "def clear_seen(self):" in addon)
check("F1c release_seen 用 _seen_lock 保护",
      re.search(r"def release_seen.*?with self\._seen_lock:", addon, re.S) is not None)
check("F1d release_seen 同时清 _dedup_logged",
      re.search(r"def release_seen.*?self\._dedup_logged\.discard", addon, re.S) is not None)
check("F1e clear_seen 清 seen + _dedup_logged",
      re.search(r"def clear_seen.*?self\.seen\.clear\(\).*?self\._dedup_logged\.clear\(\)",
                addon, re.S) is not None)
# GUI 三个调用点
check("F1f _remove_row 调用 release_seen（统一出口）",
      re.search(r"def _remove_row.*?self\.addon\.release_seen", gui, re.S) is not None)
check("F1g _drop_records_ui 调用 release_seen",
      re.search(r"def _drop_records_ui.*?self\.addon\.release_seen", gui, re.S) is not None)
check("F1h _del_all_records 调用 clear_seen",
      re.search(r"def _del_all_records.*?self\.addon\.clear_seen", gui, re.S) is not None)
check("F1i _remove_row 有 addon 存在性保护（启动期安全）",
      'getattr(self, "addon", None) is not None' in gui)
# 所有单条删除路径都汇聚到 _remove_row
for fn in ["_clear_done", "_clear_failed"]:
    body = re.search(rf"def {fn}.*?(?=\n    def )", gui, re.S)
    check(f"F1j {fn} 走 _remove_row",
          body is not None and "_remove_row(" in body.group(0))

# ---------- 3. 故障二：批量驱动 ----------
print("\n" + "=" * 74)
print("F2 故障二修复：批量驱动不再永久停摆")
print("=" * 74)
check("F2a 新增 resetProgress（失焦不清 done）",
      "function resetProgress() { rounds = 0; stable = 0; }" in addon)
check("F2b 失焦分支调用 resetProgress",
      re.search(r"if \(!on\) \{.*?resetProgress\(\);", addon, re.S) is not None)
check("F2c 到底后自动续跑（grew || waited > 20000）",
      "if (grew || waited > 20000)" in addon)
check("F2d done 记录 doneAt 时间戳", "doneAt = Date.now();" in addon)
check("F2e 到底阈值放宽到 stable >= 20", "stable >= 20" in addon)
check("F2f 旧的 stable >= 12 已移除", "stable >= 12" not in addon)
check("F2g 代理侧下发 grew 信号",
      '("r" if grew else "")' in addon)
check("F2h _batch_seen_count 初始化", "self._batch_seen_count = 0" in addon)
check("F2i 批量启动重置基线",
      "self.addon._batch_seen_count = self.addon.capture_count" in gui)
check("F2j 状态栏给出可操作提示", "点一下视频号窗口" in gui)

# ---------- 4. 回归修复 ----------
print("\n" + "=" * 74)
print("F3 回归修复：auto_download 分支存在")
print("=" * 74)
check("F3a _handle 内 if self.auto_download: 存在",
      re.search(r"def _handle.*?\n        if self\.auto_download:", addon, re.S)
      is not None)
# 确认它不在 return 之后（缩进层级正确）
m = re.search(r"( *)(if self\.auto_download:)\n( *)if self\.duplicate_check", addon)
check("F3b 该分支未被 return 遮挡（可达）",
      m is not None and len(m.group(1)) == 8,
      f"缩进={len(m.group(1)) if m else '?'}")
check("F3c pipeline.submit 在 _handle 内",
      re.search(r"def _handle.*?self\.pipeline\.submit\(vd\)", addon, re.S) is not None)

# ---------- 5. 使用说明 ----------
print("\n" + "=" * 74)
print("D 使用说明（客户可自助）")
print("=" * 74)
check("D1 有「清单不再增长」章节", "清单抓到几千条后不再增长" in usage)
check("D2 有「删记录后抓不到」章节", "删了记录之后再也抓不到" in usage)
check("D3 给出 3 种自助办法", usage.count("①") and "点一下视频号窗口" in usage)
check("D4 批量拉取段有失焦提示", "自动翻页需要视频号窗口保持选中状态" in usage)
check("D5 明确告知 v2.0.18 已修复", "v2.0.18 已修复" in usage)

# ---------- 6. 文档 ----------
print("\n" + "=" * 74)
print("DOC 文档沉淀")
print("=" * 74)
check("DOC1 PITFALLS 记录 patch 行尾符事故",
      "CRLF/LF 混排" in pitfalls)
check("DOC2 PITFALLS 记录两个故障的区分法",
      "不要看到\"清单不动\"就套同一个结论" in pitfalls)
check("DOC3 PITFALLS 记录 GBK 日志坑", "GBK" in pitfalls)
check("DOC4 故障二分析报告存在",
      (REPO / "docs" / "故障分析-清单停止增长-20261016.md").exists())
check("DOC5 故障一分析报告存在",
      (REPO / "docs" / "故障分析-清单不动-20261016.md").exists())

# ---------- 7. 密钥纪律（红线）----------
print("\n" + "=" * 74)
print("SEC 密钥纪律（红线）")
print("=" * 74)
check("SEC1 _lic_key.py 不存在（未入库/未残留）",
      not (ROOT / "sphdl" / "_lic_key.py").exists())
check("SEC2 .license_secret 未被 git 跟踪",
      subprocess.run(["git", "check-ignore", "-q", ".license_secret"],
                     cwd=ROOT).returncode == 0)
check("SEC3 _lic_key.py 未被 git 跟踪",
      subprocess.run(["git", "check-ignore", "-q", "sphdl/_lic_key.py"],
                     cwd=ROOT).returncode == 0)
st = subprocess.run(["git", "status", "--short"], cwd=ROOT,
                    capture_output=True, text=True).stdout
check("SEC4 暂存区无密钥/构建产物",
      "_lic_key" not in st and ".license_secret" not in st
      and "portable.zip" not in st)

print()
ok = sum(1 for _, p, _ in RESULTS if p)
print(f"RESULT: {'ALL PASS' if ok == len(RESULTS) else 'FAIL'} ({ok}/{len(RESULTS)})")
for n, p, _ in RESULTS:
    if not p:
        print("  未通过: " + n)
sys.exit(0 if ok == len(RESULTS) else 1)
