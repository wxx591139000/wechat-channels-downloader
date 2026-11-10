"""小鹅通课程下载 GUI（单条 / 批量链接）。

跟 `bilibili_ui.py` 同一套窗口骨架与线程约定，差别在：
  · 没有分P 展开 —— 小鹅通一节就是一条，粘贴几条就是几行
  · 多一个「登录 Cookie」输入框 —— 付费课需要（免费课留空即可）
  · 表格没有 UP 主列，换成清晰度（小鹅通只给「高清/超清」这种档位）

设计取舍：
  · 独立窗口，不改动视频号/公众号/抖音/B 站任何链路 —— 零回归风险
  · 下载到 `<下载目录>/小鹅通/`，不跟其它平台混
  · 一节一行，勾选粒度 = 下载粒度
  · 已存在的文件直接跳过（不清空重下），配合分片级断点续传
"""
from __future__ import annotations

import random
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from .logger import logger
from .util import one_line

FONT = "Microsoft YaHei UI"
#: 小鹅通品牌橙（比公众号的橙更深，避免两个窗口看着一样）
ACCENT = "#c2410c"


class XiaeoWindow:
    """小鹅通下载窗口。"""

    def __init__(self, app):
        self.app = app
        self.rows = []            # [XiaeoVideo]
        self._rows = {}           # iid -> index
        self._meta = {}           # idx -> {status, file}
        self._stop = False
        self._busy = False
        self._store_job = None
        self._store_dirty = False
        self.root = app.root

        self.win = tk.Toplevel(self.root)
        self.win.title("小鹅通课程下载")
        self.win.geometry("1020x760")
        self.win.minsize(860, 620)
        self.win.resizable(True, True)
        self._build()
        try:
            self._restore_list()
        except Exception as e:                     # noqa: BLE001
            logger.warn(f"小鹅通清单恢复失败（按空清单继续）：{type(e).__name__}: {e}")
        try:
            self.win.protocol("WM_DELETE_WINDOW", self._hide_window)
        except Exception as e:                     # noqa: BLE001
            logger.warn(f"小鹅通窗口关闭协议设置失败：{e}")

    # ────────────────── UI ──────────────────
    def _build(self):
        # ★ 这里**故意不做**自绘标题栏（P64-⑥）：
        #   本窗口是带系统标题栏的普通 Toplevel，自绘一条会变成两条标题栏、
        #   两套关闭入口；而原来那条深色带上的提示语与下面橙色提示条一字不差
        #   —— 纯重复。系统标题栏 + 橙色提示条就够了。
        bar = tk.Frame(self.win, bg="#fff7ed", height=34)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        tk.Label(bar,
                 text="免费/公开课不用登录；付费课请在下面填网页版 Cookie。"
                      "清晰度以课程实际转码为准（多为 720P）。",
                 bg="#fff7ed", fg="#9a3412", font=(FONT, 9),
                 anchor="w").pack(side="left", padx=14)

        pad = tk.Frame(self.win, bg="#ffffff")
        pad.pack(fill="both", expand=True)

        # ① 粘贴区
        tk.Label(pad, text="① 粘贴课程视频链接（一行一个，点进具体一节再复制地址）",
                 bg="#ffffff", font=(FONT, 10, "bold")).pack(
            anchor="w", padx=14, pady=(10, 4))
        self.txt = tk.Text(pad, height=5, wrap="word", font=(FONT, 10),
                           bg="#ffffff", relief="solid", bd=1)
        self.txt.pack(fill="x", padx=14)
        self.txt.insert(
            "1.0",
            "https://appxxxxxxx.h5.xiaoeknow.com/p/course/video/v_xxxxxxxx?product_id=p_xxxxxxxx\n"
            "\n"
            "★ 课程目录页（/p/course/column/...）解析不了，请点进具体一节\n"
            "★ 分享文案整段粘进来也行，工具自己抠链接")

        # Cookie 行
        crow = tk.Frame(pad, bg="#ffffff")
        crow.pack(fill="x", padx=14, pady=(6, 0))
        tk.Label(crow, text="登录 Cookie（可选）", bg="#ffffff",
                 font=(FONT, 9), fg="#475569").pack(side="left")
        self.var_cookie = tk.StringVar(value="")
        self.ent_cookie = tk.Entry(crow, textvariable=self.var_cookie,
                                   font=(FONT, 9), relief="solid", bd=1,
                                   show="•")
        self.ent_cookie.pack(side="left", fill="x", expand=True, padx=6)
        self.chk_show = tk.Checkbutton(
            crow, text="显示", bg="#ffffff", font=(FONT, 9),
            command=self._toggle_cookie_echo, cursor="hand2")
        self.chk_show.pack(side="left")
        self._tt(self.ent_cookie,
                 "免费课留空。付费课：浏览器登录小鹅通 → F12 → "
                 "Network 随便点一个请求 → 复制 Request Headers 里的 Cookie 整串")

        bar1 = tk.Frame(pad, bg="#ffffff")
        bar1.pack(fill="x", padx=14, pady=(8, 4))
        self.btn_parse = tk.Button(
            bar1, text="② 解析链接", command=self._on_parse,
            bg="#2563eb", fg="#ffffff", relief="flat",
            disabledforeground="#dbeafe",
            font=(FONT, 10, "bold"), padx=16, pady=6, cursor="hand2")
        self.btn_parse.pack(side="left")
        self.lbl_count = tk.Label(bar1, text="", bg="#ffffff",
                                  font=(FONT, 9), fg="#64748b")
        self.lbl_count.pack(side="left", padx=10)

        tk.Label(bar1, text="并发分片", bg="#ffffff",
                 font=(FONT, 9), fg="#64748b").pack(side="left", padx=(6, 2))
        self.var_workers = tk.StringVar(value="8")
        tk.Spinbox(bar1, from_=1, to=16, width=4, textvariable=self.var_workers,
                   font=(FONT, 9)).pack(side="left")
        tk.Label(bar1, text="（8 路实测约 5~6 MB/s）", bg="#ffffff",
                 font=(FONT, 9), fg="#64748b").pack(side="left")

        # ② 结果表
        tk.Label(pad, text="② 解析结果（勾选后下载）",
                 bg="#ffffff", font=(FONT, 10, "bold")).pack(
            anchor="w", padx=14, pady=(8, 4))

        cols = ("sel", "title", "dur", "quality", "size", "status")
        wrap = tk.Frame(pad, bg="#ffffff")
        # ★ 这里先不 pack：等高 DPI 放大后总高会超窗口，
        #   若表格先 pack 且 expand=True，会把底部控件静默挤出窗口（P64）。
        #   先把底部条 side="bottom" 占位，最后再让表格吃剩下的空间。
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings",
                                 height=10, selectmode="extended")
        heads = {"sel": ("✓", 34), "title": ("课程标题", 470),
                 "dur": ("时长", 78), "quality": ("清晰度", 86),
                 "size": ("大小", 90), "status": ("状态", 210)}
        centered = ("sel", "dur", "quality", "size")
        for c in cols:
            self.tree.heading(c, text=heads[c][0],
                              anchor="center" if c in centered else "w")
            self.tree.column(c, width=heads[c][1],
                             anchor="center" if c in centered else "w",
                             stretch=(c == "title"))
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._on_click)

        try:
            from tkinter import font as tkfont

            from .gui import RowTip
            self._tip_font = tkfont.Font(family=FONT, size=9)
            self._rowtip = RowTip(self.tree, self._tip_text_for_cell)
        except Exception as e:                     # noqa: BLE001
            self._tip_font = None
            logger.warn(f"小鹅通列表悬停提示启用失败：{type(e).__name__}: {e}")

        # ③ 操作条（先创建、稍后按 side="bottom" 顺序占位）
        bar2 = tk.Frame(pad, bg="#ffffff")
        self.btn_all = tk.Button(
            bar2, text="全选 / 全不选", command=self._toggle_all, bg="#e2e8f0",
            relief="flat", font=(FONT, 9), padx=12, pady=5, cursor="hand2")
        self.btn_all.pack(side="left")
        self.btn_dl = tk.Button(
            bar2, text="③ 下载选中", command=self._on_download,
            bg="#059669", fg="#ffffff", relief="flat",
            disabledforeground="#d1fae5",
            font=(FONT, 10, "bold"), padx=16, pady=6, cursor="hand2")
        self.btn_dl.pack(side="left", padx=8)
        self.btn_stop = tk.Button(
            bar2, text="停止", command=self._on_stop, bg="#dc2626",
            fg="#ffffff", relief="flat", font=(FONT, 9), padx=12, pady=5,
            cursor="hand2", state="disabled")
        self.btn_stop.pack(side="left")
        self.btn_dir = tk.Button(
            bar2, text="打开下载目录", command=self._open_dir, bg="#e2e8f0",
            relief="flat", font=(FONT, 9), padx=12, pady=5, cursor="hand2")
        self.btn_dir.pack(side="right")
        self.btn_clear = tk.Button(
            bar2, text="清空列表", command=self._on_clear, bg="#f1f5f9",
            relief="flat", font=(FONT, 9), padx=12, pady=5, cursor="hand2")
        self.btn_clear.pack(side="right", padx=6)

        self.pbar = ttk.Progressbar(pad, mode="determinate", maximum=100)
        self.lbl_stat = tk.Label(pad, text="就绪", bg="#ffffff", anchor="w",
                                 font=(FONT, 9), fg="#64748b")

        # ★ P64：底部三件套用 side="bottom" 自下而上占位（先 pack 的在最底部），
        #   表格放到最后 pack —— 这样窗口再矮也不会把状态条/按钮挤出可视区。
        self.lbl_stat.pack(side="bottom", fill="x", padx=14, pady=(0, 8))
        self.pbar.pack(side="bottom", fill="x", padx=14, pady=(4, 2))
        bar2.pack(side="bottom", fill="x", padx=14, pady=(8, 4))
        wrap.pack(fill="both", expand=True, padx=14)

    # ────────────────── 内部工具 ──────────────────
    def _tt(self, widget, text: str):
        tip = {"win": None}

        def _show(_e=None):
            if tip["win"] is not None:
                return
            try:
                x = widget.winfo_rootx() + 12
                y = widget.winfo_rooty() + widget.winfo_height() + 4
                w = tk.Toplevel(widget)
                w.wm_overrideredirect(True)
                w.wm_geometry(f"+{x}+{y}")
                tk.Label(w, text=text, bg="#1e293b", fg="#ffffff",
                         font=(FONT, 9), padx=8, pady=4,
                         justify="left").pack()
                tip["win"] = w
            except tk.TclError as e:
                logger.warn(f"小鹅通 tooltip 显示失败：{e}")

        def _hide(_e=None):
            w = tip["win"]
            if w is not None:
                try:
                    w.destroy()
                except tk.TclError as e:
                    logger.warn(f"小鹅通 tooltip 关闭失败：{e}")
                tip["win"] = None

        widget.bind("<Enter>", _show, add="+")
        widget.bind("<Leave>", _hide, add="+")

    def _toggle_cookie_echo(self):
        try:
            self.ent_cookie.config(show="" if self.chk_show.get() else "•")
        except tk.TclError as e:
            logger.warn(f"小鹅通 Cookie 显示切换失败：{e}")

    def _tip_text_for_cell(self, iid, col) -> str:
        if self._tip_font is None:
            return ""
        try:
            idx = self._rows.get(iid)
            if idx is None or idx >= len(self.rows):
                return ""
            v = self.rows[idx]
            if col == 1:
                t = v.title or ""
            elif col == 5:
                t = self.tree.item(iid, "values")[5] or ""
            else:
                return ""
            if self._tip_font.measure(t) <= self.tree.column(col, "width") - 12:
                return ""
            return t
        except tk.TclError as e:
            logger.warn(f"小鹅通悬停提示取值失败：{e}")
            return ""

    def _dir(self) -> Path:
        try:
            base = Path(self.app.config.get("download_dir")
                        or Path.home() / "Downloads")
        except Exception as e:                     # noqa: BLE001
            logger.warn(f"小鹅通取下载目录失败，落默认目录：{e}")
            base = Path.home() / "Downloads"
        d = base / "小鹅通"
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warn(f"小鹅通创建下载目录失败 {d}: {e}")
        return d

    def _open_dir(self):
        import os
        import subprocess
        d = self._dir()
        try:
            if os.name == "nt":
                os.startfile(str(d))            # noqa: S606
            else:
                subprocess.Popen(["xdg-open", str(d)])   # noqa: S603,S607
        except Exception as e:                     # noqa: BLE001
            logger.warn(f"小鹅通打开目录失败：{e}")
            messagebox.showinfo("下载目录", str(d), parent=self.win)

    def _set_busy(self, busy: bool, stat: str = ""):
        self._busy = busy
        for b in (self.btn_parse, self.btn_dl, self.btn_clear):
            try:
                b.config(state=("disabled" if busy else "normal"))
            except tk.TclError as e:
                logger.warn(f"小鹅通按钮状态切换失败：{e}")
        try:
            self.btn_stop.config(state=("normal" if busy else "disabled"))
        except tk.TclError as e:
            logger.warn(f"小鹅通停止按钮切换失败：{e}")
        if stat:
            try:
                self.lbl_stat.config(text=stat)
            except tk.TclError as e:
                logger.warn(f"小鹅通状态栏更新失败：{e}")

    def _log(self, lv, msg):
        try:
            self.app.push_log(lv, msg)
        except Exception as e:                     # noqa: BLE001
            logger.warn(f"小鹅通写主日志失败：{e}")

    def _alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def _hide_window(self):
        try:
            self.win.withdraw()
        except tk.TclError as e:
            logger.warn(f"小鹅通窗口隐藏失败：{e}")

    def show(self):
        try:
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
        except tk.TclError as e:
            logger.warn(f"小鹅通窗口显示失败：{e}")

    # ────────────────── 勾选 ──────────────────
    def _on_click(self, evt):
        try:
            region = self.tree.identify("region", evt.x, evt.y)
            if region != "cell":
                return
            if self.tree.identify_column(evt.x) != "#1":
                return
            iid = self.tree.identify_row(evt.y)
            if not iid:
                return
            cur = self.tree.item(iid, "values")[0]
            self.tree.set(iid, "sel", "☐" if cur == "☑" else "☑")
        except tk.TclError as e:
            logger.warn(f"小鹅通勾选失败：{e}")

    def _toggle_all(self):
        kids = self.tree.get_children()
        if not kids:
            return
        all_on = all(self.tree.item(i, "values")[0] == "☑" for i in kids)
        for i in kids:
            self.tree.set(i, "sel", "☐" if all_on else "☑")

    # ────────────────── 清单持久化 ──────────────────
    def _mark_dirty(self):
        self._store_dirty = True
        if self._store_job is not None:
            return
        try:
            self._store_job = self.win.after(800, self._flush_store)
        except tk.TclError as e:
            logger.warn(f"小鹅通清单延迟保存调度失败：{e}")

    def _flush_store(self):
        self._store_job = None
        if self._store_dirty:
            self._write_store()

    def _write_store(self):
        from . import xiaoe_store
        rows = []
        for idx, v in enumerate(self.rows):
            m = self._meta.get(idx) or {}
            rows.append(xiaoe_store.video_to_entry(
                v, status=m.get("status") or "pending",
                file=m.get("file") or "", size=v.size or 0))
        xiaoe_store.save(rows)
        self._store_dirty = False

    def _restore_list(self):
        from . import xiaoe_store
        for e in xiaoe_store.load():
            v = xiaoe_store.entry_to_video(e)
            if v is None:
                continue
            idx = len(self.rows)
            self.rows.append(v)
            st = str(e.get("status") or "pending")
            f = str(e.get("file") or "")
            self._set_meta(idx, st, f)
            sel = "☐" if st == "done" else "☑"
            state_cn = {"done": "已完成", "failed": "失败",
                        "pending": "待下载"}.get(st, "待下载")
            iid = self.tree.insert("", "end", values=(
                sel, self._title_of(v), self._dur(v.duration),
                v.quality or "—", self._size(v.size), state_cn))
            self._rows[iid] = idx
        if self.rows:
            self.lbl_stat.config(text=f"已恢复上次清单：{len(self.rows)} 节")

    # ────────────────── 元信息 ──────────────────
    def _meta_of(self, idx) -> tuple:
        m = self._meta.setdefault(idx, {})
        return m.get("status") or "pending", m.get("file") or ""

    def _set_meta(self, idx, status=None, file=None):
        m = self._meta.setdefault(idx, {})
        if status is not None:
            m["status"] = status
        if file is not None:
            m["file"] = file

    @staticmethod
    def _title_of(v) -> str:
        return one_line(v.title or v.resource_id or "（无标题）")

    @staticmethod
    def _dur(sec: int) -> str:
        from .xiaoe import fmt_dur
        return fmt_dur(sec) if sec else "—"

    @staticmethod
    def _size(n: int) -> str:
        from .xiaoe import fmt_size
        return fmt_size(n) if n else "—"

    # ────────────────── 解析 ──────────────────
    def _cookie(self) -> str:
        try:
            return (self.var_cookie.get() or "").strip()
        except tk.TclError:
            return ""

    def _on_parse(self):
        if self._busy:
            return
        from .xiaoe import extract_links

        links = extract_links(self.txt.get("1.0", "end"))
        if not links:
            messagebox.showinfo(
                "没找到小鹅通链接",
                "请粘贴课程视频的完整链接，形如：\n"
                "https://appxxxx.h5.xiaoeknow.com/p/course/video/v_xxxx?product_id=p_xxxx\n\n"
                "★ 课程目录页（/p/course/column/...）解析不了，\n"
                "  请点进具体一节再复制地址。",
                parent=self.win)
            return

        for i in list(self.tree.get_children()):
            self.tree.delete(i)
        self._rows.clear()
        self.rows.clear()
        self._meta.clear()
        self._stop = False
        self._set_busy(True, f"正在解析 {len(links)} 个链接…")
        self.lbl_count.config(text="")
        cookie = self._cookie()

        def _worker():
            from .xiaoe import fetch_video, parse_target

            got = 0
            for raw in links:
                if self._stop:
                    break
                t = parse_target(raw)
                if t["kind"] != "video":
                    self.app.ui_q.put(
                        ("xiaoe_fail", raw,
                         "不是单节视频链接（可能是课程目录页）"))
                    continue
                res = fetch_video(t, cookie=cookie)
                if not res.get("ok"):
                    self.app.ui_q.put(("xiaoe_fail", raw,
                                       res.get("error") or "解析失败"))
                    continue
                got += 1
                self.app.ui_q.put(("xiaoe_video", res["video"]))
                time.sleep(random.uniform(0.5, 1.2))
            self.app.ui_q.put(("xiaoe_parse_done", got, len(links)))

        threading.Thread(target=_worker, daemon=True).start()

    def apply_video(self, video):
        """（主线程）插入一行。按 (appid, resource_id) 去重。"""
        if not self._alive():
            return
        have = {(v.appid, v.resource_id) for v in self.rows}
        if (video.appid, video.resource_id) in have:
            self._log("info", f"  已在清单里，跳过：{video.title}")
            return
        idx = len(self.rows)
        self.rows.append(video)
        self._set_meta(idx, "pending", "")
        iid = self.tree.insert("", "end", values=(
            "☑", self._title_of(video), self._dur(video.duration),
            video.quality or "—", "—", "待下载"))
        self._rows[iid] = idx
        self.lbl_count.config(text=f"{len(self.rows)} 节待下载")
        self._log("ok", f"解析到：{video.title}"
                        f"（{fmt_or_dash(video.quality)}，"
                        f"{self._dur(video.duration)}）")
        self._mark_dirty()

    def apply_fail(self, raw: str, err: str):
        if not self._alive():
            return
        self._log("warn", f"解析失败：{str(raw)[:56]} → {err}")

    def parse_done(self, got: int, total: int):
        if not self._alive():
            return
        self._set_busy(False, f"解析完成：{got}/{total} 个链接，"
                              f"共 {len(self.rows)} 节")
        if not got:
            messagebox.showwarning(
                "一个也没解析出来",
                "常见原因：\n"
                "  · 链接是课程目录页，不是具体一节\n"
                "  · 这节要付费/登录才能看 → 请在下面填 Cookie 再解析\n"
                "  · 课程已下架，或只在 App 内可看\n"
                "  · 网络问题\n\n"
                "日志里有每一条的具体错误。",
                parent=self.win)

    # ────────────────── 下载 ──────────────────
    def _picked(self) -> list:
        out = []
        for iid in self.tree.get_children():
            if self.tree.item(iid, "values")[0] != "☑":
                continue
            i = self._rows.get(iid)
            if i is None or i >= len(self.rows):
                continue
            out.append((iid, i, self.rows[i]))
        return out

    def _on_download(self):
        if self._busy:
            return
        picked = self._picked()
        if not picked:
            messagebox.showinfo("没勾选", "先勾选要下载的课程（点最左边的 ✓ 列）。",
                                parent=self.win)
            return
        try:
            workers = max(1, int(self.var_workers.get() or 8))
        except (TypeError, ValueError):
            workers = 8
        self._stop = False
        self._set_busy(True, f"开始下载 {len(picked)} 节…")
        self._log("info", f"小鹅通下载开始：{len(picked)} 节，"
                          f"{workers} 路并发，目录 {self._dir()}")

        out_dir = self._dir()
        cookie = self._cookie()

        def _worker():
            # ★ P42-②：线程必须自带兜底。这里任何一个没被 download_video
            #   兜住的异常都会让线程直接死掉，`xiaoe_all_done` 永不发送 →
            #   `_busy` 永远是 True，界面永久卡「下载中」且日志一片安静。
            #   所以：except 落日志，finally **无条件**补发结束事件。
            from .xiaoe import download_video

            done = failed = 0
            total = len(picked)
            try:
                for n, (iid, idx, v) in enumerate(picked, 1):
                    if self._stop:
                        break
                    self.app.ui_q.put(("xiaoe_dl_start", idx, n, total))
                    t0 = time.time()
                    last = [0.0]

                    def _prog(dseg, tseg, nbytes, _idx=idx, _n=n, _t0=t0):
                        now = time.time()
                        if now - last[0] < 0.25:
                            return
                        last[0] = now
                        self.app.ui_q.put(
                            ("xiaoe_dl_prog", _idx, dseg, tseg, nbytes, _n, total))

                    res = download_video(
                        v, out_dir,
                        cookie=cookie,
                        workers=workers,
                        on_progress=_prog,
                        should_stop=lambda: self._stop,
                        on_log=lambda lv, m: self._log(lv, m),
                    )
                    if res.get("ok"):
                        done += 1
                    else:
                        failed += 1
                    self.app.ui_q.put(("xiaoe_dl_done", idx, res, n, total))
                    if self._stop:
                        break
                    # 拟人间隔：连续下多节时别把 CDN 打急
                    if n < total:
                        time.sleep(random.uniform(1.0, 2.0))
            except Exception as e:             # noqa: BLE001
                logger.warn(f"小鹅通下载线程异常：{type(e).__name__}: {e}")
                self._log("error", f"下载线程异常：{type(e).__name__}: {e}")
            finally:
                self.app.ui_q.put(("xiaoe_all_done", done, failed, total))

        threading.Thread(target=_worker, daemon=True).start()

    def apply_dl_start(self, idx: int, n: int, total: int):
        if not self._alive():
            return
        try:
            iid = self._iid_of(idx)
            if iid:
                self.tree.set(iid, "status", f"下载中（{n}/{total}）")
                self.tree.see(iid)
        except tk.TclError as e:
            logger.warn(f"小鹅通下载开始状态刷新失败：{e}")
        self.lbl_stat.config(text=f"第 {n}/{total} 节：{self._title_of(self.rows[idx])}")

    def apply_dl_prog(self, idx: int, dseg: int, tseg: int,
                      nbytes: int, n: int, total: int):
        if not self._alive():
            return
        pct = int(dseg * 100 / max(1, tseg))
        try:
            self.pbar.config(value=pct)
        except tk.TclError as e:
            logger.warn(f"小鹅通进度条更新失败：{e}")
        from .xiaoe import fmt_size
        try:
            iid = self._iid_of(idx)
            if iid:
                self.tree.set(iid, "status",
                              f"{dseg}/{tseg} 片 · {fmt_size(nbytes)}")
                self.tree.set(iid, "size", fmt_size(nbytes))
        except tk.TclError as e:
            logger.warn(f"小鹅通分片进度刷新失败：{e}")

    def apply_dl_done(self, idx: int, res: dict, n: int, total: int):
        if not self._alive():
            return
        iid = self._iid_of(idx)
        ok = bool(res.get("ok"))
        from .xiaoe import fmt_size
        if ok:
            size = int(res.get("size") or 0)
            note = res.get("note") or ""
            txt = f"完成 {fmt_size(size)}" + ("（TS）" if res.get("ts_only") else "")
            self.rows[idx].size = size
            self._set_meta(idx, "done", Path(res.get("path") or "").name)
            if note:
                self._log("warn", f"  {note}")
        else:
            txt = f"失败：{str(res.get('error'))[:28]}"
            self._set_meta(idx, "failed", "")
        try:
            if iid:
                self.tree.set(iid, "status", txt)
                if ok:
                    self.tree.set(iid, "size", fmt_size(res.get("size") or 0))
                    self.tree.set(iid, "sel", "☐")
        except tk.TclError as e:
            logger.warn(f"小鹅通下载结果刷新失败：{e}")
        self._mark_dirty()

    def apply_all_done(self, done: int, failed: int, total: int):
        if not self._alive():
            return
        self._set_busy(False, f"全部结束：成功 {done}，失败 {failed}，共 {total}")
        self._log("ok" if not failed else "warn",
                  f"小鹅通下载结束：成功 {done}，失败 {failed}")
        if failed:
            messagebox.showwarning(
                "有下载失败",
                f"成功 {done} 节，失败 {failed} 节。\n\n"
                "失败的多半是：需要登录 Cookie、播放地址过期、或网络中断。\n"
                "断点续传：重新解析并下载同一条，会从已下好的分片接着来。",
                parent=self.win)

    def _on_stop(self):
        self._stop = True
        self.lbl_stat.config(text="正在停止…（分片已保留，可续传）")
        try:
            self.btn_stop.config(state="disabled")
        except tk.TclError as e:
            logger.warn(f"小鹅通停止按钮禁用失败：{e}")

    def _on_clear(self):
        if self._busy:
            return
        if not messagebox.askyesno("清空列表", "只清空界面清单，不删已下载的文件。",
                                   parent=self.win):
            return
        try:
            for i in list(self.tree.get_children()):
                self.tree.delete(i)
        except tk.TclError as e:
            logger.warn(f"小鹅通清空列表失败：{e}")
        self.rows.clear()
        self._rows.clear()
        self._meta.clear()
        self.lbl_count.config(text="")
        self.lbl_stat.config(text="就绪")
        from . import xiaoe_store
        xiaoe_store.clear()

    def _iid_of(self, idx: int):
        for iid, i in self._rows.items():
            if i == idx:
                return iid
        return None


def fmt_or_dash(s) -> str:
    return str(s) if s else "未知清晰度"
