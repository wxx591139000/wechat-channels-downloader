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

FONT = "Microsoft YaHei UI"


class DouyinWindow:
    """抖音批量下载窗口。"""

    def __init__(self, app):
        self.app = app
        self.videos = []          # [(url, DouyinVideo)]
        self._stop = False
        self._busy = False
        self._rows = {}           # iid -> index
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

        pad = tk.Frame(self.win, bg="#f6f8fa")
        pad.pack(fill="both", expand=True)

        # ① 粘贴区
        tk.Label(pad, text="① 粘贴抖音链接（一行一个，也可直接粘整段分享文案）",
                 bg="#f6f8fa", font=(FONT, 10, "bold")).pack(
            anchor="w", padx=14, pady=(12, 4))
        self.txt = tk.Text(pad, height=5, wrap="word", font=(FONT, 10),
                           bg="#ffffff", relief="solid", bd=1)
        self.txt.pack(fill="x", padx=14)
        self.txt.insert("1.0",
                        "例：https://www.douyin.com/video/7xxxxxxxxxxxxxxxxx\n"
                        "或：https://v.douyin.com/xxxxxx/")

        bar1 = tk.Frame(pad, bg="#f6f8fa")
        bar1.pack(fill="x", padx=14, pady=(8, 4))
        self.btn_parse = tk.Button(
            bar1, text="② 解析链接", command=self._on_parse,
            bg="#2563eb", fg="#ffffff", relief="flat",
            font=(FONT, 10, "bold"), padx=16, pady=6, cursor="hand2")
        self.btn_parse.pack(side="left")
        self.lbl_count = tk.Label(bar1, text="", bg="#f6f8fa",
                                  font=(FONT, 9), fg="#6b7280")
        self.lbl_count.pack(side="left", padx=10)

        # ② 结果表
        tk.Label(pad, text="② 解析结果（勾选后批量下载）",
                 bg="#f6f8fa", font=(FONT, 10, "bold")).pack(
            anchor="w", padx=14, pady=(10, 4))

        cols = ("sel", "title", "author", "dur", "size", "status")
        wrap = tk.Frame(pad, bg="#f6f8fa")
        wrap.pack(fill="both", expand=True, padx=14)
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings",
                                 height=10, selectmode="extended")
        heads = {"sel": ("✓", 34), "title": ("标题", 360),
                 "author": ("作者", 130), "dur": ("时长", 70),
                 "size": ("大小", 80), "status": ("状态", 150)}
        for c in cols:
            self.tree.heading(c, text=heads[c][0])
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

        # ③ 操作条
        bar2 = tk.Frame(pad, bg="#f6f8fa")
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
        self.lbl_stat = tk.Label(pad, text="就绪", bg="#f6f8fa", anchor="w",
                                 font=(FONT, 9), fg="#6b7280")
        self.lbl_stat.pack(fill="x", padx=14, pady=(0, 8))

    # ────────────────── 内部工具 ──────────────────
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

        for i in list(self.tree.get_children()):
            self.tree.delete(i)
        self._rows.clear()
        self.videos.clear()
        self._stop = False
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
        """（主线程）回填一条解析结果。"""
        if i >= len(self.videos):
            return
        url = self.videos[i][0]
        self.videos[i] = (url, v)
        iid = None
        for k, idx in self._rows.items():
            if idx == i:
                iid = k
                break
        if not iid:
            return
        if v.ok:
            self.tree.item(iid, values=(
                "☑", v.title or "(无标题)", v.author or "-",
                v.duration_text, v.size_text, "待下载"))
        else:
            self.tree.item(iid, values=(
                "☐", f"（失败）{url[:36]}", "-", "-", "-",
                (v.error or "解析失败")[:26]))

    def parse_done(self, n: int):
        if not self._alive():
            return
        ok = sum(1 for _, v in self.videos if v and v.ok)
        self._set_busy(False, f"解析完成：成功 {ok} / {n} 条")
        self.lbl_count.config(text=f"共 {n} 条，可下载 {ok} 条")
        self._log("ok" if ok else "warn",
                  f"抖音解析完成：成功 {ok}/{n}")

    # ────────────────── 下载 ──────────────────
    def _picked(self):
        out = []
        for iid in self.tree.get_children():
            if self.tree.item(iid, "values")[0] != "☑":
                continue
            i = self._rows.get(iid)
            if i is None or i >= len(self.videos):
                continue
            u, v = self.videos[i]
            if v and v.ok:
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

        def _worker():
            from .douyin import download
            dest = self._dir()
            done = 0
            failed = 0
            for n, (idx, v) in enumerate(items, 1):
                if self._stop:
                    self._log("warn", "抖音：已停止下载")
                    break
                self.app.ui_q.put(("dy_dl_start", idx, n, total))
                fname = ""
                try:
                    last = [0.0]

                    def prog(got, tot, dt, idx=idx, n=n):
                        # 限频：最多每 0.4 秒刷一次 UI
                        if time.time() - last[0] < 0.4:
                            return
                        last[0] = time.time()
                        self.app.ui_q.put(("dy_dl_prog", idx, got, tot,
                                           n, total))

                    ok, info = download(v, dest, on_progress=prog,
                                        should_stop=lambda: self._stop)
                    fname = info if ok else ""
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
            else:
                v[5] = f"❌ {str(info)[:22]}"
                self.tree.item(iid, values=v)
                self._log("error", f"抖音下载失败：{info}")
        if total:
            self.pbar["value"] = n * 100.0 / total

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
