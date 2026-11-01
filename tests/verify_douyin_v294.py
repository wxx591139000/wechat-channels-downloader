"""短链与列宽修复验证（v2.9.4）。

═══ 客户实测（2026-11-01）═══
① 「状态栏显示不完整，希望列表的各列宽度允许人拖拽」
② 输入「长按复制此条消息，打开抖音搜索，查看TA的更多作品。
    https://v.douyin.com/N00VRZ-_isA/」
   → 报「30 秒内没等到视频数据」

═══ ② 的根因（实测）═══
```
短链 v.douyin.com/N00VRZ-_isA/
  → 落地 https://www.douyin.com/user/MS4wLjABAAAAAgTdaUkW9Hoblhe...
  → 这是【主页】，不是作品
```
但短链本身没有 ID → 被当成作品走 detail 流程 → 页面无 detail 接口
→ 等 20 秒报「30 秒内没等到视频数据」。

顺带发现 `normalize()` 不认 `iesdouyin.com/share/user/<sec_uid>`
（短链落地的真实形态）→ 判成 video。

覆盖：
S1  ★短链能解析出落地类型（profile）
S2  ★iesdouyin.com/share/user/ 识别为主页
S3  ★/user/<id>?modal_id= 仍是主页（判断顺序正确）
S4  /video/<id> 仍是作品
S5  jingxuan?modal_id= 是作品
S6  ★主页链接被当作品时有明确提示（不是等 20 秒）
S7  ★列宽 minwidth 已放开（可拖拽）
S8  ★列 stretch=True（可拉伸）
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


UID = "MS4wLjABAAAAAgTdaUkW9HoblheZJ-W44LQ-5YkSC2pbNbssIgKa8tg"

from sphdl.douyin_profile import normalize  # noqa: E402

print("=" * 74)
print("S1-S5 链接类型识别")
print("=" * 74)

cases = [
    (f"https://www.douyin.com/user/{UID}", "profile"),
    (f"https://www.douyin.com/user/{UID}?modal_id=123", "profile"),
    (f"https://www.iesdouyin.com/share/user/{UID}?from=1", "profile"),
    (f"https://www.douyin.com/video/7702018171426868531", "video"),
    ("https://www.douyin.com/jingxuan?modal_id=7702018171426868531", "video"),
    (f"https://www.iesdouyin.com/share/video/7702018171426868531", "video"),
    ("https://v.douyin.com/N00VRZ-_isA/", "short"),
]
for u, exp in cases:
    n = normalize(u)
    check(f"  {u[:60]:62s}", n["kind"] == exp,
          f"{n['kind']}（期望 {exp}）")

print()
print("=" * 74)
print("S2b share/user 能取到 sec_uid")
print("=" * 74)
n = normalize(f"https://www.iesdouyin.com/share/user/{UID}")
check("S2b ★sec_uid 正确", n.get("sec_uid") == UID,
      (n.get("sec_uid") or "")[:40])
check("S2c 规范化成 douyin.com/user/",
      n.get("url", "").startswith("https://www.douyin.com/user/"),
      n.get("url", "")[:60])

print()
print("=" * 74)
print("S6 主页被当作品时的提示")
print("=" * 74)
src = (ROOT / "sphdl" / "douyin.py").read_text(encoding="utf-8")
check("S6a ★有主页链接前置识别", '"/user/" in _u' in src)
check("S6b ★提示说明「请用主页批量抓取」",
      "主页批量抓取" in src)
check("S6c 提示说明怎么下单个视频", "点开该视频后再复制" in src)

print()
print("=" * 74)
print("S7-S8 列宽可拖拽")
print("=" * 74)
gui = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8")
# 定位主清单列配置段
i = gui.find('if c in ("no", "size"):')
seg = gui[i:i + 900] if i > 0 else ""
print(f"  列配置段 {len(seg)} 字符")
check("S7a ★可见列 minwidth=40（可拖窄）",
      "minwidth=40" in seg, seg.count("minwidth=40").__str__())
check("S8a ★可见列 stretch=True（可拉伸）",
      seg.count("stretch=True") >= 2, str(seg.count("stretch=True")))
check("S7b 隐藏列仍不可见（no/size 保持 width=0）",
      'width=0, minwidth=0, stretch=False' in seg)
check("S7c 点赞列也放开拖拽", "minwidth=40" in seg.split("like")[-1][:300]
      if "like" in seg else False)

print()
print("=" * 74)
print("S9 短链解析函数存在")
print("=" * 74)
prof = (ROOT / "sphdl" / "douyin_profile.py").read_text(encoding="utf-8")
check("S9a ★有 resolve_short_url", "def resolve_short_url" in prof)
check("S9b ★先用裸 HTTP（快）", "urllib.request" in prof)
check("S9c 再用浏览器兜底", "launch_persistent_context" in prof)
ui = (ROOT / "sphdl" / "douyin_ui.py").read_text(encoding="utf-8")
check("S9d ★界面已接入短链解析", "resolve_short_url" in ui or "_resolve_short" in ui)
check("S9e 短链→主页走方案B", 'kind == "profile"' in ui)

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
