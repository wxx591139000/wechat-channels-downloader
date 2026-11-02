"""探针：把真实 GUI 的控件树几何打出来（不用猜、不看截图猜）。

为什么需要：截图只能看出"下载目录那行不见了"，看不出是
被裁掉、还是根本没 pack、还是被压缩到 0 高度。
这个脚本直接读 Tk 的 winfo_*，给的是硬数据。

用法：python tests/probe_layout_geom.py [关键字...]
   默认打印「捕获与下载」区块及所有含"目录/上限"文字的控件。
"""
import sys
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_created = {}
_OrigTk = tk.Tk


class _Tk(_OrigTk):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        _created["root"] = self
        self.after(7000, _dump_and_quit)


def _text_of(w):
    try:
        if isinstance(w, (tk.Label, tk.Button, tk.Checkbutton)):
            return str(w.cget("text"))
        from tkinter import ttk
        if isinstance(w, (ttk.Label, ttk.Button, ttk.Checkbutton,
                          ttk.LabelFrame, ttk.Entry, ttk.Treeview)):
            try:
                return str(w.cget("text"))
            except Exception:
                return ""
    except Exception:
        pass
    return ""


def _walk(w, depth=0, out=None):
    out = out if out is not None else []
    try:
        x = w.winfo_rootx() - _created["root"].winfo_rootx()
        y = w.winfo_rooty() - _created["root"].winfo_rooty()
        out.append((depth, w.winfo_class(), _text_of(w)[:34],
                    x, y, w.winfo_width(), w.winfo_height(),
                    w.winfo_manager()))
    except Exception:
        pass
    for c in w.winfo_children():
        _walk(c, depth + 1, out)
    return out


def _dump_and_quit():
    root = _created["root"]
    rows = _walk(root)
    print("=" * 96)
    print(f"{'cls':14s} {'text':36s} {'x':>5s} {'y':>5s} {'w':>5s} {'h':>5s}  mgr")
    print("=" * 96)
    for d, cls, txt, x, y, w, h, mgr in rows:
        print(f"{'  ' * min(d, 6)}{cls:14s} {txt:36s} {x:5d} {y:5d} "
              f"{w:5d} {h:5d}  {mgr}")
    print("=" * 96)
    root.destroy()


tk.Tk = _Tk
import sphdl.gui as G   # noqa: E402

G.main()
