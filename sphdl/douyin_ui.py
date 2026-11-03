"""抖音批量下载 GUI（方案 A：粘贴链接）。

设计取舍：
  · **独立窗口**，不改动现有「视频号/公众号」链路 —— 跑通优先，零回归风险
  · 解析/下载都在后台线程，主线程只刷 UI（和现有代码同一套约定）
  · 下载到 `<下载目录>/抖音/` 子目录，不污染视频号文件
  · 复用主窗口的 `push_log`，日志统一
"""
from __future__ import annotations

import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from .logger import logger
from .util import one_line

FONT = "Microsoft YaHei UI"


class DouyinWindow:
    """抖音批量下载窗口。"""

    def __init__(self, app):
        self.app = app
        self.videos = []          # [(url, DouyinVideo)]
        self._stop = False
        self._busy = False
        self._rows = {}           # iid -> index
        # ★ v3.1.5：每条的状态/落盘文件（持久化用）；idx -> (status, file)
        #   status: pending / done / failed
        self._meta = {}
        self._carry_status = {}   # 重新解析时把上次"已下载"接回来
        self._store_job = None
        self._store_dirty = False
        self.root = app.root

        self.win = tk.Toplevel(self.root)
        self.win.title("抖音批量下载")
        # ★ v2.5.1：不能用 transient —— Windows 上 transient 窗口是「工具窗口」，
        #   标题栏会**没有最小化/最大化按钮**（客户报障）。
        #   去掉后就是普通窗口，三个系统按钮齐全，且可自由缩放。
        self.win.geometry("980x720")
        self.win.minsize(820, 600)
        self.win.resizable(True, True)
        self._build()
        self._refresh_login_state()
        # ★ v3.1.5：把上次解析的清单恢复出来（客户：重开工具清单是空的）
        try:
            self._restore_list()
        except Exception as e:                     # noqa: BLE001
            logger.warn(f"抖音清单恢复失败（按空清单继续）：{type(e).__name__}: {e}")
        # ★ v2.5.2（客户要求）：点 ✕ 只隐藏，**不中断**解析/下载。
        #   拦截系统标题栏的关闭按钮，统一走 _hide_window。
        try:
            self.win.protocol("WM_DELETE_WINDOW", self._hide_window)
        except Exception:
            pass

    # ────────────────── UI ──────────────────
    def _build(self):
        top = tk.Frame(self.win, bg="#111827", height=46)
        top.pack(fill="x")
        top.pack_propagate(False)
        tk.Label(top, text="  抖音批量下载", bg="#111827", fg="#ffffff",
                 font=(FONT, 13, "bold"), anchor="w").pack(side="left")
        tk.Label(top, text="无需安装证书 · 粘贴链接即可  ",
                 bg="#111827", fg="#9ca3af",
                 font=(FONT, 9)).pack(side="right")

        # ★ v2.5.1：显式窗口按钮（客户要求「页面上要能最小化/最大化」）
        #   系统标题栏也有，这里再给一套，鼠标不用去右上角找。
        def _btn(txt, cmd, tip):
            b = tk.Button(top, text=txt, command=cmd, bg="#1f2937",
                          fg="#e5e7eb", relief="flat", bd=0,
                          font=(FONT, 11, "bold"), padx=10, pady=0,
                          cursor="hand2", activebackground="#374151",
                          activeforeground="#ffffff", width=3)
            b.pack(side="right", padx=(0, 2), pady=6)
            self._tt(b, tip)
            return b

        _btn("✕", self._hide_window, "隐藏窗口（解析/下载会在后台继续）")
        self.btn_max = _btn("□", self._toggle_max, "最大化 / 还原窗口")
        _btn("—", self.win.iconify, "最小化到任务栏")

        # ★ 登录状态条（限流对策核心）
        self.loginbar = tk.Frame(self.win, bg="#fff8c5", height=38)
        self.loginbar.pack(fill="x")
        self.loginbar.pack_propagate(False)
        self.lbl_login = tk.Label(
            self.loginbar, text="", bg="#fff8c5", fg="#7a5c00",
            font=(FONT, 9, "bold"), anchor="w")
        self.lbl_login.pack(side="left", padx=(14, 8))
        self.btn_login = tk.Button(
            self.loginbar, text="登录抖音", command=self._on_login,
            bg="#f59e0b", fg="#ffffff", relief="flat",
            font=(FONT, 9, "bold"), padx=12, pady=3, cursor="hand2")
        self.btn_login.pack(side="left")
        self.btn_logout = tk.Button(
            self.loginbar, text="退出登录", command=self._on_logout,
            bg="#e5e7eb", relief="flat", font=(FONT, 9), padx=10, pady=3,
            cursor="hand2")
        self.btn_logout.pack(side="left", padx=6)

        pad = tk.Frame(self.win, bg="#ffffff")
        pad.pack(fill="both", expand=True)

        # ① 粘贴区
        tk.Label(pad, text="① 粘贴抖音链接（作品链接 或 博主主页链接，一行一个）",
                 bg="#ffffff", font=(FONT, 10, "bold")).pack(
            anchor="w", padx=14, pady=(12, 4))
        self.txt = tk.Text(pad, height=5, wrap="word", font=(FONT, 10),
                           bg="#ffffff", relief="solid", bd=1)
        self.txt.pack(fill="x", padx=14)
        self.txt.insert(
            "1.0",
            "作品链接：https://www.douyin.com/video/7xxxxxxxxxxxxxxxxx\n"
            "短  链：https://v.douyin.com/xxxxxx/\n"
            "主页链接：https://www.douyin.com/user/MS4wLjABAAAA...\n"
            "\n"
            "★ 粘主页链接 = 抓该博主的全部作品（最多抓 500 条，可改）\n"
            "★ 主页抓取【必须先登录】：未登录时抖音不返回作品列表，\n"
            "   页面会一直显示「数据加载中」。请先点顶部【登录抖音】扫码。")

        bar1 = tk.Frame(pad, bg="#ffffff")
        bar1.pack(fill="x", padx=14, pady=(8, 4))
        self.btn_parse = tk.Button(
            bar1, text="② 解析链接", command=self._on_parse,
            bg="#2563eb", fg="#ffffff", relief="flat",
            font=(FONT, 10, "bold"), padx=16, pady=6, cursor="hand2")
        self.btn_parse.pack(side="left")
        self.lbl_count = tk.Label(bar1, text="", bg="#ffffff",
                                  font=(FONT, 9), fg="#6b7280")
        self.lbl_count.pack(side="left", padx=10)

        # ★ 方案 B：主页最多抓多少条
        tk.Label(bar1, text="主页最多抓", bg="#ffffff",
                 font=(FONT, 9), fg="#6b7280").pack(side="left", padx=(6, 2))
        self.var_max = tk.StringVar(value="500")
        tk.Spinbox(bar1, from_=10, to=2000, width=6, textvariable=self.var_max,
                   font=(FONT, 9)).pack(side="left")
        tk.Label(bar1, text="条", bg="#ffffff", font=(FONT, 9),
                 fg="#6b7280").pack(side="left")

        # ② 结果表
        tk.Label(pad, text="② 解析结果（勾选后批量下载）",
                 bg="#ffffff", font=(FONT, 10, "bold")).pack(
            anchor="w", padx=14, pady=(10, 4))

        cols = ("sel", "title", "author", "dur", "size", "status")
        wrap = tk.Frame(pad, bg="#ffffff")
        wrap.pack(fill="both", expand=True, padx=14)
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings",
                                 height=10, selectmode="extended")
        heads = {"sel": ("✓", 34), "title": ("标题", 430),
                 "author": ("作者", 130), "dur": ("时长", 70),
                 "size": ("大小", 80), "status": ("状态", 150)}
        for c in cols:
            # 表头对齐跟数据对齐一致（v3.1.4 在主窗口定下的规矩：
            # 表头一律居中、数据一律左对齐 → 看着散）
            self.tree.heading(c, text=heads[c][0],
                              anchor=("center" if c in ("sel", "dur", "size")
                                      else "w"))
            self.tree.column(c, width=heads[c][1],
                             anchor="center" if c in ("sel", "dur", "size")
                             else "w",
                             stretch=(c == "title"))
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        # 点「✓」列切换勾选
        self.tree.bind("<Button-1>", self._on_click)
        # ★ v3.1.5：长标题被列宽硬切时，悬停 0.35 秒看全文
        #   （客户对"标题显示不完整"很敏感；抖音标题尤其长，还带话题标签）
        try:
            from tkinter import font as tkfont

            from .gui import RowTip
            self._tip_font = tkfont.Font(family=FONT, size=9)
            self._rowtip = RowTip(self.tree, self._tip_text_for_cell)
        except Exception as e:                     # noqa: BLE001
            self._tip_font = None
            logger.warn(f"抖音列表悬停提示启用失败：{type(e).__name__}: {e}")

        # ③ 操作条
        bar2 = tk.Frame(pad, bg="#ffffff")
        bar2.pack(fill="x", padx=14, pady=(8, 4))
        self.btn_all = tk.Button(
            bar2, text="全选", command=self._toggle_all, bg="#e5e7eb",
            relief="flat", font=(FONT, 9), padx=12, pady=5, cursor="hand2")
        self.btn_all.pack(side="left")
        self.btn_dl = tk.Button(
            bar2, text="③ 下载选中", command=self._on_download,
            bg="#16a34a", fg="#ffffff", relief="flat",
            font=(FONT, 10, "bold"), padx=16, pady=6, cursor="hand2")
        self.btn_dl.pack(side="left", padx=8)
        self.btn_stop = tk.Button(
            bar2, text="停止", command=self._on_stop, bg="#dc2626",
            fg="#ffffff", relief="flat", font=(FONT, 9), padx=12, pady=5,
            cursor="hand2", state="disabled")
        self.btn_stop.pack(side="left")
        self.btn_dir = tk.Button(
            bar2, text="打开下载目录", command=self._open_dir, bg="#e5e7eb",
            relief="flat", font=(FONT, 9), padx=12, pady=5, cursor="hand2")
        self.btn_dir.pack(side="right")

        self.pbar = ttk.Progressbar(pad, mode="determinate", maximum=100)
        self.pbar.pack(fill="x", padx=14, pady=(4, 2))
        self.lbl_stat = tk.Label(pad, text="就绪", bg="#ffffff", anchor="w",
                                 font=(FONT, 9), fg="#6b7280")
        self.lbl_stat.pack(fill="x", padx=14, pady=(0, 8))

    # ────────────────── 内部工具 ──────────────────
    def _tip_text_for_cell(self, iid, col_0):
        """悬停提示取文本：只有标题列**真的放不下**时才返回全文。

        跟主窗口同一套判据（字体实测像素宽 vs 当前列宽）：
        没被切的格子返回空串，不会满屏冒气泡。
        """
        if col_0 != 1:                       # 0=勾选列，1=标题列
            return ""
        try:
            vals = self.tree.item(iid, "values")
        except Exception:
            return ""
        if len(vals) <= 1:
            return ""
        text = str(vals[1])
        if not text or getattr(self, "_tip_font", None) is None:
            return text
        try:
            avail = int(self.tree.column("title", "width"))
            need = self._tip_font.measure(text) + 12
        except Exception:
            return text
        return text if need > avail else ""

    def _meta_of(self, idx) -> tuple:
        """取某条的状态 → (status, 文件路径)。"""
        m = self._meta.get(idx) or {}
        return (m.get("status") or "pending", m.get("file") or "")

    def _set_meta(self, idx, status=None, file=None):
        m = self._meta.setdefault(idx, {})
        if status is not None:
            m["status"] = status
        if file is not None:
            m["file"] = file

    # ── 清单持久化（v3.1.5：关掉工具后清单不丢） ──
    def _restore_list(self):
        """把上次解析的清单读回来铺进表格。

        ★ 客户 2026-11-03：「重新启动工具后，发现上一次解析出的抖音博主
          清单是空的」。原来清单只在内存，进程一退就没了。

        恢复出来的条目：play_url 可能已过期（抖音签名地址有有效期），
        真正下载时若失败会自动重新解析一次（见 _download_worker）。
        """
        from . import douyin_store

        entries = douyin_store.load()
        if not entries:
            return
        loaded = done = 0
        for e in entries:
            v = douyin_store.entry_to_video(e)
            if v is None or not getattr(v, "aweme_id", ""):
                continue
            url = f"https://www.douyin.com/video/{v.aweme_id}"
            fpath = str(e.get("file") or "")
            st = str(e.get("status") or "pending")
            if fpath and Path(fpath).exists():
                st = "done"
            elif st == "done":
                # 记录说完成、文件却没了（被挪走或删了）→ 当没下过
                st, fpath = "pending", ""
            mark = "☐" if st == "done" else "☑"
            txt = ("✅ 已完成" if st == "done"
                   else ("❌ 上次失败" if st == "failed" else "待下载"))
            idx = len(self.videos)
            self.videos.append((url, v))
            iid = self.tree.insert("", "end", values=(
                mark, one_line(v.title or "(无标题)"), v.author or "-",
                v.duration_text, v.size_text, txt))
            self._rows[iid] = idx
            self._meta[idx] = {"status": st, "file": fpath}
            loaded += 1
            if st == "done":
                done += 1
        if not loaded:
            return
        self.lbl_count.config(text=f"上次清单 {loaded} 条（已下载 {done} 条）")
        self.lbl_stat.config(text=f"已恢复上次的清单：{loaded} 条")
        self._log("info", f"已恢复上次解析的抖音清单：{loaded} 条"
                          f"（其中 {done} 条已下载）")
        if done:
            self._log("info", "已下载的条目默认不勾选；要重下请手动勾上。")

    def _mark_dirty(self):
        """清单有变化 → 1.2 秒后合并写盘（逐条解析时不会疯狂写文件）。"""
        try:
            self._store_dirty = True
            if self._store_job is None and self._alive():
                self._store_job = self.win.after(1200, self._flush_store)
        except Exception:
            pass

    def _flush_store(self):
        self._store_job = None
        if not self._store_dirty:
            return
        self._store_dirty = False
        self._write_store()

    def _write_store(self):
        """把当前清单按真实状态写盘（失败只记日志，绝不打断下载）。"""
        try:
            from . import douyin_store

            rows = []
            for i, (_url, v) in enumerate(self.videos):
                if v is None or not getattr(v, "aweme_id", ""):
                    continue
                st, fpath = self._meta_of(i)
                rows.append(douyin_store.video_to_entry(v, status=st,
                                                        file=fpath))
            douyin_store.save(rows)
        except Exception as e:                     # noqa: BLE001
            logger.warn(f"抖音清单写盘失败：{type(e).__name__}: {e}")

    def _dir(self) -> Path:
        base = Path(self.app.dir_var.get() or
                    Path.home() / "抖音下载")
        return base / "抖音"

    def _open_dir(self):
        import os
        d = self._dir()
        try:
            d.mkdir(parents=True, exist_ok=True)
            os.startfile(str(d))       # noqa: S606
        except Exception as e:
            messagebox.showerror("打开失败", str(e), parent=self.win)

    def _set_busy(self, busy: bool, stat: str = ""):
        self._busy = busy
        st = "disabled" if busy else "normal"
        self.btn_parse.config(state=st)
        self.btn_dl.config(state=st)
        self.btn_stop.config(state="normal" if busy else "disabled")
        if stat:
            self.lbl_stat.config(text=stat)

    def _log(self, lv, msg):
        try:
            self.app.push_log(lv, msg)
        except Exception:
            pass

    def _on_click(self, evt):
        """点「✓」列切换勾选状态。"""
        if self.tree.identify_region(evt.x, evt.y) != "cell":
            return
        col = self.tree.identify_column(evt.x)
        iid = self.tree.identify_row(evt.y)
        if col == "#1" and iid:
            vals = list(self.tree.item(iid, "values"))
            vals[0] = "☐" if vals[0] == "☑" else "☑"
            self.tree.item(iid, values=vals)

    def _toggle_all(self):
        want = "☑"
        # 若已全选则反选
        items = self.tree.get_children()
        if items and all(self.tree.item(i, "values")[0] == "☑"
                         for i in items):
            want = "☐"
        for i in items:
            vals = list(self.tree.item(i, "values"))
            vals[0] = want
            self.tree.item(i, values=vals)

    # ────────────────── 窗口按钮 ──────────────────
    def _alive(self) -> bool:
        """窗口是否还存在（被摧毁后回填要静默跳过，不能抛异常）。"""
        try:
            return bool(self.win.winfo_exists())
        except Exception:
            return False

    def _hide_window(self):
        """隐藏窗口，**不中断**正在进行的解析/下载。

        ★ 客户要求（2026-10-27）：
          「点 X 关闭抖音批量下载界面后，不应该影响解析、下载中的抖音任务」

        做法：withdraw 而不是 destroy ——
          · 后台线程照常跑，结果照常回填到（隐藏的）表格里
          · 再点主界面【🎵 抖音批量下载】就 deiconify，能看到真实进度
          · 真想停任务用界面里的【停止】按钮（明确、可控）
        """
        try:
            busy = self._busy
            self.win.withdraw()
            if busy:
                self._log("info", "抖音窗口已隐藏 —— 解析/下载仍在后台继续，"
                                  "再点【抖音批量下载】可看进度")
            else:
                self._log("info", "抖音窗口已隐藏（再点【抖音批量下载】可打开）")
        except Exception:
            pass

    def _toggle_max(self):
        """最大化 / 还原窗口，并同步按钮图标。"""
        try:
            if self.win.state() == "zoomed":
                self.win.state("normal")
                self.btn_max.config(text="□")
            else:
                self.win.state("zoomed")
                self.btn_max.config(text="❐")
        except Exception:
            # 某些 Tk 版本不支持 state("zoomed")，退回几何最大化
            try:
                import tkinter as _tk
                sw = self.win.winfo_screenwidth()
                sh = self.win.winfo_screenheight()
                self.win.geometry(f"{sw}x{sh - 60}+0+0")
            except Exception:
                pass

    def _tt(self, widget, text: str):
        """简单悬停提示（主窗口有同名方法，这里自己实现一份）。"""
        tip = tk.Toplevel(widget)
        tip.withdraw()
        tip.overrideredirect(True)
        lbl = tk.Label(tip, text=text, bg="#1f2328", fg="#ffffff",
                       font=(FONT, 9), justify="left", padx=8, pady=5)
        lbl.pack()

        def enter(_e=None):
            tip.deiconify()
            tip.update_idletasks()
            x = widget.winfo_rootx() + 12
            y = widget.winfo_rooty() + widget.winfo_height() + 4
            tip.geometry(f"+{x}+{y}")
            tip.lift()

        def leave(_e=None):
            tip.withdraw()

        widget.bind("<Enter>", enter, add="+")
        widget.bind("<Leave>", leave, add="+")
        widget.bind("<Destroy>", lambda _e: tip.destroy(), add="+")

    # ────────────────── 登录态（限流对策） ──────────────────
    def _refresh_login_state(self):
        """显示当前登录状态 + 对应的批量上限。"""
        try:
            from .douyin import is_logged_in, BATCH_ANON, BATCH_AUTH
            logged = is_logged_in()
        except Exception:
            logged = False
            BATCH_ANON, BATCH_AUTH = 10, 50
        if logged:
            self.lbl_login.config(
                text=f"✅ 已登录抖音（单批最多 {BATCH_AUTH} 条，速度快）",
                fg="#1a7f37")
            self.loginbar.config(bg="#dafbe1")
            self.lbl_login.config(bg="#dafbe1")
            self.btn_login.config(text="重新登录", bg="#e5e7eb", fg="#1f2328")
            self.btn_logout.pack(side="left", padx=6)
        else:
            self.lbl_login.config(
                text=f"⚠ 未登录（单批最多 {BATCH_ANON} 条，容易触发限流）"
                     f" —— 建议先登录，成功率高很多",
                fg="#7a5c00")
            self.loginbar.config(bg="#fff8c5")
            self.lbl_login.config(bg="#fff8c5")
            self.btn_login.config(text="登录抖音", bg="#f59e0b", fg="#ffffff")
            self.btn_logout.pack_forget()

    def _on_login(self):
        if self._busy:
            messagebox.showinfo("请稍等", "正在解析/下载中，请结束后再登录。",
                                parent=self.win)
            return
        if not messagebox.askyesno(
                "登录抖音",
                "接下来会打开一个浏览器窗口，请用抖音 App 扫码登录。\n\n"
                "登录后会明显降低「解析失败/限流」的概率。\n\n"
                "隐私说明：\n"
                "  · 登录态只保存在你自己的电脑上\n"
                "  · 不会上传，本工具不读取你的账号内容\n"
                "  · 随时可点「退出登录」删除\n\n"
                "继续？", parent=self.win):
            return
        self._set_busy(True, "请在弹出的浏览器窗口里扫码登录…")
        self.btn_login.config(state="disabled")

        def _worker():
            from .douyin import login_interactive
            ok, msg = login_interactive(on_log=self._log)
            self.app.ui_q.put(("dy_login_done", ok, msg))

        threading.Thread(target=_worker, daemon=True).start()

    def apply_login_done(self, ok: bool, msg: str):
        if not self._alive():
            return
        self._set_busy(False, "登录成功" if ok else f"登录未完成：{msg}")
        self.btn_login.config(state="normal")
        # ★ v2.5.1：登录流程自己已经确认成功（浏览器里检测到 sessionid），
        #   就不要再让 is_logged_in() 的二次判定把它否掉 ——
        #   客户报障「登录成功后界面仍显示未登录」就是这个原因之一。
        if ok:
            try:
                self._force_logged_in_ui()
            except Exception:
                pass
        self._refresh_login_state()
        if ok:
            messagebox.showinfo(
                "登录成功",
                "已保存抖音登录态。\n\n"
                "现在批量解析的成功率会高很多（单批上限也提高了）。",
                parent=self.win)
        else:
            messagebox.showwarning(
                "没有登录成功", f"{msg}\n\n可再点一次「登录抖音」重试。",
                parent=self.win)

    def _force_logged_in_ui(self):
        """把界面强制切成「已登录」样式（登录流程已确认成功时用）。"""
        try:
            from .douyin import BATCH_AUTH
        except Exception:
            BATCH_AUTH = 50
        self.lbl_login.config(
            text=f"✅ 已登录抖音（单批最多 {BATCH_AUTH} 条，速度快）",
            fg="#1a7f37")
        self.loginbar.config(bg="#dafbe1")
        self.lbl_login.config(bg="#dafbe1")
        self.btn_login.config(text="重新登录", bg="#e5e7eb", fg="#1f2328")
        self.btn_logout.pack(side="left", padx=6)

    def _on_logout(self):
        if not messagebox.askyesno(
                "退出登录",
                "将删除本机保存的抖音登录态（不影响你的抖音账号）。\n"
                "之后需要重新扫码才能恢复。\n\n继续？", parent=self.win):
            return
        from .douyin import logout
        ok = logout()
        self._refresh_login_state()
        self._log("ok" if ok else "warn",
                  "已删除本地抖音登录态" if ok else "删除登录态失败")

    # ────────────────── 解析 ──────────────────
    # ────────────── ★ 方案 B：主页批量抓取 ──────────────
    def _start_profile_fetch(self, profile_urls, video_urls):
        """抓取主页全部作品（方案 B）。

        流程：打开主页 → 让页面自己请求（签名天然正确）→ 读响应 → 滚动加载更多
        抓完后把视频列表填进表格，用户勾选下载。
        """
        try:
            max_items = int(float(self.var_max.get()))
        except Exception:
            max_items = 500
        max_items = max(10, min(2000, max_items))

        self._set_busy(True, f"正在抓取主页作品（最多 {max_items} 条）…")
        self._log("info", f"主页模式：{len(profile_urls)} 个主页，"
                          f"最多 {max_items} 条/主页")

        def _worker():
            # ★ v2.9.5：用「客户滚动 + 工具监听」方案（实测 84 条 22 秒抓完）
            from .douyin_profile_scroll import fetch_profile_by_scroll as fetch_profile
            from .douyin import resolve_many

            all_videos = []
            try:
                for pu in profile_urls:
                    if self._stop:
                        break
                    self._log("info", f"打开主页：{pu}")

                    def on_page(added, total):
                        self.app.ui_q.put(("dy_profile_progress",
                                           f"已抓到 {total} 条"))

                    res = fetch_profile(
                        pu, max_items=max_items,
                        on_log=lambda lv, m: self._log(lv, m),
                        on_page=on_page,
                        should_stop=lambda: self._stop,
                        visible=True,        # ★ 必须可见：headless 滚动不触发加载
                        auto_scroll=True,    # 工具自动滚，客户只需看着
                    )
                    if res.error:
                        self._log("error", f"主页抓取失败：{res.error}")
                        continue
                    who = res.nickname or res.sec_uid[:16]
                    self._log("ok", f"✅ {who}：抓到 {len(res.items)} 条作品"
                                    + (f"（{res.note}）" if res.note else ""))
                    all_videos.extend(res.items)

                if not all_videos:
                    self._log("error", "没有抓到任何作品")
                    self.app.ui_q.put(("dy_parse_done", 0))
                    return

                # ★ v3.1.1：主页条目**本身已完整**（play_url/标题/时长/大小都有），
                #   直接铺进 videos 并回填表格，不需要再逐条 resolve。
                self.app.ui_q.put(("dy_profile_batch", all_videos))
                self._log("ok", f"共 {len(all_videos)} 条，可以勾选下载了")

                # 如果同时还粘了作品链接，一并解析
                if video_urls and not self._stop:
                    self._log("info", f"另外解析 {len(video_urls)} 个作品链接…")

                    def on_item(i, url, v):
                        self.app.ui_q.put(("dy_item", len(all_videos) + i, v))

                    resolve_many(video_urls, on_item=on_item,
                                 on_log=lambda lv, m: self._log(lv, m),
                                 should_stop=lambda: self._stop)
            except Exception as e:                     # noqa: BLE001
                self._log("error", f"主页抓取异常: {type(e).__name__}: {e}")
            finally:
                self.app.ui_q.put(("dy_parse_done",
                                   len(all_videos) + len(video_urls)))

        import threading
        threading.Thread(target=_worker, daemon=True).start()

    def _on_parse(self):
        if self._busy:
            return
        from .douyin import extract_urls

        text = self.txt.get("1.0", "end")
        urls = extract_urls(text)
        if not urls:
            messagebox.showinfo(
                "没找到抖音链接",
                "请在文本框里粘贴抖音视频链接，例如：\n"
                "https://www.douyin.com/video/7xxxxxxxxxxxxxxxxx\n"
                "或短链 https://v.douyin.com/xxxxxx/",
                parent=self.win)
            return

        # ══════ ★ 方案 B：先检查有没有「主页链接」══════
        from .douyin_profile import normalize as _norm_profile
        from .douyin_profile import resolve_short_url as _resolve_short
        profile_urls = []
        video_urls = []
        shorts = []
        for u in urls:
            n = _norm_profile(u)
            if n["kind"] == "profile":
                profile_urls.append(n["url"])
            elif n["kind"] == "short":
                shorts.append(u)
            else:
                video_urls.append(u)

        # ★★★ v2.9.4（客户实测）★★★
        #   短链必须先跟随重定向才能知道是主页还是作品。
        #   客户贴「查看TA的更多作品 https://v.douyin.com/xxx/」
        #   → 落地是 /user/<sec_uid>（主页）
        #   → 但原实现把它当作品解析 → 报「30 秒内没等到视频数据」
        if shorts:
            self._log("info", f"解析 {len(shorts)} 个短链，判断是主页还是作品…")
            for su in shorts:
                r = _resolve_short(su)
                kind = r.get("kind")
                if kind == "profile" and r.get("sec_uid"):
                    profile_urls.append(r["url"])
                    self._log("ok", f"  {su} → 主页（将抓取全部作品）")
                elif kind == "video":
                    video_urls.append(r.get("resolved_from") or su)
                    self._log("ok", f"  {su} → 作品")
                else:
                    # 解析不出就当作品试（保持原行为，不静默丢弃）
                    video_urls.append(su)
                    self._log("warn", f"  {su} → 无法判断类型"
                                      f"（{r.get('error', '未知')}），按作品尝试")

        # ★ v3.1.5：先记住上一份清单里「已下载」的作品（aweme_id → 文件）。
        #   重新解析同一个博主时，状态能接回来 —— 客户不会看到明明下过的
        #   作品又变成「待下载」而重复下一遍 40GB。
        self._carry_status = {}
        for _i, (_u, _v) in enumerate(self.videos):
            if _v is None or not getattr(_v, "aweme_id", ""):
                continue
            _st, _fp = self._meta_of(_i)
            if _st == "done" and _fp:
                self._carry_status[_v.aweme_id] = _fp

        for i in list(self.tree.get_children()):
            self.tree.delete(i)
        self._rows.clear()
        self.videos.clear()
        self._meta.clear()
        self._stop = False

        if profile_urls:
            self._start_profile_fetch(profile_urls, video_urls)
            return

        self._set_busy(True, f"正在解析 {len(urls)} 条…（每条间隔 2~5 秒防限流）")

        # 先占位，边解析边回填
        for i, u in enumerate(urls):
            iid = self.tree.insert("", "end", values=(
                "☑", f"（待解析）{u[:40]}", "", "", "", "排队中"))
            self._rows[iid] = i
            self.videos.append((u, None))

        def _worker():
            from .douyin import resolve_many

            def on_item(i, url, v):
                self.app.ui_q.put(("dy_item", i, v))

            def on_log(lv, msg):
                self._log(lv, msg)

            try:
                resolve_many(urls, on_item=on_item, on_log=on_log,
                             should_stop=lambda: self._stop)
            except Exception as e:                 # noqa: BLE001
                self._log("error", f"抖音解析异常: {e}")
            self.app.ui_q.put(("dy_parse_done", len(urls)))

        threading.Thread(target=_worker, daemon=True).start()

    def apply_item(self, i: int, v):
        if not self._alive():
            return
        """（主线程）回填一条解析结果。

        ★★ v3.1.1 修复（客户：抖音解析博主「成功 0/84 条」）★★
          主页模式（方案B）抓到的条目**本身就完整**（有 play_url /
          title / duration / size），不需要再 resolve。
          但它走 dy_item 事件时，原来这里 `self.videos[i][0]`
          在 videos 还没铺满时会 IndexError → 被外层 except 吞掉
          → 表格一行没填 → 统计 0/84。

          修法：
            ① 越界时自动扩容 videos（不再依赖预先占位）
            ② 主页条目用 aweme_id 构造一个可追溯的 url
        """
        if i >= len(self.videos):
            # 扩容到 i+1，缺的用 ("", None) 填充
            self.videos.extend([("", None)] * (i + 1 - len(self.videos)))
        if self.videos[i] is None:
            self.videos[i] = ("", None)
        url = self.videos[i][0] if self.videos[i] else ""
        if not url and getattr(v, "aweme_id", ""):
            url = f"https://www.douyin.com/video/{v.aweme_id}"
        self.videos[i] = (url, v)
        iid = None
        for k, idx in self._rows.items():
            if idx == i:
                iid = k
                break
        if not iid:
            return
        if v.ok:
            self._set_meta(i, "pending")
            self.tree.item(iid, values=(
                "☑", one_line(v.title or "(无标题)"), v.author or "-",
                v.duration_text, v.size_text, "待下载"))
        else:
            self._set_meta(i, "failed")
            self.tree.item(iid, values=(
                "☐", f"（失败）{url[:36]}", "-", "-", "-",
                (v.error or "解析失败")[:26]))

    def apply_profile_batch(self, videos):
        """（主线程）主页批量抓取完成后，一次性把作品铺进表格。

        ★ v3.1.1：主页条目来自 aweme_list，本身字段完整
          （play_url / desc / duration / 大小 / 作者都有），
          不需要再走 resolve_many。直接填表即可下载。

        ★ v3.1.5：接回上次的「已下载」状态（_carry_status），
          已下过的默认不勾选 —— 客户重解析同一博主不会白下一遍。
        """
        if not self._alive():
            return
        carry = getattr(self, "_carry_status", {}) or {}
        done_n = 0
        for i, v in enumerate(videos):
            url = f"https://www.douyin.com/video/{v.aweme_id}" \
                  if getattr(v, "aweme_id", "") else ""
            if i < len(self.videos):
                self.videos[i] = (url, v)
            else:
                self.videos.append((url, v))
            # 状态：上次下过且文件还在 → 已完成，不勾选
            aid = getattr(v, "aweme_id", "")
            fpath = carry.get(aid, "")
            is_done = bool(fpath) and Path(fpath).exists()
            self._set_meta(i, "done" if is_done else "pending", fpath)
            if is_done:
                done_n += 1
            mark = "☐" if is_done else "☑"
            txt = "✅ 已完成" if is_done else "待下载"
            iid = None
            for k, idx in self._rows.items():
                if idx == i:
                    iid = k
                    break
            if iid is None:
                iid = self.tree.insert("", "end", values=(
                    mark, one_line(v.title or "(无标题)"), v.author or "-",
                    v.duration_text, v.size_text, txt))
                self._rows[iid] = i
            else:
                self.tree.item(iid, values=(
                    mark, one_line(v.title or "(无标题)"), v.author or "-",
                    v.duration_text, v.size_text, txt))
        self._log("ok", f"主页作品已载入表格：{len(videos)} 条，可直接勾选下载")
        if done_n:
            self._log("info", f"其中 {done_n} 条上次已经下过（文件还在），"
                              "默认不勾选，要重下请手动勾上")
        self._mark_dirty()

    def parse_done(self, n: int):
        if not self._alive():
            return
        ok = sum(1 for _, v in self.videos if v and v.ok)
        self._set_busy(False, f"解析完成：成功 {ok} / {n} 条")
        self.lbl_count.config(text=f"共 {n} 条，可下载 {ok} 条")
        self._log("ok" if ok else "warn",
                  f"抖音解析完成：成功 {ok}/{n}")
        self._mark_dirty()

    # ────────────────── 下载 ──────────────────
    # 出现这些关键词 = 「播放地址过期/失效」，值得重新解析一次
    _EXPIRED_KW = ("403", "404", "410", "过期", "失效", "太小",
                   "Forbidden", "Not Found")

    def _looks_expired(self, info) -> bool:
        s = str(info or "")
        if "已停止" in s:
            return False
        return any(k in s for k in self._EXPIRED_KW)

    def _re_resolve(self, v):
        """按 aweme_id 重新解析单条（阻塞，只在下载线程里调用）。

        ★ 恢复出来的清单里 play_url 可能已过期，重新解析能拿到新签名地址；
          断点文件按 aweme_id 命名，所以重新解析后照样从断点续传。
        """
        from .douyin import resolve_many

        aid = getattr(v, "aweme_id", "")
        if not aid:
            return None
        got = []
        try:
            resolve_many([f"https://www.douyin.com/video/{aid}"],
                         on_item=lambda i, u, nv: got.append(nv),
                         on_log=lambda lv, m: self._log(lv, m),
                         should_stop=lambda: self._stop,
                         use_cache=False)
        except Exception as e:                     # noqa: BLE001
            self._log("warn", f"重新解析失败：{type(e).__name__}: {e}")
            return None
        if not got:
            return None
        return got[0] if got[0].ok else None

    def _picked(self):
        out = []
        for iid in self.tree.get_children():
            if self.tree.item(iid, "values")[0] != "☑":
                continue
            i = self._rows.get(iid)
            if i is None or i >= len(self.videos):
                continue
            u, v = self.videos[i]
            # ★ v3.1.5：有 aweme_id 但播放地址为空/失效的（清单恢复后常见）
            #   也算可下载 —— 下载线程会先重新解析再下。
            if v and (v.ok or getattr(v, "aweme_id", "")):
                out.append((i, v))
        return out

    def _on_download(self):
        if self._busy:
            return
        items = self._picked()
        if not items:
            messagebox.showinfo("没有可下载的",
                                "请先解析，并勾选要下载的条目。",
                                parent=self.win)
            return
        total = len(items)
        if not messagebox.askyesno(
                "开始下载",
                f"将下载 {total} 条抖音视频到：\n{self._dir()}\n\n"
                "继续？", parent=self.win):
            return

        self._stop = False
        self._set_busy(True, f"正在下载 0/{total}…")

        # ★★ v3.1.2 修复（真实端到端测试抓出来的）★★
        #   原来 `_worker` 里第一行是 `dest = self._dir()`，而 `_dir()` 会读
        #   `self.app.dir_var.get()` —— 那是 **Tk 变量**。
        #   在后台线程里访问 Tk 变量，一旦主线程不在 mainloop 内就会抛
        #   `RuntimeError: main thread is not in main loop`；
        #   更糟的是：整个线程直接死掉，`dy_all_done` 永远不发 →
        #   界面**永久停在「正在下载 0/N」**，客户只能强杀进程。
        #   修法：目录在主线程（按钮回调里）先算好，线程只读字符串。
        dest = self._dir()

        def _worker():
            from .douyin import download
            done = 0
            failed = 0
            try:
                for n, (idx, v) in enumerate(items, 1):
                    if self._stop:
                        self._log("warn", "抖音：已停止下载")
                        break
                    self.app.ui_q.put(("dy_dl_start", idx, n, total))
                    try:
                        last = [0.0]

                        def prog(got, tot, dt, idx=idx, n=n):
                            # 限频：最多每 0.4 秒刷一次 UI
                            if time.time() - last[0] < 0.4:
                                return
                            last[0] = time.time()
                            self.app.ui_q.put(("dy_dl_prog", idx, got, tot,
                                               n, total))

                        # ★ v3.1.5：清单恢复出来的条目可能压根没有可用播放地址
                        #   （上次解析失败/只存了元数据）→ 先重新解析一次
                        if (not v.ok and not self._stop
                                and getattr(v, "aweme_id", "")):
                            nv = self._re_resolve(v)
                            if nv is not None:
                                v = nv

                        ok, info = download(v, dest, on_progress=prog,
                                            should_stop=lambda: self._stop)

                        # ★ v3.1.5：清单是持久化的，play_url 可能是上次存的、
                        #   签名已过期（抖音地址有时效）。这时自动重新解析一次
                        #   再下 —— 断点文件按 aweme_id 命名，续传照常有效。
                        if (not ok and not self._stop
                                and self._looks_expired(info)
                                and getattr(v, "aweme_id", "")):
                            self._log("warn",
                                      f"下载地址可能已过期，重新解析后接着下："
                                      f"{one_line(v.title)[:24]}")
                            nv = self._re_resolve(v)
                            if nv is not None and nv.ok:
                                v = nv
                                ok, info = download(v, dest, on_progress=prog,
                                                    should_stop=lambda: self._stop)
                    except Exception as e:            # noqa: BLE001
                        ok, info = False, f"{type(e).__name__}: {e}"

                    if ok:
                        done += 1
                        self.app.ui_q.put(("dy_dl_done", idx, True, info,
                                           n, total))
                    else:
                        failed += 1
                        self.app.ui_q.put(("dy_dl_done", idx, False, info,
                                           n, total))
            except Exception as e:                    # noqa: BLE001
                # ★ 线程级兜底：绝不能让异常悄悄结束线程 → 界面卡在 busy
                #   （PITFALLS P34：except 不许静默；这里还要补发结束事件）
                self._log("error", f"抖音下载线程异常: {type(e).__name__}: {e}")
            finally:
                self.app.ui_q.put(("dy_all_done", done, failed, total))

        threading.Thread(target=_worker, daemon=True).start()

    def apply_dl_start(self, idx, n, total):
        if not self._alive():
            return
        iid = self._iid_of(idx)
        if iid:
            v = list(self.tree.item(iid, "values"))
            v[5] = f"下载中 {n}/{total}"
            self.tree.item(iid, values=v)
        self._set_busy(True, f"正在下载 {n}/{total}")

    def apply_dl_prog(self, idx, got, tot, n, total):
        if not self._alive():
            return
        iid = self._iid_of(idx)
        if not iid:
            return
        v = list(self.tree.item(iid, "values"))
        pct = int(got * 100 / tot) if tot else 0
        v[5] = f"下载中 {pct}%"
        self.tree.item(iid, values=v)
        # 进度条 = 已完成条数 + 当前条进度
        if total:
            overall = ((n - 1) + (pct / 100.0)) * 100.0 / total
            self.pbar["value"] = max(0, min(100, overall))
        self.lbl_stat.config(
            text=f"正在下载 {n}/{total} ｜ {got/1048576:.1f} MB"
                 + (f" / {tot/1048576:.1f} MB" if tot else ""))

    def apply_dl_done(self, idx, ok, info, n, total):
        if not self._alive():
            return
        iid = self._iid_of(idx)
        # ★ 下载函数可能返回 "路径|WARN|说明" —— 表示文件下下来了但不完美
        warn = ""
        if ok and "|WARN|" in str(info):
            info, warn = str(info).split("|WARN|", 1)
        if iid:
            v = list(self.tree.item(iid, "values"))
            if ok:
                if warn:
                    v[5] = "⚠ 已完成(无声)"
                    self._log("warn",
                              f"抖音下载完成但有问题：{Path(info).name} — "
                              f"{warn}")
                else:
                    v[5] = "✅ 已完成"
                    self._log("ok", f"抖音下载完成：{Path(info).name}")
                self.tree.item(iid, values=v)
                # ★ v3.1.5：记下落盘路径，重启后能显示「已完成」并默认不勾选
                self._set_meta(idx, "done", str(info))
            else:
                v[5] = f"❌ {str(info)[:22]}"
                self.tree.item(iid, values=v)
                self._log("error", f"抖音下载失败：{info}")
                self._set_meta(idx, "failed")
        if total:
            self.pbar["value"] = n * 100.0 / total
        self._mark_dirty()

    def apply_all_done(self, done, failed, total):
        if not self._alive():
            return
        # 统计「完成但无声」的条数，必须让用户知道
        silent = 0
        for iid in self.tree.get_children():
            if "无声" in str(self.tree.item(iid, "values")[5]):
                silent += 1
        tail = f"（其中 {silent} 条无声音）" if silent else ""
        self._set_busy(False, f"下载结束：成功 {done} / 失败 {failed}"
                              f"（共 {total}）{tail}")
        self.pbar["value"] = 100 if failed == 0 else self.pbar["value"]
        self._log("ok" if failed == 0 else "warn",
                  f"抖音批量下载结束：成功 {done}，失败 {failed}")
        if failed == 0 and done:
            try:
                if messagebox.askyesno(
                        "下载完成",
                        f"全部 {done} 条已下载完成。\n\n"
                        f"目录：{self._dir()}\n\n打开目录看看？",
                        parent=self.win):
                    self._open_dir()
            except Exception:
                pass

    def _iid_of(self, idx):
        for k, i in self._rows.items():
            if i == idx:
                return k
        return None

    def _on_stop(self):
        self._stop = True
        self._set_busy(True, "正在停止…（当前这一条下完后停）")
