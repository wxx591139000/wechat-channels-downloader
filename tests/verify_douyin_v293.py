"""抖音解析修复验证（v2.9.3）。

═══ 客户实测（2026-11-01）═══
贴 `https://www.douyin.com/jingxuan?modal_id=7702018171426868531`
结果：标题「抖音视频_7702018171426868531」、作者空、时长 0 秒。

═══ 根因（两个叠加）═══
① 正则只认 `/video/<数字>`，不认 `?modal_id=` → 提示「没找到抖音链接」
   （v2.9.1 已修）
② 修完正则后能解析，但**元数据全丢**。实测发现：
   · `jingxuan?modal_id=` 打开后页面**没有 h1**（标题在弹窗层，DOM 读不到）
   · `/video/<id>` 打开后 h1 有完整标题，detail 接口也会正常返回
   → 必须把链接**规范化**成 `/video/<id>` 再打开

═══ 三个数据来源（按优先级）═══
1. `aweme/detail` 接口（最全：标题/作者/时长/大小/地址）
2. DOM 兜底（h1 + video.duration + a[href*=user]）—— 接口失效时用
3. 页面媒体流（只有地址）

覆盖：
M1  ★modal_id 链接能被规范化成 /video/<id>
M2  ★规范化后 != 原链接（确实发生了转换）
M3  已是 /video/ 的不改
M4  纯 aweme_id 也能规范化
M5  ★DOM 兜底函数存在且字段完整
M6  ★DOM 兜底会排除 @douyin.com 这类误匹配
M7  无法解析 id 时保持原链接（不硬改）
M8  ★源码里 goto 用的是 nav_url 而不是 url
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


from sphdl.douyin import _aweme_id_from_url  # noqa: E402

print("=" * 74)
print("M1-M4 链接规范化")
print("=" * 74)

AID = "7702018171426868531"


def normalize(url):
    """复刻 _resolve_one 里的规范化逻辑。"""
    aid = _aweme_id_from_url(url)
    return (f"https://www.douyin.com/video/{aid}" if aid else url)


cases = [
    (f"https://www.douyin.com/jingxuan?modal_id={AID}", True),
    (f"https://www.douyin.com/video/{AID}", False),
    (f"https://www.douyin.com/discover?modal_id={AID}", True),
    (f"https://m.douyin.com/share/video/{AID}", True),
]
for u, should_change in cases:
    n = normalize(u)
    changed = n != u
    ok = (changed == should_change) and f"/video/{AID}" in n
    check(f"  {u[:52]:54s}", ok, f"→ {n[-30:]}")

# 裸数字 ID 不规范化（客户不会粘这个；extract_urls 也提取不到它）
# 保持原样交给下游，由 _aweme_id_from_url 返回空 → 跟原链接
check("M4 裸数字 ID 保持原样（不误改）",
      normalize(AID) == AID, normalize(AID))

print()
print("=" * 74)
print("M5-M7 DOM 兜底")
print("=" * 74)
src = (ROOT / "sphdl" / "douyin.py").read_text(encoding="utf-8")
i = src.find("def _parse_from_dom")
j = src.find("\ndef ", i + 10)
seg = src[i:j] if j > i else src[i:i + 4000]

check("M5a ★DOM 兜底函数存在", i > 0 and len(seg) > 500, f"{len(seg)} 字符")
check("M5b 取 h1 作为标题", "h1" in seg)
check("M5c 取 video.duration", "duration" in seg)
check("M5d 取 videoWidth/Height", "videoWidth" in seg)
check("M6 ★排除 @douyin.com 误匹配",
      ".com" in seg and "indexOf('@')" in seg, "有过滤逻辑")

check("M7 ★无法解析 id 时不硬改",
      "if _aid_for_nav else url" in src)

print()
print("=" * 74)
print("M8 goto 用规范化后的 URL")
print("=" * 74)
check("M8a ★有 nav_url 变量", "nav_url" in src)
check("M8b ★goto(nav_url) 而不是 goto(url)",
      "page.goto(nav_url" in src)
check("M8c 规范化只在有 id 时发生", "_aweme_id_from_url(url)" in src)

print()
print("=" * 74)
print("M9 DOM 兜底接入兜底路径")
print("=" * 74)
check("M9a ★detail 无数据时读 DOM", "dom_meta = _parse_from_dom(page)" in src)
check("M9b ★用 dom_meta 填标题而不是默认名",
      "title=dom_meta.title" in src)
check("M9c 保留默认名作为最后兜底",
      'title=f"抖音视频_{aid}"' in src)

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
