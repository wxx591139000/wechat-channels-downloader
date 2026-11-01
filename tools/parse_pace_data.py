"""解析抖音 __pace_f 脚本里的视频数据。

═══ 发现（实测 2026-11-01）═══
作品页有 3 个 `self.__pace_f.push([...])` 脚本：
  [0]   5,297 字节  含 video
  [1] 215,905 字节  含 desc/nickname/duration/video
  [2] 266,146 字节  含 desc/nickname/duration/video   ← 最全

这是 Next.js/React 的 RSC flight 数据流（`__pace_f` 是抖音的封装）。
数据被 JSON.stringify 后又转义了一层，需要逐层解。

本脚本验证能否稳定提取出：
  标题 / 作者 / 时长 / 下载地址 / 点赞数 / aweme_id
"""
import json
import re
import sys
from pathlib import Path

TMP = Path("C:/Users/Dancing/AppData/Local/Temp")


def load_pace(idx):
    p = TMP / f"pace_{idx}.txt"
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8", errors="replace")


def extract_flight_json(text):
    """从 `self.__pace_f.push([1,"<escaped json>"])` 里抠出 JSON 字符串。

    格式形如：
      self.__pace_f.push([1,"{\"is_oversea\":0,...}"])
    这里的 \\" 是 JS 字符串转义，需要还原。
    """
    results = []
    # 匹配 push([ 数字 , " ... " ])
    for m in re.finditer(r'__pace_f\.push\(\[\s*\d+\s*,\s*"((?:[^"\\]|\\.)*)"', text):
        raw = m.group(1)
        # JS 字符串反转义
        try:
            s = raw.encode("utf-8").decode("unicode_escape")
        except Exception:
            s = raw.replace('\\"', '"').replace("\\\\", "\\")
        results.append(s)
    return results


def find_all(obj, key, out=None, depth=0, max_depth=15):
    """递归找所有 key 的值。"""
    if out is None:
        out = []
    if depth > max_depth or len(out) > 50:
        return out
    if isinstance(obj, dict):
        if key in obj:
            out.append(obj[key])
        for v in obj.values():
            find_all(v, key, out, depth + 1, max_depth)
    elif isinstance(obj, list):
        for v in obj[:20]:
            find_all(v, key, out, depth + 1, max_depth)
    return out


def find_dict_with(obj, keys, out=None, depth=0, max_depth=15):
    """找同时含多个指定 key 的 dict（定位 aweme 主体）。"""
    if out is None:
        out = []
    if depth > max_depth or len(out) > 10:
        return out
    if isinstance(obj, dict):
        if all(k in obj for k in keys):
            out.append(obj)
        for v in obj.values():
            find_dict_with(v, keys, out, depth + 1, max_depth)
    elif isinstance(obj, list):
        for v in obj[:20]:
            find_dict_with(v, keys, out, depth + 1, max_depth)
    return out


print("=" * 74)
print("解析 __pace_f 数据")
print("=" * 74)

for idx in (2, 1, 0):
    txt = load_pace(idx)
    if txt is None:
        continue
    print(f"\n【pace_{idx}】{len(txt):,} 字节")
    chunks = extract_flight_json(txt)
    print(f"  提取出 {len(chunks)} 个 JSON 片段")

    all_objs = []
    for c in chunks:
        c2 = c.strip()
        if not c2:
            continue
        # 可能是 JSON 数组分段（RSC 格式），也可能是完整 JSON
        for cand in (c2, f"[{c2}]"):
            try:
                all_objs.append(json.loads(cand))
                break
            except Exception:
                continue

    print(f"  成功解析 {len(all_objs)} 个 JSON 对象")
    if not all_objs:
        continue

    # 找 aweme 主体
    aws = []
    for o in all_objs:
        aws.extend(find_dict_with(o, ("desc", "video")))
        aws.extend(find_dict_with(o, ("aweme_id", "desc")))

    print(f"  找到 {len(aws)} 个含 desc+video 的对象")
    for a in aws[:3]:
        if not isinstance(a, dict):
            continue
        desc = a.get("desc")
        if not desc:
            continue
        print(f"\n  ── 视频 ──")
        print(f"  desc        : {str(desc)[:70]}")
        print(f"  aweme_id    : {a.get('aweme_id')}")
        print(f"  duration    : {a.get('duration')}")
        au = a.get("author") or {}
        print(f"  author      : {au.get('nickname')}")
        print(f"  author.uid  : {au.get('sec_uid', '')[:30]}")
        v = a.get("video") or {}
        pa = v.get("play_addr") or {}
        urls = pa.get("url_list") or []
        print(f"  play_addr   : {urls[:1]}")
        print(f"  data_size   : {pa.get('data_size')}")
        print(f"  w/h         : {pa.get('width')}/{pa.get('height')}")
        br = v.get("bit_rate") or []
        print(f"  bit_rate    : {len(br)} 档")
        if br:
            best = max(br, key=lambda x: (x.get("play_addr") or {}).get(
                "data_size") or 0)
            bpa = best.get("play_addr") or {}
            print(f"    最高档    : {(bpa.get('url_list') or [''])[:1]}")
            print(f"    大小      : {bpa.get('data_size')}")
        st = a.get("statistics") or {}
        if st:
            print(f"  statistics  : 赞{st.get('digg_count')} "
                  f"评{st.get('comment_count')} 转{st.get('share_count')}")
        break
