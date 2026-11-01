"""抖音主页批量载入修复验证（v3.1.1）。

═══ 客户报障（2026-11-01）═══
「刚试了抖音解析博主，显示解析完成，成功 0/84 条」

但同一批作品：
  · 主页抓取拿到了 84 条（标题/时长/大小/作者全对）
  · play_url 实测 HTTP 200 + 有效 MP4

═══ 根因 ═══
`apply_item()` 里：
```python
url = self.videos[i][0]      # ❌ self.videos 还没铺满 → IndexError
self.videos[i] = (url, v)
```
主页模式走 dy_item 事件回填时 `self.videos` 是空列表，
`IndexError` 被外层 `except` 静默吞掉 → 表格一行没填 → 统计 0/84。

═══ 修法 ═══
1. `apply_item` 越界自动扩容，主页条目用 aweme_id 补 url
2. 主页模式改用批量事件 `dy_profile_batch` → `apply_profile_batch`
   （主页条目本身完整，不需要再逐条 resolve）

覆盖：
P1  ★apply_item 越界不崩（自动扩容）
P2  ★主页条目自动补 url（aweme_id 构造）
P3  ★有 apply_profile_batch 批量入口
P4  ★批量入口会往 videos 和 tree 都写入
P5  ★gui.py 有 dy_profile_batch 分发
P6  ★主页模式不再逐条 dy_item
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


ui = (ROOT / "sphdl" / "douyin_ui.py").read_text(encoding="utf-8")
gui = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8")

print("=" * 74)
print("P1-P2 apply_item 健壮性")
print("=" * 74)
i = ui.find("def apply_item")
j = ui.find("\n    def ", i + 10)
seg = ui[i:j] if j > i else ui[i:i + 2500]
print(f"  apply_item 片段 {len(seg)} 字符")

check("P1a ★越界时自动扩容", "self.videos.extend" in seg)
check("P1b ★扩容用 (\"\", None) 填充", '("", None)' in seg)
check("P2a ★主页条目用 aweme_id 补 url",
      "douyin.com/video/" in seg and "aweme_id" in seg)
check("P2b 不再裸取 videos[i][0]", "self.videos[i][0] if self.videos[i]" in seg)

print()
print("=" * 74)
print("P3-P4 批量入口")
print("=" * 74)
check("P3a ★有 apply_profile_batch", "def apply_profile_batch" in ui)
i2 = ui.find("def apply_profile_batch")
j2 = ui.find("\n    def ", i2 + 10)
seg2 = ui[i2:j2] if j2 > i2 else ui[i2:i2 + 2500]
check("P4a ★写入 self.videos", "self.videos[i] = (url, v)" in seg2
      or "self.videos.append" in seg2)
check("P4b ★写入 tree（插行或更新）",
      "self.tree.insert" in seg2 or "self.tree.item" in seg2)
check("P4c ★更新 _rows 映射", "self._rows[iid]" in seg2)
check("P4d 标记为可下载（☑ + 待下载）",
      "☑" in seg2 and "待下载" in seg2)

print()
print("=" * 74)
print("P5 gui 事件分发")
print("=" * 74)
check("P5a ★事件列表含 dy_profile_batch",
      '"dy_profile_batch"' in gui)
check("P5b ★分发调用 apply_profile_batch",
      "w.apply_profile_batch" in gui)

print()
print("=" * 74)
print("P6 主页模式不再逐条 dy_item")
print("=" * 74)
check("P6a ★主页模式发批量事件",
      '("dy_profile_batch", all_videos)' in ui)
# 作品链接模式仍应保留逐条 dy_item（那是需要 resolve 的）
check("P6b 作品链接模式仍走 dy_item（未误伤）",
      '("dy_item", i, v)' in ui)

print()
print("=" * 74)
print("P7 真实数据可达性（play_url 直下）")
print("=" * 74)
prof = (ROOT / "sphdl" / "douyin_profile_scroll.py").read_text(encoding="utf-8")
check("P7a ★主页抓取产出含 play_url", "play_url" in prof)
check("P7b ★_slim 保留 play_url", '"play_url"' in prof)

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
