"""★ 事故回归：parser.py 里 _pick 被重复定义，导致视频解析全废（v2.2.13）。

═══ 客户报障（2026-10-23）═══
「打开嗅探后，抓取不到视频清单」

═══ 根因 ═══
加评论功能时，我在 parser.py 末尾写了：

    def _pick(d: dict, keys):        # ← 单参数版本
        ...

而文件第 88 行早已有：

    def _pick(mapping: dict, *names):  # ← 可变参数版本

**Python 后定义的覆盖前面的**，于是所有 `_pick(m, "url", "urlToken")`
调用抛 TypeError → 外层 try/except 吞掉 → 返回 None
→ **视频解析 100% 失败，一条都抓不到**。

这类 bug 极隐蔽：不报错、不崩溃，只是静默返回 None。

═══ 本测试守什么 ═══
  P1 parser.py 里模块级函数名不得重复
  P2 _pick 必须是可变参数版本（能接多个名字）
  P3 真实形状的 objectDesc 能解出视频
  P4 评论提取仍正常（不能被修复带坏）
  P5 其他模块也不许出现同名重复定义（addon / gui 重点）
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


def module_level_funcs(path: Path):
    """返回模块级函数名列表（含重复项）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]


print("=" * 74)
print("P1/P5 模块级函数名不得重复")
print("=" * 74)
for fname in ("parser.py", "addon.py", "userdata.py", "pipeline.py", "config.py"):
    p = ROOT / "sphdl" / fname
    if not p.exists():
        continue
    names = module_level_funcs(p)
    dupes = sorted({n for n in names if names.count(n) > 1})
    print(f"  {fname:14s} 函数 {len(names):3d} 个  重复: {dupes or '无'}")
    check(f"P1/P5 {fname} 无重复定义", not dupes, str(dupes))

# gui.py 里方法重名（类内）也查
print()
gui = ROOT / "sphdl" / "gui.py"
tree = ast.parse(gui.read_text(encoding="utf-8"))
bad_classes = {}
for node in tree.body:
    if isinstance(node, ast.ClassDef):
        ms = [n.name for n in node.body if isinstance(n, ast.FunctionDef)]
        d = sorted({n for n in ms if ms.count(n) > 1})
        if d:
            bad_classes[node.name] = d
print(f"  gui.py 类内方法重复: {bad_classes or '无'}")
check("P5b gui.py 类内无重复方法", not bad_classes, str(bad_classes))

print()
print("=" * 74)
print("P2 _pick 必须是可变参数版本")
print("=" * 74)
src = (ROOT / "sphdl" / "parser.py").read_text(encoding="utf-8")
tree = ast.parse(src)
pick_node = None
for n in tree.body:
    if isinstance(n, ast.FunctionDef) and n.name == "_pick":
        pick_node = n
        break
ok = False
if pick_node is not None:
    args = pick_node.args
    ok = args.vararg is not None      # *names
    print(f"  _pick 参数: {[a.arg for a in args.args]} + *{args.vararg.arg if args.vararg else '无'}")
check("P2 _pick 带 *args（可变参数）", ok,
      "缺 *names 会导致多字段调用 TypeError")

# 实测能接多个名字
from sphdl.parser import _pick
try:
    v = _pick({"urlToken": "T"}, "url", "urlToken", "url_token")
    print(f"  实测 _pick({{'urlToken':'T'}}, 'url','urlToken','url_token') → {v!r}")
    check("P2b 能接多个候选名", v == "T", repr(v))
except Exception as e:
    check("P2b 能接多个候选名", False, f"{type(e).__name__}: {e}")

print()
print("=" * 74)
print("P3 真实形状 objectDesc 能解出视频（事故核心）")
print("=" * 74)
from sphdl.parser import extract_from_object_desc

OD = {
    "description": "标题X",
    "media": [{"url": "http://finder.video.qq.com/251/20302/stodownload?k=1",
               "decodeKey": "0123456789abcdef0123456789abcdef",
               "fileSize": 2048}],
}
vd = extract_from_object_desc(OD)
print(f"  结果: {'✅ 解出' if vd else '❌ None'}")
check("P3 objectDesc 能解出视频", vd is not None and vd.url,
      str(vd)[:70] if vd else "None")

# 多个 media（多清晰度）也要能解
OD2 = {"description": "标题Y", "media": [
    {"url": "http://x/1.mp4", "fileSize": 100},
    {"url": "http://x/2.mp4", "fileSize": 200},
]}
vd2 = extract_from_object_desc(OD2)
check("P3b 多 media 也能解",
      vd2 is not None and len(getattr(vd2, "renditions", [])) >= 2,
      str(len(getattr(vd2, "renditions", [])) if vd2 else 0))

print()
print("=" * 74)
print("P4 评论提取仍正常（修复没带坏）")
print("=" * 74)
from sphdl.parser import extract_comments
c, t = extract_comments({"commentList": [
    {"nickname": "A", "content": "评论1", "likeCount": 3}], "totalCount": 9})
print(f"  评论 {len(c)} 条 total={t}")
check("P4 评论提取正常", len(c) == 1 and c[0]["nick"] == "A", str(c))

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
