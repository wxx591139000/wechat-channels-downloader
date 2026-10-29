"""批量拉取统一入口验证（v2.7.2）。

═══ 客户反馈（2026-10-29）═══
1. 「点测试滚一次后窗口真动了」→ 滚轮机制 OK
2. 「点自动翻页后页面动了几次后停那不动了」
   「翻动页面时不要自动把直播回放的窗口顶到最前方」
3. 「用户操作最简：常规视频号和直播回放应自动识别，
    不该让用户区分点『批量拉取』还是『自动翻页』」

═══ 调查结论 ═══
「⚡ 批量拉取」本来就已经能做全部：
  · 整页入库（一页 8 条回放全抓）
  · 页面内 JS 自动滚动到底
  · 只滚「当前聚焦」页面 → **从不调用 SetForegroundWindow，不抢焦点**
  · 拉到底自动停

我新加的「🔁 自动翻页」是重复实现，且用 Win32 滚轮 + 强制前置 → 会抢焦点。
→ **删除该按钮，能力并入批量拉取。**

覆盖：
U1  「🔁 自动翻页」按钮已移除（客户不该看到两个入口）
U2  autopage_ui / autopage 模块已删除（不留死代码）
U3  「⚡ 批量拉取」提示文案说明了「短视频和回放都用这一个」
U4  批量拉取提示明确「不顶到最前、不抢焦点」
U5  ★滚轮兜底存在且不抢焦点（只用 PostMessage）
U6  兜底只在「12 秒无进展」时触发（不干扰正常 JS 滚动）
U7  兜底随批量拉取启停（不会关掉后还在滚）
U8  ★源码里没有 SetForegroundWindow 出现在批量的滚动路径上
U9  长视频整页捕获仍在（v2.6.6 的能力没丢）
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


gui = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8")
addon = (ROOT / "sphdl" / "addon.py").read_text(encoding="utf-8")

print("=" * 74)
print("U1-U2 入口合并")
print("=" * 74)
check("U1a 主界面没有「🔁 自动翻页」按钮", "🔁 自动翻页" not in gui,
      "已移除")
check("U1b 没有 _on_autopage 入口", "def _on_autopage" not in gui)
check("U1c 没有 autopage_done 事件分支", "autopage_done" not in gui)
check("U2a autopage_ui.py 已删除",
      not (ROOT / "sphdl" / "autopage_ui.py").exists())
print(f"  autopage.py 是否保留: {(ROOT / 'sphdl' / 'autopage.py').exists()}")
check("U2b autopage.py 保留为「滚轮工具」（被兜底复用）",
      (ROOT / "sphdl" / "autopage.py").exists()
      and "from .autopage import find_video_window, post_wheel" in gui)

print()
print("=" * 74)
print("U3-U4 文案：一个按钮覆盖两种场景 + 不抢焦点")
print("=" * 74)
check("U3a 提示写明「短视频和直播回放都用这一个按钮」",
      "【短视频】和【直播回放】都用这一个按钮" in gui)
check("U3b 提示写明自动识别", "自动识别" in gui)
check("U3c 提示写明回放「整页全部入库」",
      "整页全部入库" in gui)
check("U4a 提示写明不顶到最前", "不会把窗口顶到最前" in gui)
check("U4b tooltip 也写明不抢焦点", "不抢焦点" in gui)

print()
print("=" * 74)
print("U5-U7 滚轮兜底")
print("=" * 74)
check("U5a 有兜底启动方法", "def _start_batch_wheel_fallback" in gui)
check("U5b 有兜底停止方法", "def _stop_batch_wheel_fallback" in gui)
check("U5c 兜底用 post_wheel（PostMessage）", "post_wheel(hwnd" in gui)
# 兜底代码段里不能有 SetForegroundWindow / set_cursor_pos
i = gui.find("def _start_batch_wheel_fallback")
j = gui.find("def _set_batch", i)
seg = gui[i:j]
print(f"  兜底代码段 {len(seg)} 字符")
# ★ 只查**真实调用**，不查注释/文档串里的文字
#   （第一版把注释里「不调 SetForegroundWindow」这句话当成了违规 —— 测试误报）
import re as _re
calls = _re.findall(r"[\w.]*SetForegroundWindow\s*\(", seg)
print(f"  兜底代码段里 SetForegroundWindow 真实调用: {len(calls)} 次 {calls}")
check("U5d ★兜底不调 SetForegroundWindow", not calls, str(calls))
check("U5e ★兜底不移动光标", "set_cursor_pos" not in seg)
check("U5f 兜底不用 activate_window", "activate_window" not in seg)

check("U6a 12 秒无进展才触发", "12" in seg and "last_change" in seg)
check("U6b 每 4 秒检查一次", "wait(4.0)" in seg)
check("U6c 有新捕获就重置计时", "last_change = time.time()" in seg)

check("U7a 批量开启时启动兜底", "_start_batch_wheel_fallback()" in gui)
check("U7b 批量关闭时停止兜底", "_stop_batch_wheel_fallback()" in gui)
# 关闭路径里确实调用了停止
k = gui.find('self.addon._batch_active = False')
check("U7c 停止调用在「_batch_active=False」旁边",
      "_stop_batch_wheel_fallback()" in gui[k:k + 300],
      gui[k:k + 120].replace("\n", " "))

print()
print("=" * 74)
print("U8-U9 关键约束")
print("=" * 74)
# 整份 gui 里出现 SetForegroundWindow 的地方不能是批量滚动路径
cnt = gui.count("SetForegroundWindow")
print(f"  gui.py 里 SetForegroundWindow 出现 {cnt} 次")
_calls8 = _re.findall(r"[\w.]*SetForegroundWindow\s*\(", seg)
check("U8 批量滚动路径不含强制前置", not _calls8, str(_calls8))

check("U9a 长视频整页捕获仍在（v2.6.6 能力没丢）",
      "LONG_VIDEO_SEC" in addon and "长视频整页捕获" in addon)
check("U9b 批量模式整页入库仍在", "batch_mode" in addon)
check("U9c 批量模式只滚聚焦页（不抢焦点）",
      "chosen = bool(self._batch_page) and pid == self._batch_page" in addon)

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
