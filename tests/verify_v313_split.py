"""验证 v3.1.3：视频列表 / 日志 高度可拖拽 + 默认列表变高 + 高度记忆。

为什么必须有这个脚本：
    「能拖」这件事截图看不出来（截图只有一帧）。这里用真实的 Tk 事件
    （event_generate 带 rootx/rooty，跟鼠标拖动走同一条绑定链）去拖，
    再读 winfo_height 硬数据判定，而不是看代码"觉得对"。

用法：python tests/verify_v313_split.py
    退出码 0 = 全绿；1 = 有失败项。
"""
import json
import os
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 配置落到临时目录：绝不能污染本机真实 config.json（授权码在里面）
_TMP = tempfile.mkdtemp(prefix="sphdl_split_cfg_")
os.environ["SPHDL_DATA_DIR"] = _TMP
# ★ 预置一份最小配置：否则 userdata 会从"程序目录 config.json"迁移过来，
#   把开发机上的 log_pane 旧值带进测试（实测首跑就是 284 而不是默认 156）
(Path(_TMP) / "config.json").write_text(
    json.dumps({"log_pane": 0, "auto_download": False}), encoding="utf-8")

import sphdl.gui as G   # noqa: E402

assert str(G.CONFIG_FILE).startswith(_TMP), f"配置没落到临时目录：{G.CONFIG_FILE}"

FAILS = []


def check(name, ok, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + name + (f"   [{detail}]" if detail else ""))
    if not ok:
        FAILS.append(name)
    return ok


def pump(root, n=8):
    for _ in range(n):
        root.update_idletasks()
        root.update()
        time.sleep(0.03)


def drag(grip, target_root_y, steps=3):
    """模拟一次真实拖动：按下 → 分几步移动 → 松开。"""
    x = grip.winfo_rootx() + grip.winfo_width() // 2
    y0 = grip.winfo_rooty() + grip.winfo_height() // 2
    grip.event_generate("<Button-1>", x=5, y=5, rootx=x, rooty=y0)
    for i in range(1, steps + 1):
        ry = int(y0 + (target_root_y - y0) * i / steps)
        grip.event_generate("<B1-Motion>", x=5, y=5, rootx=x, rooty=ry)
    grip.event_generate("<ButtonRelease-1>", x=5, y=5, rootx=x, rooty=target_root_y)


def main():
    print("=" * 72)
    print("v3.1.3 分隔条验证（真实 Tk 事件 + winfo 硬数据）")
    print("=" * 72)

    app = G.App()
    root = app.root
    pump(root, 12)

    grip = app.split_grip
    logh = app._log_holder
    listf = app.list_frame

    # ---------- ① 默认布局 ----------
    print("\n① 默认布局")
    h0, l0 = logh.winfo_height(), listf.winfo_height()
    check("日志默认高度 = LOG_PANE_DEFAULT",
          abs(h0 - app.LOG_PANE_DEFAULT) <= 1, f"日志 {h0}px / 期望 {app.LOG_PANE_DEFAULT}")
    check("视频列表比 v3.1.2 的 300px 更高", l0 > 300, f"列表 {l0}px（v3.1.2 为 300px）")
    check("分隔条铺满整宽", grip.winfo_width() >= root.winfo_width() - 40,
          f"{grip.winfo_width()}px / 窗口 {root.winfo_width()}px")
    check("分隔条厚度 = SPLIT_H", grip.winfo_height() == app.SPLIT_H,
          f"{grip.winfo_height()}px / 期望 {app.SPLIT_H}")
    check("握把有 3 条短杠（Canvas item）", len(app.split_grip.find_all()) == 3,
          f"{len(app.split_grip.find_all())} 个 item")
    check("握把是一整块可点区域（Canvas，不是小子控件）",
          isinstance(app.split_grip, tk.Canvas),
          type(app.split_grip).__name__)
    check("鼠标指针是上下拖动型",
          str(grip.cget("cursor")) == "sb_v_double_arrow",
          str(grip.cget("cursor")))
    check("四条事件绑定都在（按下/拖动/松开/双击）",
          all(grip.bind(seq) for seq in
              ("<Button-1>", "<B1-Motion>", "<ButtonRelease-1>", "<Double-Button-1>")),
          ",".join(s for s in ("<Button-1>", "<B1-Motion>", "<ButtonRelease-1>",
                               "<Double-Button-1>") if not grip.bind(s)) or "齐全")

    # ---------- ② 往下拖 → 日志变矮、列表变高 ----------
    print("\n② 往下拖 70px（日志变矮、列表变高）")
    y0 = grip.winfo_rooty() + grip.winfo_height() // 2
    drag(grip, y0 + 70)
    pump(root, 6)
    h1, l1 = logh.winfo_height(), listf.winfo_height()
    check("日志变矮 ≈70px", 60 <= h0 - h1 <= 80, f"{h0} → {h1}")
    check("列表同步变高（总和不变）", abs((l1 - l0) - (h0 - h1)) <= 2,
          f"列表 {l0} → {l1}")

    # ---------- ②b 一直往下拖 → 日志不会消失 ----------
    print("\n②b 往下猛拖 400px（日志不能拖没）")
    drag(grip, grip.winfo_rooty() + grip.winfo_height() // 2 + 400)
    pump(root, 6)
    check("日志停在 LOG_PANE_MIN（保留最后几行）",
          logh.winfo_height() == app.LOG_PANE_MIN,
          f"{logh.winfo_height()}px / 下限 {app.LOG_PANE_MIN}px")
    check("日志文本仍在（没被压成 0）", app.log_text.winfo_height() >= 20,
          f"文本 {app.log_text.winfo_height()}px")

    # ---------- ③ 往上狂拖 → 日志卡在最大值，列表不被挤没 ----------
    print("\n③ 往上狂拖 600px（越界夹紧）")
    grip_y = grip.winfo_rooty() + grip.winfo_height() // 2
    drag(grip, grip_y - 600)
    pump(root, 6)
    h2, l2 = logh.winfo_height(), listf.winfo_height()
    check("日志停在「可用总高 - 列表下限」，没有被拖出界",
          h2 <= (l2 + h2) - app.LIST_PANE_MIN + 1,
          f"日志 {h2}px / 上限 {(l2 + h2) - app.LIST_PANE_MIN}px")
    check("列表保留 LIST_PANE_MIN 以上",
          l2 >= app.LIST_PANE_MIN, f"列表 {l2}px / 下限 {app.LIST_PANE_MIN}px")
    check("工具栏没被挤成 1×1", app.btn_download.winfo_height() > 10,
          f"下载按钮 {app.btn_download.winfo_width()}x{app.btn_download.winfo_height()}")

    # ---------- ④ 拖到中间 → 高度写入配置文件 ----------
    print("\n④ 拖到中间高度并检查配置记忆")
    drag(grip, grip.winfo_rooty() + grip.winfo_height() // 2 - 40)
    pump(root, 6)
    h3 = logh.winfo_height()
    cfg_path = Path(G.CONFIG_FILE)
    saved = 0
    try:
        saved = int(json.loads(cfg_path.read_text(encoding="utf-8")).get("log_pane", 0))
    except Exception as e:
        check("config.json 可读", False, f"{type(e).__name__}: {e}")
    check("拖动后高度写入 config.json", saved == h3, f"文件 {saved} / 实际 {h3}")

    # ---------- ⑤ 双击复位 ----------
    #   Tk 不允许 event_generate 生成 <Double-Button-1>（"Double modifier not
    #   allowed"），所以这里直接调绑定到该事件的那个 handler —— 与鼠标双击
    #   走的是同一段逻辑，只是不进 Tk 的双击识别器。
    print("\n⑤ 双击分隔条复位")
    check("双击事件已绑定", bool(grip.bind("<Double-Button-1>")), "已绑定")
    app._on_split_reset(None)
    pump(root, 6)
    check("双击后回到默认高度", abs(logh.winfo_height() - app.LOG_PANE_DEFAULT) <= 1,
          f"{logh.winfo_height()}px")

    # ---------- ⑥ 重启后沿用记住的高度 ----------
    print("\n⑥ 重启沿用记忆高度")
    app.cfg.log_pane = 260
    app.cfg.save()
    try:
        root.destroy()
    except Exception:
        pass
    time.sleep(0.3)
    app2 = G.App()
    pump(app2.root, 12)
    check("重启后日志高度 = 记忆值 260",
          abs(app2._log_holder.winfo_height() - 260) <= 1,
          f"{app2._log_holder.winfo_height()}px")
    check("重启后列表仍高于 240px",
          app2.list_frame.winfo_height() >= app2.LIST_PANE_MIN,
          f"{app2.list_frame.winfo_height()}px")
    try:
        app2.root.destroy()
    except Exception:
        pass

    print("\n" + "=" * 72)
    if FAILS:
        print(f"❌ 失败 {len(FAILS)} 项：" + "、".join(FAILS))
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
