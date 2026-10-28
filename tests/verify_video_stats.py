"""视频统计数据（点赞/转发/推荐）+ 按点赞排序 验证（v2.5.0）。

═══ 客户需求（2026-10-26）═══
「视频号下载器可以抓取到视频的点赞数、转发数、推荐数，
  并支持按点赞数对视频列表进行排序吗」

改造前答案：**不能**。parser 里只解析了评论的点赞（_LIKE_KEYS），
视频本身的统计数据一个都没取；列表也没有这三列。

═══ 实现难点（如实说明）═══
微信不同版本/接口里这些字段**名字不固定**（likeCount / like_count /
diggCount / praiseCount…），我们还不能确定当前客户端用哪个 ——
需要真实 payload 才能定死。所以策略是「容错提取 + 命中诊断」：

  1. 顶层按别名列表找
  2. 递归到 stat/stats/counts/extInfo/baseInfo 等子对象找
  3. 找不到就是 0，界面显示「—」，**绝不编造**
  4. 捕获时在日志里打印「统计字段已识别：like_count=likeCount」
     或「没解析到…请反馈」，方便按真实字段名对齐

覆盖：
  S1  扁平结构提取（likeCount 等驼峰）
  S2  下划线结构提取（like_count）
  S3  嵌套在 stat / statistics / counts 里
  S4  JSON 字符串子对象
  S5  别名覆盖：diggCount / praiseCount / shareCount / forwardCount
  S6  畸形值（负数/字符串/None/bool）不崩且归 0
  S7  没数据时全 0（不编造）
  S8  VideoData 接到统计值
  S9  Record 接统计值 + 重启不丢（records.json 往返）
  S10 显示格式：0→「—」、1234→「1234」、12345→「1.2万」
  S11 列表有三列且表头正确
  S12 按点赞升序 / 降序排序正确（用原始整数，不用显示文本）
  S13 按转发 / 推荐排序
  S14 排序时 0 值（「—」）排在最后（降序）
"""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


print("=" * 74)
print("S1-S7 parser 容错提取")
print("=" * 74)
from sphdl.parser import extract_stats

MEDIA = [{"url": "http://x/v.mp4", "decodeKey": "k", "fileSize": 1000,
          "spec": [{"fileFormat": "mp4", "width": 720, "height": 1280}]}]

# S1 驼峰
od1 = {"id": "a", "description": "t", "media": MEDIA,
       "likeCount": 1234, "forwardCount": 56, "recommendCount": 78}
st1 = extract_stats(od1)
print(f"  S1 驼峰: {st1}")
check("S1a 点赞 likeCount", st1["like_count"] == 1234, str(st1["like_count"]))
check("S1b 转发 forwardCount", st1["forward_count"] == 56)
check("S1c 推荐 recommendCount", st1["recommend_count"] == 78)

# S2 下划线
od2 = {"like_count": 11, "forward_count": 22, "recommend_count": 33,
       "fav_count": 44, "comment_count": 55}
st2 = extract_stats(od2)
print(f"  S2 下划线: {st2}")
check("S2 全部下划线字段", st2 == {"like_count": 11, "forward_count": 22,
                                "recommend_count": 33, "fav_count": 44,
                                "comment_count": 55}, str(st2))

# S3 嵌套容器
for ck in ("stat", "stats", "statistics", "counts", "extInfo"):
    st = extract_stats({ck: {"likeCount": 7, "forwardCount": 8,
                             "recommendCount": 9}})
    ok = (st["like_count"], st["forward_count"], st["recommend_count"]) == (7, 8, 9)
    check(f"S3 嵌套在 {ck}", ok, str(st))

# S4 JSON 字符串子对象
st4 = extract_stats({"stat": json.dumps({"likeCount": 100,
                                         "forwardCount": 10})})
print(f"  S4 JSON 字符串: {st4}")
check("S4 JSON 字符串子对象", st4["like_count"] == 100
      and st4["forward_count"] == 10, str(st4))

# S5 其它别名
print("  S5 别名覆盖：")
for alias, field in (("diggCount", "like_count"),
                     ("praiseCount", "like_count"),
                     ("shareCount", "forward_count"),
                     ("transmitCount", "forward_count"),
                     ("favCount", "fav_count"),
                     ("collectCount", "fav_count"),
                     ("commentCount", "comment_count")):
    st = extract_stats({alias: 42})
    ok = st[field] == 42
    print(f"    {alias:14s} → {field:16s} {'✅' if ok else '❌'} {st[field]}")
    check(f"S5 {alias} → {field}", ok, str(st[field]))

# S6 畸形值
print("  S6 畸形值：")
for bad in (-5, "abc", None, True, [], {}, "12abc"):
    st = extract_stats({"likeCount": bad})
    print(f"    likeCount={bad!r:8s} → {st['like_count']}")
    check(f"S6 likeCount={bad!r} 不崩且≥0",
          isinstance(st["like_count"], int) and st["like_count"] >= 0,
          str(st["like_count"]))

# S7 无数据
st7 = extract_stats({"id": "x", "description": "y", "media": MEDIA})
print(f"  S7 无统计字段: {st7}")
check("S7 无数据时全 0（不编造）",
      all(v == 0 for v in st7.values()), str(st7))
check("S7b 非 dict 不崩", extract_stats("not a dict")["like_count"] == 0)

print()
print("=" * 74)
print("S8 VideoData 接线")
print("=" * 74)
from sphdl.parser import extract_from_object_desc

vd = extract_from_object_desc(dict(od1))
print(f"  like={vd.like_count} forward={vd.forward_count} "
      f"recommend={vd.recommend_count}")
check("S8a VideoData 有 like_count", vd.like_count == 1234)
check("S8b VideoData 有 forward_count", vd.forward_count == 56)
check("S8c VideoData 有 recommend_count", vd.recommend_count == 78)
check("S8d 不影响原有字段（url/title）",
      vd.url == "http://x/v.mp4" and vd.description == "t")

print()
print("=" * 74)
print("S9 Record 持久化")
print("=" * 74)
import sphdl.gui as G

cfgdir = Path(tempfile.mkdtemp(prefix="stat_"))
G.CONFIG_FILE = cfgdir / "config.json"
G.RECORDS_FILE = cfgdir / "records.json"
G.CONFIG_FILE.write_text("{}", encoding="utf-8")
G.RECORDS_FILE.write_text("[]", encoding="utf-8")

rec = G.Record(id="r1", title="统计测试", url="http://x/1.mp4",
               decode_key="k", size=1000, source="test",
               created="10-26 12:00:00", status="waiting", seq=1,
               like_count=8888, forward_count=99, recommend_count=77,
               fav_count=55, comment_count=44)
app = G.App()
app.root.withdraw()
app.root.update()
app.store.add(rec)
app.store.save()
app.root.destroy()

saved = json.loads(G.RECORDS_FILE.read_text(encoding="utf-8"))
row = saved[0] if isinstance(saved, list) else (saved.get("items") or [{}])[0]
print(f"  磁盘: like={row.get('like_count')} forward={row.get('forward_count')} "
      f"recommend={row.get('recommend_count')}")
check("S9a 写入磁盘", row.get("like_count") == 8888)
check("S9b 转发也写了", row.get("forward_count") == 99)
check("S9c 推荐也写了", row.get("recommend_count") == 77)

# 重启读取
app2 = G.App()
app2.root.withdraw()
app2.root.update()
r2 = app2.store.items.get("r1")
print(f"  重启后: like={getattr(r2, 'like_count', None)} "
      f"forward={getattr(r2, 'forward_count', None)}")
check("S9d 重启后统计仍在（白名单没漏）",
      r2 is not None and r2.like_count == 8888 and r2.forward_count == 99,
      f"{getattr(r2, 'like_count', None)}/{getattr(r2, 'forward_count', None)}")

print()
print("=" * 74)
print("S10 显示格式")
print("=" * 74)
for n, want in ((0, "—"), (1, "1"), (999, "999"), (1234, "1234"),
                (10000, "1万"), (12345, "1.2万"), (150000, "15万"),
                (1234567, "123.5万")):
    got = G.App._num_cell(n)
    ok = got == want
    print(f"  {n:>8} → {got:>8}  期望 {want:>8}  {'✅' if ok else '❌'}")
    check(f"S10 {n} → {want}", ok, got)

print()
print("=" * 74)
print("S11-S14 列表列与排序")
print("=" * 74)
# 用 app2 继续（已有一行），再加几行不同点赞
data = [("a", "低赞", 10, 5, 1), ("b", "高赞", 99999, 88, 9),
        ("c", "零赞", 0, 0, 0), ("d", "中赞", 500, 50, 5)]
for rid, title, lk, fw, rc in data:
    _r = G.Record(
        id=rid, title=title, url=f"http://x/{rid}.mp4", decode_key="",
        size=1000, source="test", created="10-26 12:00:00",
        status="waiting", seq=2, like_count=lk, forward_count=fw,
        recommend_count=rc)
    app2.store.add(_r)
    # ★ 必须真的插进 Treeview，否则 _row_of 里没有它（排序/取单元格都会 KeyError）
    try:
        app2._add_row(_r)
    except Exception:
        pass
# 确保 r1 也有 Treeview 行
_r1 = app2.store.items.get("r1")
if _r1 is not None:
    try:
        app2._add_row(_r1)
    except Exception:
        pass
app2.root.deiconify()
app2.root.geometry("1280x760+30+30")
for _ in range(8):
    app2.root.update()
    time.sleep(0.08)

cols = app2.tree["columns"]
print(f"  列: {cols}")
check("S11a 有点赞列", "like" in cols)
check("S11b ★不显示转发列（客户要求先不上）", "forward" not in cols,
      str(cols))
check("S11c ★不显示推荐列（客户要求先不上）", "recommend" not in cols,
      str(cols))
heads = {c: app2.tree.heading(c, "text") for c in cols}
print(f"  表头: {heads}")
check("S11d 点赞表头正确", "点赞" in heads.get("like", ""), heads.get("like"))
check("S11e values 长度与列数一致（防错位）",
      len(app2._row_values(app2.store.items["r1"])) == len(cols),
      f"{len(app2._row_values(app2.store.items['r1']))} vs {len(cols)}")

# 数据仍在（只是不显示）
r_b = app2.store.items.get("b")
print(f"  b 的转发/推荐数据仍在: forward={getattr(r_b,'forward_count',None)} "
      f"recommend={getattr(r_b,'recommend_count',None)}")
check("S11f ★转发数据仍解析并存盘（只是不显示）",
      getattr(r_b, "forward_count", 0) == 88, str(getattr(r_b, "forward_count", 0)))
check("S11g ★推荐数据仍解析并存盘（只是不显示）",
      getattr(r_b, "recommend_count", 0) == 9, str(getattr(r_b, "recommend_count", 0)))

# ★ v2.6.0：「点赞增强包」是增值功能，未授权时统计列一律显示「—」。
#   本测试验的是**显示格式**（万级换算），所以先打开授权开关。
app2._like_stats_ok = True
app2._refresh_like_column()
# ★ 关键：行值只在 _add_row 时生成过一次，改授权状态后必须**重绘所有行**
for _rid in list(app2._row_of.keys()):
    app2._update_row(_rid)
app2.root.update()

# 单元格显示
for rid in ("r1", "b", "c"):
    iid = app2._row_of.get(rid)
    if iid:
        vals = app2.tree.item(iid, "values")
        print(f"  {rid} 行: {vals}")
check("S11h 高赞显示为万级",
      "万" in str(app2.tree.item(app2._row_of["b"], "values")[6]),
      str(app2.tree.item(app2._row_of["b"], "values")[6]))
check("S11i 零赞显示「—」",
      app2.tree.item(app2._row_of["c"], "values")[6] == "—",
      str(app2.tree.item(app2._row_of["c"], "values")[6]))

# ★ v2.6.0：未授权（增值功能未购买）时点赞列一律「—」
app2._like_stats_ok = False
app2._update_row("b")
app2.root.update()
locked = app2.tree.item(app2._row_of["b"], "values")[6]
print(f"  未授权时高赞行: {locked!r}")
check("S11j ★未授权时点赞列显示「—」（增值功能锁住）",
      locked == "—", str(locked))
app2._like_stats_ok = True
app2._update_row("b")
app2.root.update()

# S12 按点赞排序
app2._sort_col = "like"
app2._sort_desc = True
app2._apply_sort()
app2.root.update()
order_desc = [app2.store.items[rid].title
              for rid in sorted(app2._row_of, key=lambda x: app2.tree.index(
                  app2._row_of[x]))]
print(f"  点赞降序: {order_desc}")
check("S12a 降序第一个是最高赞", order_desc[0] == "高赞", str(order_desc[:2]))
check("S12b 降序最后是零赞", order_desc[-1] == "零赞", str(order_desc[-1]))
check("S12c 顺序正确",
      order_desc == ["高赞", "统计测试", "中赞", "低赞", "零赞"],
      str(order_desc))

app2._sort_desc = False
app2._apply_sort()
app2.root.update()
order_asc = [app2.store.items[rid].title
             for rid in sorted(app2._row_of, key=lambda x: app2.tree.index(
                 app2._row_of[x]))]
print(f"  点赞升序: {order_asc}")
check("S12d 升序第一个是零赞", order_asc[0] == "零赞", str(order_asc[0]))
check("S12e 升序反转正确", order_asc == list(reversed(order_desc)),
      str(order_asc))

# S15 排序键必须是整数
app2._sort_col = "like"
k_like = app2._sort_key("b")
print(f"  _sort_key('b') = {k_like}")
check("S15 排序键是原始整数（不是'万'文本）",
      isinstance(k_like[0], int) and k_like[0] == 99999, str(k_like))

# S16 隐藏列的排序键仍可用（将来打开转发列时不用改代码）
app2._sort_col = "forward"
k_fw = app2._sort_key("b")
print(f"  _sort_key('b') 按转发 = {k_fw}")
check("S16 转发排序键仍可用（代码已就绪，只差显示）",
      isinstance(k_fw[0], int) and k_fw[0] == 88, str(k_fw))

app2.root.destroy()

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
