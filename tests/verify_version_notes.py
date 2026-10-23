"""版本更新要点（update notes）测试 — v2.2.4。

用户需求：
  「可以在发码平台里显示各个版本相比前一版本的主要升级内容不」

实现：
  · snippets.json 每个版本加 notes 字段（可手填，留空则从 CHANGELOG 自动提取）
  · changelog_notes(ver)：解析 CHANGELOG.md 里 `## [x.y.z]` 段落
  · version_notes(ver)：手填优先 → CHANGELOG 兜底
  · 管理页：每个版本可折叠查看要点 + 表单可编辑
  · 发码页：选中版本后显示要点 + 「复制更新说明」按钮

覆盖：
  N1 CHANGELOG 解析：能取出指定版本段落
  N2 解析结果去掉了 markdown 装饰（**、`、#、-）
  N3 不存在的版本返回空
  N4 版本号带 v 前缀也能取
  N5 不串版本（2.2.2 的内容不会混进 2.2.3）
  N6 手填 notes 优先于 CHANGELOG
  N7 snippets.json 能存取 notes 字段
  N8 页面含「更新要点」块与复制按钮
  N9 CHANGELOG 文件缺失时不炸
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


import sphdl_admin as A

# 隔离到临时目录，用真实 CHANGELOG 内容
tdir = Path(tempfile.mkdtemp(prefix="notes_"))
A.SNIPPETS = tdir / "snippets.json"
A.CHANGELOG = tdir / "CHANGELOG.md"
src = ROOT / "CHANGELOG.md"
A.CHANGELOG.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

print("=" * 76)
print("N1/N2 CHANGELOG 解析")
print("=" * 76)
n223 = A.changelog_notes("2.2.3")
print(f"  2.2.3: {len(n223)} 字符, {len(n223.splitlines())} 行")
for ln in n223.splitlines()[:5]:
    print("   ", ln[:66])
check("N1a 2.2.3 能解析出内容", len(n223) > 100, f"{len(n223)} 字符")
check("N1b 含该版关键词（Referer）", "Referer" in n223)
check("N1c 含403", "403" in n223)

check("N2a 没残留 ** 加粗", "**" not in n223, n223[:30])
check("N2b 没残留反引号", "`" not in n223)
check("N2c 没残留 ### 标题井号", "###" not in n223)

print()
print("=" * 76)
print("N3-N5 边界")
print("=" * 76)
empty = A.changelog_notes("9.9.9")
print(f"  不存在的 9.9.9 → {empty!r}")
check("N3 不存在的版本返回空", empty == "")

nv = A.changelog_notes("v2.2.3")
print(f"  带 v 前缀 v2.2.3 → {len(nv)} 字符")
check("N4 带 v 前缀也能取", len(nv) == len(n223), f"{len(nv)} vs {len(n223)}")

n222 = A.changelog_notes("2.2.2")
print(f"  2.2.2: {len(n222)} 字符")
check("N5a 2.2.2 有自己的内容", len(n222) > 50, f"{len(n222)} 字符")
check("N5b 2.2.2 不含 2.2.3 的 Referer 内容",
      "Referer" not in n222, "串版本了" if "Referer" in n222 else "")
check("N5c 2.2.2 含自己的关键词（序号）",
      "序号" in n222, n222[:40])

print()
print("=" * 76)
print("N6/N7 手填优先 + 存取")
print("=" * 76)
d = A.snippets_load()
d["versions"].append({
    "ver": "2.2.3", "channel": "百度",
    "filename": "微信视频号下载器-v2.2.3-win64-portable.zip",
    "link": "https://pan.baidu.com/s/x?pwd=aa", "pwd": "aa",
    "notes": "手写的要点：修好了公众号下载",
})
A.snippets_save(d)

v = A.version_notes("2.2.3")
print(f"  version_notes('2.2.3') → {v!r}")
check("N6a 手填 notes 优先", v == "手写的要点：修好了公众号下载", v[:30])
# ★ 2.2.2 的内容是「序号重置」，不是 Referer（Referer 是 2.2.3 的）
_n222 = A.version_notes("2.2.2")
check("N6b 不在库里也回退 CHANGELOG（取到 2.2.2 自己的内容）",
      "序号" in _n222 and "Referer" not in _n222, _n222[:36])

d2 = A.snippets_load()
got = [x for x in d2["versions"] if x["ver"] == "2.2.3"]
print(f"  读回 versions 里的 notes: {got[0].get('notes')!r}")
check("N7a notes 持久化", got and got[0].get("notes") == "手写的要点：修好了公众号下载")
check("N7b 没填 notes 的版本读回空串",
      all("notes" in x for x in d2["versions"]), "字段缺失")

print()
print("=" * 76)
print("N8 页面元素")
print("=" * 76)
src_txt = (ROOT / "server" / "sphdl_admin.py").read_text(encoding="utf-8")
for kw, desc in (
    ("notesBox", "发码页更新要点折叠块"),
    ("copyNotes()", "复制更新说明按钮"),
    ("notesUpd()", "切换版本刷新要点"),
    ("const NOTES=", "NOTES 数据注入"),
    ('name="notes"', "管理页可编辑 notes"),
    ("本版更新要点", "管理页列表显示要点"),
    ("changelog_notes", "CHANGELOG 解析函数"),
):
    ok = kw in src_txt
    print(f"  {'✅' if ok else '❌'} {desc}")
    check(f"N8 {desc}", ok)

print()
print("=" * 76)
print("N9 CHANGELOG 缺失不炸")
print("=" * 76)
A.CHANGELOG = tdir / "nonexistent.md"
try:
    r = A.changelog_notes("2.2.3")
    print(f"  文件不存在 → {r!r}")
    check("N9a 文件缺失返回空不炸", r == "")
    v = A.version_notes("2.2.3")
    check("N9b version_notes 也能容错", isinstance(v, str), repr(v))
except Exception as e:
    check("N9a 文件缺失返回空不炸", False, str(e))

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
