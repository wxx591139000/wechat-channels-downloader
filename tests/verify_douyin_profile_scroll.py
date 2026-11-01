"""抖音主页抓取（客户滚动 + 工具监听）验证。

═══ 客户问题（2026-11-01）═══
「方案A里客户需要怎么互动来实现抓全部视频，体感如何」

═══ 实测定论 ═══

## 之前"方案A"的设想（每次抓第一页累积）—— 实测不成立
```
第1次抓取: 18 条，累计 18
 滚动
第2次抓取: 18 条，新增 0   ← 滚完重抓还是同一批！
```
客户滚多少次都只有 18 条。**这条路死了。**

## 正确方案：客户滚动 + 工具监听页面自发请求 —— 实测成功
```
滚 1 次 → 批1  cursor=1786593802000  18 条
滚 2 次 → 批2  cursor=1778817648000  18 条
滚 3 次 → 批3  cursor=1767605400000  18 条
滚 4 次 → 批4/5 has_more=0
→ 84 条全部拿到，22.5 秒
```

原理：抖音对「页面自己发的请求」放行，对「工具主动 fetch」限流。
所以让页面自己发（客户一滚就发），工具只监听。

覆盖：
R1  ★关键：监听页面自发请求的函数存在
R2  ★必须 visible=True（headless 滚动无效，实测）
R3  ★响应处理是精简后再传（不传 1.8MB 原始对象）
R4  ★has_more=0 时判定到底
R5  ★client 精简体包含下载必需字段
R6  ★界面已接入 scroll 版
R7  未登录有明确提示
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


src = (ROOT / "sphdl" / "douyin_profile_scroll.py").read_text(encoding="utf-8")
ui = (ROOT / "sphdl" / "douyin_ui.py").read_text(encoding="utf-8")

print("=" * 74)
print("R1-R5 核心实现")
print("=" * 74)
check("R1a ★有 fetch_profile_by_scroll",
      "def fetch_profile_by_scroll" in src)
check("R1b ★通过 on_response 监听页面请求",
      'pg.on("response", on_response)' in src)
check("R1c ★只认 aweme/post 接口",
      "aweme/v1/web/aweme/post" in src and "aweme/post" in src)
check("R2a ★visible 参数控制有头/无头",
      "headless=not visible" in src)
check("R2b ★默认 visible=True（headless 滚动实测无效）",
      "visible: bool = True" in src)

print()
print("=" * 74)
print("R3 精简传输（关键性能/稳定性）")
print("=" * 74)
check("R3a ★有 _slim 精简函数", "def _slim" in src)
check("R3b ★精简后才构造 DouyinVideo",
      "_slim(a)" in src and "DouyinVideo(" in src)
check("R3c 说明为什么精简（1.8MB 传输问题）",
      "1.8MB" in src or "几百 KB" in src)

print()
print("=" * 74)
print("R4 到底判定")
print("=" * 74)
check("R4a ★has_more=0 且已有批次则停止",
      'meta["batches"] > 0 and not meta["has_more"]' in src)
check("R4b 有 idle 兜底（防死等）", "idle >=" in src)
check("R4c 说明写「已到底，共 N 条」", "已到底，共" in src)

print()
print("=" * 74)
print("R5 精简体字段完整")
print("=" * 74)
for f in ("aweme_id", "title", "author", "duration_ms", "play_url",
          "size", "width", "height", "cover"):
    check(f"R5 字段 {f}", f'"{f}"' in src)

print()
print("=" * 74)
print("R6 界面接入")
print("=" * 74)
check("R6a ★界面导入 scroll 版",
      "douyin_profile_scroll" in ui or "fetch_profile_by_scroll" in ui)
check("R6b ★调用时 visible=True",
      "visible=True" in ui)
check("R6c ★调用时 auto_scroll=True", "auto_scroll=True" in ui)

print()
print("=" * 74)
print("R7 登录前置")
print("=" * 74)
check("R7a ★未登录直接返回指引", "is_logged_in" in src)
check("R7b 提示说明怎么登录", "登录抖音" in src)

print()
print("=" * 74)
print("R8 滚动 JS 存在且合理")
print("=" * 74)
check("R8a ★有 _SCROLL_JS", "_SCROLL_JS" in src)
check("R8b ★滚到底（scrollTop = scrollHeight）",
      "scrollTop = el.scrollHeight" in src or
      "scrollTop = best.scrollHeight" in src)
check("R8c 找可滚动容器", "overflowY" in src)
check("R8d 每次滚动后等待", "SCROLL_WAIT" in src and "time.sleep(SCROLL_WAIT)" in src)

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
