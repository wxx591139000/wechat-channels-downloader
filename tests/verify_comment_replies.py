"""嵌套回复 + 评论计数 + 导出（v2.2.14）。

═══ 客户反馈（2026-10-23）═══
1. 「发现评论总数比视频号评论少（比如"天降熊熊"的评论 24 个 VS 69 个）」
2. 「视频的评论是以什么形式下载保存」
3. 「希望打开的评论区跟视频号评论区同款，特别是有些是多个粉丝间的相互对答」

═══ 根因 ═══
旧实现 `extract_comments` 选「条数最多的那一层」当结果，
而粉丝互相对答在数据里是**每条评论下面的嵌套列表**（replyList 等），
被当成独立列表丢掉了 → 只拿到主评论，回复全丢，
总数自然对不上（24 vs 69）。

═══ 修法 ═══
  · `_parse_comment_node` 递归解析嵌套回复，挂到父评论 `replies`
  · `_walk_comment_roots` 只把最浅一层当根（更深的属于回复，避免重复计数）
  · `count_tree` 统计含全部回复的条数
  · 界面递归渲染：回复缩进 + 左侧竖线 + 浅色头像
  · 加「💾 导出评论」，支持 .txt（带缩进）和 .json（完整树）

覆盖：
  R1 一层嵌套回复能解析出来
  R2 多层嵌套（A→B→C）
  R3 count_tree 统计含回复的总数
  R4 ★不再把回复列表当根（避免重复计数）
  R5 主评论顺序不乱
  R6 拍平文本带缩进（导出/复制用）
  R7 导出 JSON 结构完整
  R8 导出 TXT 含回复
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


from sphdl.parser import extract_comments, count_tree

PAYLOAD = {
    "data": {
        "commentList": [
            {
                "nickname": "粉丝甲", "content": "主评论一", "likeCount": 30,
                "replyList": [
                    {"nickname": "粉丝乙", "content": "我同意甲的说法",
                     "likeCount": 5,
                     "replyList": [
                         {"nickname": "粉丝甲", "content": "谢谢乙",
                          "likeCount": 1},
                     ]},
                    {"nickname": "粉丝丙", "content": "补充一句",
                     "likeCount": 2},
                ],
            },
            {
                "nickname": "粉丝丁", "content": "主评论二", "likeCount": 8,
                "replyList": [
                    {"nickname": "粉丝戊", "content": "丁说得对"},
                ],
            },
            {"nickname": "粉丝己", "content": "主评论三（无回复）"},
        ],
        "totalCount": 69,
    }
}

print("=" * 74)
print("R1/R2 嵌套回复解析")
print("=" * 74)
c, t = extract_comments(PAYLOAD)
print(f"  根评论 {len(c)} 条，total={t}，含回复共 {count_tree(c)} 条")
for x in c:
    print(f"  · {x['nick']}：{x['content']}  （回复 {len(x.get('replies') or [])} 条）")
    for y in x.get("replies") or []:
        print(f"      └ {y['nick']}：{y['content']}  （回复 {len(y.get('replies') or [])} 条）")
        for z in y.get("replies") or []:
            print(f"          └ {z['nick']}：{z['content']}")

check("R1a 根评论 3 条", len(c) == 3, str(len(c)))
check("R1b 第一条有 2 条回复",
      len(c[0].get("replies") or []) == 2,
      str(len(c[0].get("replies") or [])))
check("R2 回复里还有回复（嵌套 2 层）",
      len((c[0]["replies"][0].get("replies") or [])) == 1,
      str(len((c[0]["replies"][0].get("replies") or []))))

print()
print("=" * 74)
print("R3/R4 计数正确、不重复")
print("=" * 74)
total_nodes = count_tree(c)
print(f"  含回复总条数 = {total_nodes}（应为 3+3+1 = 7）")
check("R3 count_tree 统计正确", total_nodes == 7, str(total_nodes))
check("R4 total 取到 69", t == 69, str(t))
# 确认没有把回复列表当根重复计
root_names = [x["nick"] for x in c]
print(f"  根评论昵称: {root_names}")
check("R4b 根只有 3 个（回复没被当根）", len(root_names) == 3, str(root_names))

print()
print("=" * 74)
print("R5 顺序不乱")
print("=" * 74)
check("R5a 第一条是「粉丝甲」", c[0]["nick"] == "粉丝甲", c[0]["nick"])
check("R5b 第二条是「粉丝丁」", c[1]["nick"] == "粉丝丁", c[1]["nick"])
check("R5c 回复顺序保持", c[0]["replies"][0]["nick"] == "粉丝乙",
      c[0]["replies"][0]["nick"])

print()
print("=" * 74)
print("R6 拍平文本（导出/复制用）")
print("=" * 74)


def _flatten(nodes, depth=0, out=None):
    if out is None:
        out = []
    for cc in nodes or []:
        if not isinstance(cc, dict):
            continue
        prefix = ("  " * depth) + ("└ " if depth else "")
        line = f"{prefix}{cc.get('nick','')}：{cc.get('content','')}"
        extra = []
        if cc.get("likes"):
            extra.append(f"👍{cc['likes']}")
        if extra:
            line += "（" + " ".join(extra) + "）"
        out.append(line)
        _flatten(cc.get("replies"), depth + 1, out)
    return out


flat = _flatten(c)
for ln in flat:
    print("  " + ln)
check("R6a 拍平后 7 行", len(flat) == 7, str(len(flat)))
check("R6b 回复有缩进前缀", any("└ " in x for x in flat), "")
check("R6c 两层缩进用 4 空格",
      any(x.startswith("    └ ") for x in flat), "")

print()
print("=" * 74)
print("R7/R8 导出格式")
print("=" * 74)
tdir = Path(tempfile.mkdtemp(prefix="exp_"))
# JSON
jdata = {"video_title": "测试视频", "comment_total": t,
         "captured": len(flat), "comments": c}
jp = tdir / "评论.json"
jp.write_text(json.dumps(jdata, ensure_ascii=False, indent=2), encoding="utf-8")
back = json.loads(jp.read_text(encoding="utf-8"))
print(f"  JSON: {jp.stat().st_size} 字节，comments 顶层 {len(back['comments'])} 条")
check("R7a JSON 可读回", len(back["comments"]) == 3)
check("R7b JSON 保留了嵌套",
      len(back["comments"][0].get("replies") or []) == 2,
      str(len(back["comments"][0].get("replies") or [])))

# TXT
tp = tdir / "评论.txt"
lines = ["视频：测试视频", f"评论总数：{t}（已捕获 {len(flat)} 条）",
         "-" * 50, ""] + flat
tp.write_text("\n".join(lines), encoding="utf-8")
txt = tp.read_text(encoding="utf-8")
print(f"  TXT: {tp.stat().st_size} 字节")
print("  " + txt.split("\n")[3])
check("R8a TXT 含总条数", "69" in txt)
check("R8b TXT 含嵌套回复", "我同意甲的说法" in txt and "└" in txt)

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
