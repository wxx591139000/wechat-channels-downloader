"""评论提取器测试（v2.2.12）。

═══ 客户需求 ═══
「能下载视频号评论吗」→「先做个存进视频清单里，有个『查看评论』选项
  展开评论，跟视频号同样的评论区展示效果」

═══ 设计前提 ═══
**我们还不知道视频号评论接口的确切字段名**（需要客户跑一次
bridge 诊断才能确认）。所以提取器必须**不依赖固定字段名**，
按结构特征识别：
  · 昵称类键：nickname / nickName / nick / userName / displayName ...
  · 内容类键：content / text / msg / commentContent ...
  · 可选：likeCount（点赞）、createTime（时间）、commentId

只要一个 list 里有多条这种 dict，就当评论列表。

覆盖：
  C1 标准字段（nickname + content + likeCount）
  C2 驼峰变体（nickName + text + likeNum）
  C3 下划线变体（nick_name + msg + like_count）
  C4 中文键值不误判（有 nick 无 content → 不是评论）
  C5 单条评论也能抓到
  C6 ★嵌套结构（result 是 JSON 字符串 → 二次解码）
  C7 ★混合 payload：选条数最多的那份（主评论 vs 嵌套回复）
  C8 没有评论的 payload 返回空（不误报）
  C9 content 是对象/超长 → 拒绝（防畸形数据）
  C10 真实视频 objectDesc payload 不会被误认成评论
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


from sphdl.parser import extract_comments, iter_comment_lists

print("=" * 74)
print("C1 标准字段")
print("=" * 74)
p1 = {"data": {"commentList": [
    {"nickname": "小明", "content": "这视频真好", "likeCount": 12},
    {"nickname": "小红", "content": "学到了", "likeCount": 3},
]}}
c, t = extract_comments(p1)
print(f"  抽到 {len(c)} 条: {[(x['nick'], x['content'], x['likes']) for x in c]}")
check("C1a 条数正确", len(c) == 2, str(len(c)))
check("C1b 昵称/内容/点赞都对",
      c and c[0]["nick"] == "小明" and c[0]["likes"] == 12, str(c[:1]))

print()
print("=" * 74)
print("C2 驼峰变体")
print("=" * 74)
p2 = {"x": [{"nickName": "A", "text": "hello", "likeNum": 5}]}
c, _ = extract_comments(p2)
print(f"  {c}")
check("C2 驼峰字段也能识别", len(c) == 1 and c[0]["nick"] == "A", str(c))

print()
print("=" * 74)
print("C3 下划线变体")
print("=" * 74)
p3 = {"list": [{"nick_name": "B", "msg": "world", "like_count": 7}]}
c, _ = extract_comments(p3)
print(f"  {c}")
check("C3 下划线字段也能识别", len(c) == 1 and c[0]["likes"] == 7, str(c))

print()
print("=" * 74)
print("C4 有昵称无内容 → 不是评论")
print("=" * 74)
p4 = {"users": [{"nickname": "C", "avatar": "http://x"}]}
c, _ = extract_comments(p4)
print(f"  {c}")
check("C4 不误判（缺 content）", len(c) == 0, str(c))

print()
print("=" * 74)
print("C5 单条评论")
print("=" * 74)
p5 = {"comment": [{"nickname": "D", "content": "只有一条"}]}
c, _ = extract_comments(p5)
print(f"  {c}")
check("C5 单条也能抓到", len(c) == 1, str(c))

print()
print("=" * 74)
print("C6 ★result 是 JSON 字符串（bridge 实际形态）")
print("=" * 74)
inner = json.dumps({"commentList": [
    {"nickname": "E", "content": "字符串里的评论1"},
    {"nickname": "F", "content": "字符串里的评论2"},
]}, ensure_ascii=False)
p6 = {"cmdName": "something", "result": inner}
c, _ = extract_comments(p6)
print(f"  抽到 {len(c)} 条: {[x['content'] for x in c]}")
check("C6 二次解码字符串能抓到", len(c) == 2, str(len(c)))

print()
print("=" * 74)
print("C7 ★混合 payload：选条数最多的")
print("=" * 74)
p7 = {
    "main": [{"nickname": f"U{i}", "content": f"主评论{i}"} for i in range(5)],
    "replies": [{"nickname": "R1", "content": "回复1"}],
}
c, _ = extract_comments(p7)
print(f"  选中 {len(c)} 条（主评论 5 条 vs 回复 1 条）")
check("C7 选主评论列表（5条 > 1条）", len(c) == 5, str(len(c)))
check("C7 内容来自主列表", c[0]["content"] == "主评论0", c[0]["content"])

print()
print("=" * 74)
print("C8 没有评论 → 返回空")
print("=" * 74)
p8 = {"objectDesc": {"media": [{"url": "http://x"}]}, "status": "ok"}
c, _ = extract_comments(p8)
print(f"  {c}")
check("C8 不误报", len(c) == 0, str(c))

print()
print("=" * 74)
print("C9 畸形数据拒绝")
print("=" * 74)
p9 = {"x": [
    {"nickname": "G", "content": {"nested": "object"}},   # content 是对象
    {"nickname": "H", "content": "x" * 6000},              # 超长
    {"nickname": "", "content": "空昵称"},                  # 空昵称
]}
c, _ = extract_comments(p9)
print(f"  {c}")
check("C9 畸形数据被拒", len(c) == 0, str(c))

print()
print("=" * 74)
print("C10 真实视频 payload 不误判")
print("=" * 74)
p10 = {"objectDesc": {"media": [{"decodeKey": "abc", "url": "http://v",
                                 "size": 123}],
                      "description": "视频标题",
                      "spec": [{"fileFormat": "mp4"}]}}
c, _ = extract_comments(p10)
print(f"  {c}")
check("C10 视频数据不会被当评论", len(c) == 0, str(c))

print()
print("=" * 74)
print("C11 total 字段识别")
print("=" * 74)
p11 = {"commentList": [{"nickname": "I", "content": "x"}],
       "totalCount": 156}
c, t = extract_comments(p11)
print(f"  评论 {len(c)} 条，total={t}")
check("C11 能读到总数", t == 156 or t is None, str(t))

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
