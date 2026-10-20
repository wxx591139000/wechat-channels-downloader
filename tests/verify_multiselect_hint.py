"""多选操作可见性测试（v2.1.9）。

用户报障：「有些用户不知道视频清单的单选，按着 ctrl 多选，
          配合 shift 的连续多选，全选等快捷操作方法，
          你可以在界面做个明显的说明吗？」

覆盖：
  M1 提示条存在且文字完整（四种操作都有）
  M2 「全选」按钮存在
  M3 「反选」按钮存在
  M4 全选按钮真的选中全部
  M5 反选逻辑正确
  M6 反选后 selection 取反
  M7 tree 的 selectmode = extended（Ctrl/Shift 多选的基础）
  M8 Ctrl+A 快捷键仍绑定
  M9 提示文字随窗口宽度换行（不截断）
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tkinter as tk
from tkinter import ttk

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


print("=" * 74)
print("M 多选操作可见性")
print("=" * 74)

# 起真实 App（无授权也能构造，只是功能受限）
import sphdl.gui as G

app = None
try:
    app = G.App()          # ★ App() 自己创建 Tk 根窗口，不接受参数
    app.root.withdraw()    # 隐藏窗口（测试不需要显示）
except Exception as e:
    print(f"  ⚠ App 构造失败：{type(e).__name__}: {e}")
    print("  → 无法验证 UI")

if app is not None:
    # M1 提示条
    has_bar = hasattr(app, "lbl_hint")
    check("M1a 提示条存在", has_bar)
    if has_bar:
        txt = app.lbl_hint.cget("text")
        print(f"  提示文字：{txt}")
        # ★ 文案用"按住 Ctrl 再点"而不是"Ctrl+点击"（更口语，客户更易懂）
        check("M1b 含 Ctrl 多选说明",
              ("按住 Ctrl" in txt) or ("Ctrl+点击" in txt))
        check("M1c 含 Shift 连续选说明",
              ("按住 Shift" in txt) or ("Shift+点击" in txt))
        check("M1d 含「Ctrl+A」", "Ctrl+A" in txt)
        check("M1e 含单选说明（点一行）", "点一行" in txt or "点一行" in txt)

    # M2/M3 按钮
    check("M2 「全选」按钮存在",
          hasattr(app, "btn_sel_all") and app.btn_sel_all.cget("text") == "全选")
    check("M3 「反选」按钮存在",
          hasattr(app, "btn_sel_inv") and app.btn_sel_inv.cget("text") == "反选")

    # M7 selectmode
    check("M7 tree selectmode=extended（Ctrl/Shift 多选基础）",
          str(app.tree.cget("selectmode")) == "extended",
          str(app.tree.cget("selectmode")))

    # M8 Ctrl+A 绑定
    binds = app.tree.bind()
    check("M8 Ctrl+A 快捷键已绑定",
          any("Control" in b and "a" in b.lower() for b in binds) or
          any("Control-a" in b for b in binds),
          str([b for b in binds if "Control" in b]))

    # M4/M5/M6 用假行测试全选/反选
    # 往 tree 插 5 行（不经过 store，只测选择逻辑）
    kids = []
    for i in range(5):
        kids.append(app.tree.insert("", "end", iid=f"_test_{i}",
                                    values=(f"状态{i}", f"标题{i}",
                                            "视频号", "1M", "嗅探", "10:00",
                                            "")))

    # ★ 列表里可能有真实历史记录（本次实测 629 行），
    #   所以断言用"相对变化"而不是固定行数。
    total = len(app.tree.get_children())
    print(f"  列表总行数: {total}（含新增测试行 5 行）")

    app._select_all()
    sel = set(app.tree.selection())
    check("M4 全选 → 选中数 == 总行数", len(sel) == total,
          f"{len(sel)}/{total}")

    # 反选：先只选 3 行，反选后应剩 total-3 行
    app.tree.selection_set(kids[:3])
    app._invert_selection()
    sel2 = set(app.tree.selection())
    check("M5a 选 3 行 → 反选后剩 total-3 行",
          len(sel2) == total - 3, f"{len(sel2)}/{total - 3}")
    check("M5b 反选结果正确（选中的被排除，其余全选）",
          not (set(kids[:3]) & sel2) and len(sel2) == total - 3,
          f"原选中的 3 行是否被排除: {not (set(kids[:3]) & sel2)}")

    # 全选后反选 → 全不选
    app._select_all()
    app._invert_selection()
    check("M6 全选后反选 → 全不选", len(app.tree.selection()) == 0,
          f"{len(app.tree.selection())} 行")

    # M9 换行配置
    check("M9 提示条绑定了 <Configure>（窄窗口换行）",
          "<Configure>" in str(app.hint_bar.bind()))

    # 清理测试行
    for k in kids:
        try:
            app.tree.delete(k)
        except Exception:
            pass

try:
    app.root.destroy()
except Exception:
    pass

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
