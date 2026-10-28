"""★ 真实数据回归：视频统计数在 objectDesc 的**兄弟位置**（v2.5.5）

═══ 客户报障链条（2026-10-26 ~ 10-27）═══
「点赞列都是「—」，但真实视频有点赞数」

| 轮次 | 做法 | 结果 |
|---|---|---|
| v2.5.0 | 猜 30+ 字段名，全在 object_desc 里找 | ❌ 全不中 |
| v2.5.2 | 加诊断扫 object_desc | ✅ 证明 object_desc 里**没有**统计数 |
| v2.5.3 | 扫所有 bridge 调用 | ✅ 找到评论接口，视频那条被跳过 |
| v2.5.4 | 转储完整 body | ✅ 拿到 314KB 真实样本 |
| v2.5.5 | **按真实结构接线** | ✅ 13/15 视频统计数全部解出 |

═══ 实测结论（不要改回去）═══

objectDesc 只有 12 个字段：
  description / media / media_type / location / ext_reading / topic /
  mentioned_user / feed_location / mentioned_musics / short_title /
  finder_newlife_desc / mod_feed_info

统计数是 objectDesc 的**兄弟键**，在同一个视频条目对象上：
  { "id": "...", "object_desc": {...}, "like_count": 135,
    "forward_count": 94, "comment_count": 0, "fav_count": 527 }

而且数据藏在**两层 JSON 字符串**里：
  obj["result"] → str → jsapi_resp["resp_json"] → str → 真正的 object 列表
而 object 是 **list**（迭代器必须处理顶层是 list 的情况，曾经漏过）

测试固件：tests/fixtures/v2514_real_stats.json
  （从客户机器 diag_full_body 提取的 3 条真实视频，取点赞最高的三条）

覆盖：
  T1 穿两层 JSON 字符串能找到条目
  T2 顶层是 list 也能找到（关键，v2.5.5 修的）
  T3 统计值逐值核对（135/94/0/527、88/24/13/149、57/103/9/96）
  T4 视频地址仍正常（没破坏原有解析）
  T5 无统计的条目返回 0（不编造）
  T6 不传 entry 时仍能解析（向后兼容，但统计为 0）
  T7 直接喂 314KB 原始大文件也能解出（若文件还在）
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


FX = ROOT / "tests" / "fixtures" / "v2514_real_stats.json"
REAL = json.loads(FX.read_text(encoding="utf-8"))

from sphdl.parser import iter_object_desc_entries, extract_from_object_desc

print("=" * 74)
print("T1/T2 穿透两层 JSON 字符串 + 顶层 list")
print("=" * 74)
pairs = list(iter_object_desc_entries(REAL))
print(f"  找到 {len(pairs)} 个 (desc, entry) 对")
check("T1 穿透 result / resp_json 两层字符串", len(pairs) == 3, str(len(pairs)))
if pairs:
    desc0, entry0 = pairs[0]
    check("T1b desc 真的是 object_desc", "media" in desc0, str(list(desc0)[:3]))
    check("T1c entry 含 object_desc（兄弟位置）", "object_desc" in entry0)
    check("T2 entry 上有 like_count", "like_count" in entry0)

# 单独测：顶层直接给 list
d2 = json.loads(json.loads(REAL["result"])["jsapi_resp"]["resp_json"])
lst = d2.get("object")
print(f"  单独喂 object 列表（{len(lst)} 条）…")
pairs_list = list(iter_object_desc_entries(lst))
check("T2b ★顶层是 list 也能找到（v2.5.5 修的）",
      len(pairs_list) == len(lst), f"{len(pairs_list)} vs {len(lst)}")

print()
print("=" * 74)
print("T3 统计值逐值核对（真实数据）")
print("=" * 74)
expect = [(135, 94, 0, 527), (88, 24, 13, 149), (57, 103, 9, 96)]
vds = []
for i, (desc, entry) in enumerate(pairs):
    vd = extract_from_object_desc(desc, entry=entry)
    if not vd:
        continue
    vds.append(vd)
    t = (vd.description or "")[:20].replace("\n", " ")
    print(f"  {t:<22} 赞={vd.like_count:>4} 转={vd.forward_count:>4} "
          f"评={vd.comment_count:>3} 藏={vd.fav_count:>4} "
          f"url={'有' if vd.url else '无'}")

check("T3a 解出 3 条", len(vds) == 3, str(len(vds)))
for i, exp in enumerate(expect):
    if i >= len(vds):
        check(f"T3 第{i+1}条统计", False, "缺失")
        continue
    v = vds[i]
    got = (v.like_count, v.forward_count, v.comment_count, v.fav_count)
    print(f"  第{i+1}条: 实际={got} 期望={exp}")
    check(f"T3 第{i+1}条统计逐值一致", got == exp, f"{got} vs {exp}")

print()
print("=" * 74)
print("T4 原有解析未破坏")
print("=" * 74)
if vds:
    v = vds[0]
    check("T4a 标题有值", bool(v.description), (v.description or "")[:20])
    check("T4b 地址有值", bool(v.url), (v.url or "")[:40])
    check("T4c 有清晰度列表", bool(v.renditions), str(len(v.renditions)))
    check("T4d 有 video_id", bool(v.video_id), v.video_id)

print()
print("=" * 74)
print("T5 无统计不编造 / T6 向后兼容")
print("=" * 74)
naked = {"object_desc": {"description": "无统计",
                         "media": [{"url": "http://z/v.mp4", "decodeKey": "k",
                                    "fileSize": 1,
                                    "spec": [{"fileFormat": "mp4",
                                              "width": 1, "height": 1}]}]}}
vp = list(iter_object_desc_entries(naked))
check("T5a 无统计条目也能找到", len(vp) == 1, str(len(vp)))
if vp:
    vd = extract_from_object_desc(vp[0][0], entry=vp[0][1])
    got = (vd.like_count, vd.forward_count, vd.fav_count)
    print(f"  无统计: {got}")
    check("T5b 全 0（不编造）", got == (0, 0, 0), str(got))

if pairs:
    vd_old = extract_from_object_desc(pairs[0][0])
    print(f"  不传 entry: 解析={'成功' if vd_old else '失败'} "
          f"赞={getattr(vd_old, 'like_count', '?')}")
    check("T6a 不传 entry 不崩", vd_old is not None)
    check("T6b 不传 entry 时统计为 0（取不到兄弟字段，符合预期）",
          getattr(vd_old, "like_count", -1) == 0,
          str(getattr(vd_old, "like_count", "?")))

print()
print("=" * 74)
print("T7 原始 314KB 大文件（若还在）")
print("=" * 74)
big = (ROOT / "dist" / "v2.5.4" / "微信视频号下载器" / "diag_full_body"
       / "093316_314830.json")
if big.exists():
    huge = json.loads(big.read_text(encoding="utf-8", errors="replace"))
    hp = list(iter_object_desc_entries(huge))
    nz = 0
    for desc, entry in hp:
        vd = extract_from_object_desc(desc, entry=entry)
        if vd and vd.like_count:
            nz += 1
    print(f"  大文件 {big.stat().st_size} 字节 → {len(hp)} 条，有点赞的 {nz} 条")
    check("T7a 大文件解出 15 条", len(hp) == 15, str(len(hp)))
    check("T7b 至少 13 条有点赞（真实数据实测）", nz >= 13, str(nz))
else:
    print("  （大文件已清理，跳过——固件已足够）")

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
