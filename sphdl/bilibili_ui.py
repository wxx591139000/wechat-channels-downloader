"""B 站下载 GUI（单条链接 + 多P）。

跟 `douyin_ui.py` 的关系：同一套窗口骨架、同一套「后台线程干活 / 主线程
刷界面」的约定，但**刻意更简单** —— B 站不需要登录、不需要代理、不需要
证书，所以没有登录条、没有风控倒计时、没有主页批量。

设计取舍：
  · **独立窗口**，不改动视频号/公众号/抖音任何链路 —— 零回归风险
  · 下载到 `<下载目录>/B站/` 子目录，不跟视频号文件混在一起
  · 一行一个分P（一场比赛 8 个 P 就是 8 行），勾选粒度就是下载粒度
  · 已存在的文件直接跳过（不清空重下），配合断点续传
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


class BilibiliWindow:
    """B 站下载窗口。"""

    def __init__(self, app):
        self.app = app
        self.rows = []            # [(BiliVideo, BiliPart)]
        self._rows = {}           # iid -> index
        self._meta = {}           # idx -> {status, file}
        self._stop = False
        self._busy = False
        self._store_job = None
        self._store_dirty = False
        self.root = app.root

        self.win = tk.Toplevel(self.root)
        self.win.title("B 站视频下载")
        self.win.geometry("1000x730")
        self.win.minsize(840, 600)
        self.win.resizable(True, True)
        self._build()
        try:
            self._restore_list()
        except Exception as e:                     # noqa: BLE001
            logger.warn(f"B 站清单恢复失败（按空清单继续）：{type(e).__name__}: {e}")
        try:
            self.win.protocol("WM_DELETE_WINDOW", self._hide_window)
        except Exception as e:                     # noqa: BLE001
            logger.warn(f"B 站窗口关闭协议设置失败：{e}")

    # ────────────────── UI ──────────────────
    def _build(self):
        top = tk.Frame(self.win, bg="#0f172a", height=46)
        top.pack(fill="x")
        top.pack_propagate(False)
        tk.Label(top, text="  B 站视频下载", bg="#0f172a", fg="#ffffff",
                 font=(FONT, 13, "bold"), anchor="w").pack(side="left")

        def _btn(txt, cmd, tip):
            b = tk.Button(top, text=txt, command=cmd, bg="#1e293b",
                          fg="#e2e8f0", relief="flat", bd=0,
                          font=(FONT, 11, "bold"), padx=10, pady=0,
                          cursor="hand2", activebackground="#334155",
                          activeforeground="#ffffff", width=3)
            b.pack(side="right", padx=(0, 2), pady=6)
            self._tt(b, tip)
            return b

        _btn("✕", self._hide_window, "隐藏窗口（解析/下载会在后台继续）")
        self.btn_max = _btn("□", self._toggle_max, "最大化 / 还原窗口")
        _btn("—", self.win.iconify, "最小化到任务栏")
        # ★ 提示文字必须**最后** pack：pack(side="right") 是按调用顺序从右往左
        #   排的，先 pack 文字就会被推到最右，尾部直接被窗口边缘裁掉
        #   （2026-11-09 截图抓出来的，原来「不需要装证书 · 粘贴链接就能下」
        #    只剩半个「下」字，还压在 ✕ 右边）。
        tk.Label(top, text="免证书 · 直接粘贴就能下  ",
                 bg="#0f172a", fg="#94a3b8",
                 font=(FONT, 9)).pack(side="right", padx=(0, 4))

        # 提示条：把「能下多清」这句话摆在最显眼的位置，
        # 免得客户下完发现是 720P 来问（这不是 bug，是匿名上限）
        bar = tk.Frame(self.win, bg="#ecfeff", height=34)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        tk.Label(bar, text="免登录最高 720P（够看清，不是原画）。"
                           "1080P+/4K 需要 B 站登录态，本版没做。",
                 bg="#ecfeff", fg="#155e75", font=(FONT, 9),
                 anchor="w").pack(side="left", padx=14)

        pad = tk.Frame(self.win, bg="#ffffff")
        pad.pack(fill="both", expand=True)

        # ① 粘贴区
        tk.Label(pad, text="① 粘贴 B 站链接（BV 号 / 完整链接 / b23.tv 短链，一行一个）",
                 bg="#ffffff", font=(FONT, 10, "bold")).pack(
            anchor="w", padx=14, pady=(12, 4))
        self.txt = tk.Text(pad, height=5, wrap="word", font=(FONT, 10),
                           bg="#ffffff", relief="solid", bd=1)
        self.txt.pack(fill="x", padx=14)
        self.txt.insert(
            "1.0",
            "https://www.bilibili.com/video/BV1xxxxxxxxx\n"
            "短  链：https://b23.tv/xxxxxxx\n"
            "裸 BV 号：BV1xxxxxxxxx\n"
            "\n"
            "★ 多P 视频会把每个分P 各列一行，勾哪个下哪个\n"
            "★ 带 ?p=2 的链接会默认勾上第 2 个分P，其余照常列出")

        bar1 = tk.Frame(pad, bg="#ffffff")
        bar1.pack(fill="x", padx=14, pady=(8, 4))
        self.btn_parse = tk.Button(
            bar1, text="② 解析链接", command=self._on_parse,
            bg="#2563eb", fg="#ffffff", relief="flat",
            # ★ 解析/下载中会 disable，默认的灰字压在蓝底上几乎看不见
            #   （截图放大才发现），给个接近白的禁用色
            disabledforeground="#dbeafe",
            font=(FONT, 10, "bold"), padx=16, pady=6, cursor="hand2")
        self.btn_parse.pack(side="left")
        self.lbl_count = tk.Label(bar1, text="", bg="#ffffff",
                                  font=(FONT, 9), fg="#64748b")
        self.lbl_count.pack(side="left", padx=10)

        tk.Label(bar1, text="每条间隔", bg="#ffffff",
                 font=(FONT, 9), fg="#64748b").pack(side="left", padx=(6, 2))
        self.var_gap = tk.StringVar(value="2")
        tk.Spinbox(bar1, from_=0, to=30, width=4, textvariable=self.var_gap,
                   font=(FONT, 9)).pack(side="left")
        tk.Label(bar1, text="秒（别把 B 站打急）", bg="#ffffff",
                 font=(FONT, 9), fg="#64748b").pack(side="left")

        # ② 结果表
        tk.Label(pad, text="② 解析结果（勾选后下载）",
                 bg="#ffffff", font=(FONT, 10, "bold")).pack(
            anchor="w", padx=14, pady=(10, 4))

        cols = ("sel", "title", "owner", "dur", "quality", "size", "status")
        wrap = tk.Frame(pad, bg="#ffffff")
        wrap.pack(fill="both", expand=True, padx=14)
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings",
                                 height=11, selectmode="extended")
        heads = {"sel": ("✓", 34), "title": ("标题", 420),
                 "owner": ("UP 主", 136), "dur": ("时长", 74),
                 "quality": ("清晰度", 72), "size": ("大小", 82),
                 "status": ("状态", 168)}
        for c in cols:
            # 表头对齐跟数据对齐一致（v3.1.4 定的规矩）
            self.tree.heading(c, text=heads[c][0],
                              anchor=("center" if c in ("sel", "dur", "quality", "size")
                                      else "w"))
            self.tree.column(c, width=heads[c][1],
                             anchor="center" if c in ("sel", "dur", "quality", "size")
                             else "w",
                             stretch=(c == "title"))
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._on_click)

        # 长标题被列宽硬切时，悬停看全文（同主窗口判据：字体实测超宽才弹）
        try:
            from tkinter import font as tkfont

            from .gui import RowTip
            self._tip_font = tkfont.Font(family=FONT, size=9)
            self._rowtip = RowTip(self.tree, self._tip_text_for_cell)
        except Exception as e:                     # noqa: BLE001
            self._tip_font = None
            logger.warn(f"B 站列表悬停提示启用失败：{type(e).__name__}: {e}")

        # ③ 操作条
        bar2 = tk.Frame(pad, bg="#ffffff")
        bar2.pack(fill="x", padx=14, pady=(8, 4))
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

        self.pbar = ttk.Progressbar(pad, mode="determinate", maximum=100)
        self.pbar.pack(fill="x", padx=14, pady=(4, 2))
        self.lbl_stat = tk.Label(pad, text="就绪", bg="#ffffff", anchor="w",
                                 font=(FONT, 9), fg="#64748b")
        self.lbl_stat.pack(fill="x", padx=14, pady=(0, 8))

    # ────────────────── 内部工具 ──────────────────
    def _tt(self, widget, text: str):
        """简易 tooltip（跟主窗口一致的观感，不引第三方库）。"""
        tip = {"win": None}

        def show(_=None):
            if tip["win"] or not text:
                return
            try:
                x = widget.winfo_rootx() + 12
                y = widget.winfo_rooty() + widget.winfo_height() + 6
                w = tk.Toplevel(widget)
                w.wm_overrideredirect(True)
                w.wm_geometry(f"+{x}+{y}")
                tk.Label(w, text=text, justify="left", bg="#111827",
                         fg="#e5e7eb", font=(FONT, 9), padx=10, pady=6,
                         relief="solid", bd=1).pack()
                tip["win"] = w
            except Exception as e:                 # noqa: BLE001
                logger.warn(f"B 站 tooltip 显示失败：{e}")

        def hide(_=None):
            w = tip.get("win")
            if w is not None:
                try:
                    w.destroy()
                except Exception as e:             # noqa: BLE001
                    logger.warn(f"B 站 tooltip 关闭失败：{e}")
                tip["win"] = None

        widget.bind("<Enter>", show)
        widget.bind("<Leave>", hide)
        widget.bind("<ButtonPress>", hide)

    def _tip_text_for_cell(self, iid, col_0):
        if col_0 != 1:                       # 0=勾选列，1=标题列
            return ""
        try:
            vals = self.tree.item(iid, "values")
        except Exception as e:               # noqa: BLE001
            logger.warn(f"B 站悬停取行失败：{e}")
            return ""
        if len(vals) <= 1:
            return ""
        text = str(vals[1])
        if not text or getattr(self, "_tip_font", None) is None:
            return text
        try:
            avail = int(self.tree.column("title", "width"))
            need = self._tip_font.measure(text) + 12
        except Exception as e:               # noqa: BLE001
            logger.warn(f"B 站悬停测量失败：{e}")
            return text
        return text if need > avail else ""

    def _dir(self) -> Path:
        base = Path(self.app.dir_var.get() or
                    Path.home() / "B站下载")
        return base / "B站"

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
        except Exception as e:                     # noqa: BLE001
            logger.warn(f"B 站日志转发失败：{e}")

    def _alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except Exception:                          # noqa: BLE001
            return False

    def _hide_window(self):
        """隐藏窗口，不中断解析/下载（同抖音窗口的约定）。"""
        try:
            self.win.withdraw()
        except Exception as e:                     # noqa: BLE001
            logger.warn(f"B 站窗口隐藏失败：{e}")

    def show(self):
        try:
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
        except Exception as e:                     # noqa: BLE001
            logger.warn(f"B 站窗口唤出失败：{e}")

    def _toggle_max(self):
        try:
            if self.win.state() == "zoomed":
                self.win.state("normal")
                self.btn_max.config(text="□")
            else:
                self.win.state("zoomed")
                self.btn_max.config(text="❐")
        except Exception as e:                     # noqa: BLE001
            logger.warn(f"B 站窗口最大化切换失败：{e}")

    def _on_click(self, evt):
        """点「✓」列切换勾选。"""
        if self.tree.identify_region(evt.x, evt.y) != "cell":
            return
        if self.tree.identify_column(evt.x) != "#1":
            return
        iid = self.tree.identify_row(evt.y)
        if not iid:
            return
        vals = list(self.tree.item(iid, "values"))
        vals[0] = "☐" if vals[0] == "☑" else "☑"
        self.tree.item(iid, values=vals)

    def _toggle_all(self):
        items = self.tree.get_children()
        if not items:
            return
        want = "☑"
        if all(self.tree.item(i, "values")[0] == "☑" for i in items):
            want = "☐"
        for i in items:
            vals = list(self.tree.item(i, "values"))
            vals[0] = want
            self.tree.item(i, values=vals)

    # ── 清单持久化 ──
    def _mark_dirty(self):
        self._store_dirty = True
        if self._store_job is not None:
            return
        try:
            self._store_job = self.root.after(1200, self._flush_store)
        except Exception as e:                     # noqa: BLE001
            logger.warn(f"B 站清单延时保存安排失败：{e}")

    def _flush_store(self):
        self._store_job = None
        if not self._store_dirty:
            return
        self._store_dirty = False
        self._write_store()

    def _write_store(self):
        from . import bilibili_store

        entries = []
        for i, (v, p) in enumerate(self.rows):
            st, fp = self._meta_of(i)
            entries.append(bilibili_store.part_to_entry(
                v, p, status=st, file=fp, quality=p.quality, size=p.size))
        bilibili_store.save(entries)

    def _restore_list(self):
        """把上次解析的清单读回来铺进表格（客户：重开工具清单是空的）。"""
        from . import bilibili_store

        entries = bilibili_store.load()
        if not entries:
            return
        loaded = done = 0
        for e in entries:
            v, p = bilibili_store.entry_to_pair(e)
            if v is None or p is None:
                continue
            idx = len(self.rows)
            self.rows.append((v, p))
            st = e.get("status") or "pending"
            fp = e.get("file") or ""
            # 已下载但文件不见了 → 还原成待下载，别让客户以为下过了
            if st == "done" and fp and not Path(fp).exists():
                st = "pending"
                fp = ""
            self._meta[idx] = {"status": st, "file": fp}
            sel = "☐" if st == "done" else "☑"
            status = ("✅ 已下载" if st == "done"
                      else ("❌ 上次失败" if st == "failed" else "待下载"))
            iid = self.tree.insert("", "end", values=(
                sel, self._title_of(v, p), v.owner,
                self._dur(p.duration), "-",
                self._size(p.size), status))
            self._rows[iid] = idx
            loaded += 1
            if st == "done":
                done += 1
        if loaded:
            self.lbl_count.config(text=f"已恢复 {loaded} 个分P（{done} 个已下载）")
            self._log("info", f"已恢复上次解析的 B 站清单：{loaded} 个分P"
                              f"（其中 {done} 个已下载）")
            if done:
                self._log("info", "已下载的分P 默认不勾选；要重下请手动勾上。")

    def _meta_of(self, idx) -> tuple:
        m = self._meta.get(idx) or {}
        return (m.get("status") or "pending", m.get("file") or "")

    def _set_meta(self, idx, status=None, file=None):
        m = self._meta.setdefault(idx, {})
        if status is not None:
            m["status"] = status
        if file is not None:
            m["file"] = file

    # ── 展示用小工具 ──
    @staticmethod
    def _title_of(v, p) -> str:
        t = one_line(v.title or p.title or p.bvid)
        if len(v.parts) > 1:
            pt = one_line(p.title)
            if pt and pt != t:
                return f"{t} · P{p.index} {pt}"
            return f"{t} · P{p.index}"
        return t

    @staticmethod
    def _dur(sec: int) -> str:
        from .bilibili import fmt_dur
        return fmt_dur(sec) if sec else "—"

    @staticmethod
    def _size(n: int) -> str:
        from .bilibili import fmt_size
        return fmt_size(n) if n else "—"

    # ────────────────── 解析 ──────────────────
    def _on_parse(self):
        if self._busy:
            return
        from .bilibili import extract_links

        text = self.txt.get("1.0", "end")
        links = extract_links(text)
        if not links:
            messagebox.showinfo(
                "没找到 B 站链接",
                "请粘贴以下任意一种：\n"
                "  · 完整链接 https://www.bilibili.com/video/BV1xxxxxxx\n"
                "  · 短链 https://b23.tv/xxxxxxx\n"
                "  · 裸 BV 号 BV1xxxxxxx",
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

        def _worker():
            from .bilibili import fetch_view, parse_target, resolve_short

            got = 0
            for raw in links:
                if self._stop:
                    break
                t = parse_target(raw)
                if t["kind"] == "short":
                    self._log("info", f"短链解析：{raw}")
                    t2 = resolve_short(raw)
                    if not (t2.get("bvid") or t2.get("aid")):
                        self._log("warn", f"  短链没解析出来：{t2.get('error') or '未知'}")
                        self.app.ui_q.put(("bili_fail", raw,
                                           t2.get("error") or "短链无效"))
                        continue
                    t = t2
                if t["kind"] not in ("video", "short"):
                    self.app.ui_q.put(("bili_fail", raw, "不是 B 站视频链接"))
                    continue
                res = fetch_view(t.get("bvid") or "", t.get("aid") or 0)
                if not res.get("ok"):
                    self.app.ui_q.put(("bili_fail", raw,
                                       res.get("error") or "解析失败"))
                    continue
                got += 1
                self.app.ui_q.put(("bili_video", res["video"], t.get("page") or 0))
                # 拟人间隔：连续解析多个链接时别连打
                time.sleep(random.uniform(0.6, 1.4))
            self.app.ui_q.put(("bili_parse_done", got, len(links)))

        threading.Thread(target=_worker, daemon=True).start()

    def apply_video(self, video, page: int = 0):
        """（主线程）展开一个视频的全部分P。

        ★ 按 (bvid, cid) 去重：客户把同一个链接粘两次、或解析完再解析一遍，
          原来会把同一批分P 又插一遍（截图里出现过 12 行 = 6 行重复），
          下完第一遍第二遍靠「文件已存在」跳过，界面看着很乱。
        """
        if not self._alive():
            return
        have = {(v.bvid, p.cid) for v, p in self.rows}
        added = skipped = 0
        for p in video.parts:
            if (video.bvid, p.cid) in have:
                skipped += 1
                continue
            idx = len(self.rows)
            self.rows.append((video, p))
            self._set_meta(idx, "pending", "")
            # ?p=2 → 只勾第 2 个分P；其余默认勾（单P 也勾）
            if page:
                sel = "☑" if p.index == page else "☐"
            else:
                sel = "☑"
            iid = self.tree.insert("", "end", values=(
                sel, self._title_of(video, p), video.owner,
                self._dur(p.duration), "-", "-", "待下载"))
            self._rows[iid] = idx
            added += 1
        if skipped:
            self._log("info", f"  跳过 {skipped} 个重复分P（这份视频已经在清单里了）")
        self._log("ok", f"解析到：{video.title}（{video.owner}）"
                        f"{'，' + str(len(video.parts)) + ' 个分P' if video.multi else ''}")
        if added:
            self.lbl_count.config(
                text=f"{len(self.rows)} 个待下载（{len({id(v) for v, _ in self.rows})} 个视频）")
        self._mark_dirty()

    def apply_fail(self, raw: str, err: str):
        if not self._alive():
            return
        self._log("warn", f"解析失败：{raw[:48]} → {err}")

    def parse_done(self, got: int, total: int):
        if not self._alive():
            return
        self._set_busy(False, f"解析完成：{got}/{total} 个链接，共 {len(self.rows)} 个分P")
        if not got:
            messagebox.showwarning(
                "一个也没解析出来",
                "可能的原因为：\n"
                "  · 链接不是 B 站视频页（比如是空间、专栏、直播）\n"
                "  · 视频已删除 / 仅会员可见\n"
                "  · 网络问题\n\n"
                "日志里有每条的具体错误。",
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
            v, p = self.rows[i]
            out.append((i, v, p))
        return out

    def _on_download(self):
        if self._busy:
            return
        picked = self._picked()
        if not picked:
            messagebox.showinfo("没勾选", "先勾选要下载的分P（点最左边的 ✓ 列）。",
                                parent=self.win)
            return

        try:
            gap = max(0.0, float(self.var_gap.get() or 0))
        except (TypeError, ValueError):
            gap = 2.0
        self._stop = False
        self._set_busy(True, f"开始下载 {len(picked)} 个…")
        self._log("info", f"B 站下载开始：{len(picked)} 个分P，"
                          f"每条之间间隔 {gap:g} 秒，目录 {self._dir()}")

        out_dir = self._dir()

        def _worker():
            # ★ P42-②：异常必须落日志 + finally 无条件补发结束事件，
            #   否则线程静默死亡 → `_busy` 永远 True → 界面永久卡「下载中」。
            from .bilibili import download_part

            done = failed = 0
            total = len(picked)
            try:
                for n, (idx, v, p) in enumerate(picked, 1):
                    if self._stop:
                        break
                    self.app.ui_q.put(("bili_dl_start", idx, n, total))
                    last_put = [0.0]

                    def _prog(got, tot, _idx=idx, _n=n):
                        # 节流：256KB 一块，49MB 文件会有 200 次回调，
                        # 不节流会把主线程 UI 队列塞满
                        now = time.time()
                        if now - last_put[0] < 0.2:
                            return
                        last_put[0] = now
                        self.app.ui_q.put(("bili_dl_prog", _idx, got, tot, _n, total))

                    res = download_part(
                        v, p, out_dir,
                        on_progress=_prog,
                        should_stop=lambda: self._stop,
                        on_log=lambda lv, m: self._log(lv, m),
                        gap=0.0,             # 间隔统一在下面控制，避免双重等待
                    )
                    if res.get("ok"):
                        done += 1
                    elif "已停止" not in (res.get("error") or ""):
                        failed += 1
                    self.app.ui_q.put(("bili_dl_done", idx, res, n, total))
                    if self._stop:
                        break
                    if n < total and gap:
                        # 拟人间隔：随机抖动，别做成匀速机器
                        time.sleep(gap * random.uniform(0.8, 1.3))
            except Exception as e:             # noqa: BLE001
                logger.warn(f"B 站下载线程异常：{type(e).__name__}: {e}")
                self._log("error", f"下载线程异常：{type(e).__name__}: {e}")
            finally:
                self.app.ui_q.put(("bili_all_done", done, failed, total))

        threading.Thread(target=_worker, daemon=True).start()

    def apply_dl_start(self, idx: int, n: int, total: int):
        if not self._alive():
            return
        iid = self._iid_of(idx)
        if iid is None:
            return
        vals = list(self.tree.item(iid, "values"))
        vals[6] = "下载中…"
        self.tree.item(iid, values=vals)
        self.lbl_stat.config(text=f"下载中 {n}/{total}：{vals[1]}")
        self.pbar.configure(value=0)

    def apply_dl_prog(self, idx: int, got: int, tot: int, n: int, total: int):
        if not self._alive():
            return
        pct = int(got * 100 / tot) if tot else 0
        self.pbar.configure(value=max(0, min(100, pct)))
        iid = self._iid_of(idx)
        if iid is not None:
            vals = list(self.tree.item(iid, "values"))
            vals[6] = f"下载中 {pct}%"
            self.tree.item(iid, values=vals)
        self.lbl_stat.config(text=f"下载中 {n}/{total}  {pct}%")

    def apply_dl_done(self, idx: int, res: dict, n: int, total: int):
        if not self._alive():
            return
        iid = self._iid_of(idx)
        ok = bool(res.get("ok"))
        err = res.get("error") or ""
        if ok:
            self._set_meta(idx, "done", res.get("path") or "")
        else:
            self._set_meta(idx, "failed", res.get("path") or "")
        if iid is not None:
            vals = list(self.tree.item(iid, "values"))
            qn = res.get("quality_name") or ""
            if ok:
                if res.get("skipped"):
                    vals[6] = "✅ 已存在，跳过"
                else:
                    vals[6] = f"✅ 完成 {res.get('size', 0) / 1048576:.1f} MB"
                if qn:
                    vals[4] = qn
                if res.get("size"):
                    vals[5] = f"{res['size'] / 1048576:.1f} MB"
            elif "已停止" in err:
                vals[6] = "⏸ 已停止（断点已留）"
            else:
                # ★ 只留一句短的：完整错误已经进日志了。
                #   Treeview **不会**把文字裁到列宽里，写长了会横穿整张表
                #   （2026-11-09 截图抓出来的：40 字版本直接画到窗口外面）。
                #   10 字 ≈ 110px，168px 的列装得下，还留一点余量。
                short = err if len(err) <= 10 else err[:9] + "…"
                vals[6] = f"❌ {short}"
            self.tree.item(iid, values=vals)
        if ok:
            self._log("ok", f"  ✅ {self.rows[idx][0].title[:24]} "
                            f"P{self.rows[idx][1].index} 完成")
        elif "已停止" in err:
            self._log("info", f"  已停止，断点保留：{Path(res.get('path') or '').name}")
        else:
            self._log("warn", f"  ❌ P{self.rows[idx][1].index} 失败：{err}")
        self._mark_dirty()

    def apply_all_done(self, done: int, failed: int, total: int):
        if not self._alive():
            return
        self._set_busy(False, f"下载结束：成功 {done}，失败 {failed}，共 {total}")
        self.pbar.configure(value=100 if done and not failed else 0)
        if self._stop:
            self._log("info", f"B 站下载已停止（完成 {done}/{total}）；"
                              f"未完成的分P 下次会从断点继续")
        elif failed:
            self._log("warn", f"B 站下载结束：{done} 成功，{failed} 失败")
        else:
            self._log("ok", f"B 站下载全部完成：{done} 个分P → {self._dir()}")
        self._mark_dirty()

    def _iid_of(self, idx: int):
        for iid, i in self._rows.items():
            if i == idx:
                return iid
        return None

    def _on_stop(self):
        self._stop = True
        self.btn_stop.config(state="disabled")
        self.lbl_stat.config(text="正在停止…（当前文件下完这一块就停）")
