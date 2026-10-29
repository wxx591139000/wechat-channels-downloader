"""自动翻页窗口（v2.7.0）。

给客户的一个小窗：设置翻页次数/间隔 → 开始 → 看着它自己滑。

设计要点（沿用抖音窗口踩过的坑）：
  · **✕ 是隐藏不是销毁**：任务还在跑，销毁窗口会让回调全丢
  · 窗口不做 transient（否则标题栏没有最小化/最大化）
  · 所有回调加 _alive() 保护
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .logger import logger


class AutoPageWindow:
    def __init__(self, app):
        self.app = app
        self.pager = None
        self._destroyed = False

        self.win = tk.Toplevel(app.root)
        self.win.title("🔁 自动翻页抓取（直播回放）")
        self.win.geometry("620x520")
        self.win.minsize(560, 460)
        self.win.resizable(True, True)
        self.win.protocol("WM_DELETE_WINDOW", self._hide)

        self._build()
        self._refresh_target()

    # ── 状态 ────────────────────────────────────────────
    def _alive(self) -> bool:
        if self._destroyed:
            return False
        try:
            return bool(self.win.winfo_exists())
        except Exception:
            return False

    def _hide(self):
        """✕ = 隐藏，任务继续。"""
        try:
            self.win.withdraw()
        except Exception:
            pass

    # ── 界面 ────────────────────────────────────────────
    def _build(self):
        pad = ttk.Frame(self.win, padding=12)
        pad.pack(fill="both", expand=True)

        tk.Label(pad, text="自动翻页抓取直播回放",
                 font=("Microsoft YaHei UI", 13, "bold"),
                 fg="#0f766e").pack(anchor="w")
        tk.Label(pad,
                 text="工具会在「微信视频号」窗口里自动往下滑，"
                      "每页的长视频（≥10 分钟，多为回放）会自动进清单。",
                 font=("Microsoft YaHei UI", 9), fg="#4b5563",
                 wraplength=560, justify="left").pack(anchor="w", pady=(4, 10))

        # 窗口检测状态
        self.lbl_target = tk.Label(pad, text="", font=("Microsoft YaHei UI", 9),
                                   anchor="w", justify="left")
        self.lbl_target.pack(anchor="w", fill="x")

        # 参数
        opt = ttk.Frame(pad)
        opt.pack(anchor="w", pady=(10, 6))
        ttk.Label(opt, text="最多翻页：").pack(side="left")
        self.var_pages = tk.StringVar(value="40")
        ttk.Spinbox(opt, from_=1, to=300, width=6,
                    textvariable=self.var_pages).pack(side="left")
        ttk.Label(opt, text="   每页等待(秒)：").pack(side="left")
        self.var_int = tk.StringVar(value="3.0")
        ttk.Spinbox(opt, from_=1.0, to=15.0, increment=0.5, width=6,
                    textvariable=self.var_int).pack(side="left")

        # 按钮
        btnbar = ttk.Frame(pad)
        btnbar.pack(anchor="w", pady=(4, 8))
        self.btn_start = ttk.Button(btnbar, text="▶ 开始自动翻页",
                                    command=self._start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(btnbar, text="■ 停止", state="disabled",
                                   command=self._stop)
        self.btn_stop.pack(side="left", padx=(8, 0))
        ttk.Button(btnbar, text="🔄 重新检测窗口",
                   command=self._refresh_target).pack(side="left", padx=(8, 0))
        # ★ v2.7.1：先滚一次验证，别等跑 40 页才发现没生效
        ttk.Button(btnbar, text="👁 测试滚一次",
                   command=self._test_scroll).pack(side="left", padx=(8, 0))

        # 进度
        self.pb = ttk.Progressbar(pad, mode="determinate", maximum=100)
        self.pb.pack(fill="x", pady=(2, 4))
        self.lbl_prog = tk.Label(pad, text="未开始", anchor="w",
                                 font=("Microsoft YaHei UI", 9), fg="#374151")
        self.lbl_prog.pack(anchor="w")

        # 日志
        box = ttk.Frame(pad)
        box.pack(fill="both", expand=True, pady=(8, 0))
        self.txt = tk.Text(box, height=10, wrap="word",
                           font=("Microsoft YaHei UI", 9),
                           bg="#0f172a", fg="#e2e8f0", relief="flat")
        sb = ttk.Scrollbar(box, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        self.txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        tk.Label(pad, text="提示：开始后请不要点击视频号窗口，让它自己滑；"
                           "连续 3 轮没新增会自动停止。",
                 font=("Microsoft YaHei UI", 8), fg="#6b7280",
                 anchor="w").pack(anchor="w", pady=(6, 0))

    # ── 窗口检测 ────────────────────────────────────────
    def _refresh_target(self):
        if not self._alive():
            return
        try:
            from .autopage import find_video_window
            hwnd, rect = find_video_window()
            if hwnd:
                w = rect[2] - rect[0]
                h = rect[3] - rect[1]
                self.lbl_target.config(
                    text=f"✅ 已找到视频号窗口（{w}×{h}）—— 可以开始",
                    fg="#0f766e")
            else:
                self.lbl_target.config(
                    text="⚠ 没找到视频号窗口。请先打开「微信视频号」"
                         "并停在该博主的直播回放列表上，再点【重新检测窗口】。",
                    fg="#b45309")
        except Exception as e:                       # noqa: BLE001
            self.lbl_target.config(text=f"⚠ 检测失败：{e}", fg="#b91c1c")

    # ── 启停 ────────────────────────────────────────────
    def _start(self):
        if not self._alive():
            return
        try:
            pages = int(self.var_pages.get())
        except Exception:
            pages = 40
        try:
            interval = float(self.var_int.get())
        except Exception:
            interval = 3.0

        from .autopage import AutoPager, find_video_window
        hwnd, _ = find_video_window()
        if not hwnd:
            self._log("⚠ 没找到视频号窗口，无法开始。请先打开视频号并停在回放列表。",
                      "warn")
            return

        self.txt.delete("1.0", "end")
        self._log(f"开始：最多 {pages} 页，每页等 {interval:.1f} 秒", "ok")

        self.pager = AutoPager(
            max_pages=pages, interval=interval,
            count_fn=lambda: len(getattr(self.app.store, "items", {}) or {}),
            on_log=lambda m, lv="info": self.app.ui_q.put(("ap_log", m, lv)),
            on_progress=lambda d, t, a: self.app.ui_q.put(
                ("ap_prog", d, t, a)),
            on_done=lambda res: self.app.ui_q.put(("autopage_done", res)),
        )
        self.pager.start()
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")

    def _test_scroll(self):
        """只滚一次，让客户肉眼确认页面动没动（比跑 40 页再判定快得多）。"""
        if not self._alive():
            return
        try:
            from .autopage import activate_window, find_video_window, \
                post_wheel, send_wheel, set_cursor_pos, get_cursor_pos, WHEEL_DELTA
            hwnd, rect = find_video_window()
            if not hwnd:
                self._log("⚠ 没找到视频号窗口 —— 请先打开「微信视频号」"
                          "并停在回放列表上", "warn")
                return
            import win32gui
            title = win32gui.GetWindowText(hwnd) or ""
            cx = (rect[0] + rect[2]) // 2
            cy = (rect[1] + rect[3]) // 2
            fg = activate_window(hwnd)
            target = hwnd
            try:
                sub = win32gui.WindowFromPoint((cx, cy))
                if sub and win32gui.GetAncestor(sub, 2) == hwnd:
                    target = sub
            except Exception:
                pass
            old = get_cursor_pos()
            try:
                set_cursor_pos(cx, cy)
                import time as _t
                _t.sleep(0.1)
                send_wheel(-WHEEL_DELTA * 3)
                post_wheel(target, -WHEEL_DELTA * 3, cx, cy)
                _t.sleep(0.05)
            finally:
                set_cursor_pos(*old)
            self._log(f"测试滚动已发出 —— 目标「{title[:20]}」"
                      f"{rect[2]-rect[0]}×{rect[3]-rect[1]} "
                      f"前台={'是' if fg else '否'}", "ok")
            self._log("请现在看一眼视频号窗口：页面往下动了吗？"
                      "动了就点【开始自动翻页】；没动请把这行信息发给我。")
        except Exception as e:                      # noqa: BLE001
            self._log(f"❌ 测试滚动失败：{type(e).__name__}: {e}", "err")

    def _stop(self):
        if self.pager:
            self.pager.stop()
            self._log("已请求停止……", "warn")
        self.btn_stop.config(state="disabled")

    # ── 事件回填（主线程调）──────────────────────────────
    def _log(self, msg, level="info"):
        if not self._alive():
            return
        tag = {"ok": "✅ ", "warn": "⚠ ", "err": "❌ "}.get(level, "")
        try:
            self.txt.insert("end", f"{tag}{msg}\n")
            self.txt.see("end")
        except Exception:
            pass
        try:
            logger.info(f"[自动翻页] {msg}")
        except Exception:
            pass

    def apply_log(self, msg, level="info"):
        self._log(msg, level)

    def apply_progress(self, done, total, added):
        if not self._alive():
            return
        try:
            self.pb.config(maximum=max(total, 1), value=done)
            self.lbl_prog.config(text=f"第 {done}/{total} 页 · 累计新增 {added} 条")
        except Exception:
            pass

    def _on_done(self, result):
        if not self._alive():
            return
        r = result or {}
        try:
            self.lbl_prog.config(
                text=f"结束：翻 {r.get('pages', 0)} 页，新增 {r.get('added', 0)} 条"
                     f"（{r.get('reason', '')}）")
        except Exception:
            pass
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
