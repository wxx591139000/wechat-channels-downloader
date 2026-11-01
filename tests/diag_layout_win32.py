"""用 Win32 PrintWindow 截 Tk 窗口（ImageGrab 在此环境截不到 Tk 内容）。

验证：三种布局下，日志区（深色 #0b1020）是否真的被绘制出来。
"""
import ctypes
import ctypes.wintypes as wt
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32


def grab_window(hwnd, path):
    """用 PrintWindow(hwnd, dc, PW_RENDERFULLCONTENT) 截窗口。"""
    rect = wt.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return None

    hwnd_dc = _user32.GetWindowDC(hwnd)
    mfc_dc = _gdi32.CreateCompatibleDC(hwnd_dc)
    bmp = _gdi32.CreateCompatibleBitmap(hwnd_dc, w, h)
    _gdi32.SelectObject(mfc_dc, bmp)

    # PW_RENDERFULLCONTENT = 2
    ok = _user32.PrintWindow(hwnd, mfc_dc, 2)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG),
                    ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                    ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                    ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                    ("biClrImportant", wt.DWORD)]

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = w
    bi.biHeight = -h          # top-down
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0

    buf = ctypes.create_string_buffer(w * h * 4)
    _gdi32.GetDIBits(mfc_dc, bmp, 0, h, buf, ctypes.byref(bi), 0)

    _gdi32.DeleteObject(bmp)
    _gdi32.DeleteDC(mfc_dc)
    _user32.ReleaseDC(hwnd, hwnd_dc)

    try:
        from PIL import Image
        im = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1)
        im.convert("RGB").save(path)
        return path, ok
    except Exception as e:
        print(f"  保存失败: {e}")
        return None


def dark_ratio(path):
    from PIL import Image
    im = Image.open(path).convert("RGB")
    w, h = im.size
    px = im.load()
    top = int(h * 0.5)
    dark = tot = 0
    for y in range(top, h, 2):
        for x in range(0, w, 4):
            r, g, b = px[x, y]
            tot += 1
            if r < 60 and g < 60 and b < 90:
                dark += 1
    return dark / max(tot, 1)


def build(mode):
    root = tk.Tk()
    root.title(f"logtest-{mode}")
    root.geometry("1100x740+100+100")
    root.configure(bg="#f6f8fa")

    st = ttk.Style()
    if st.theme_use() in ("vista", "xpnative", "winnative"):
        st.theme_use("clam")

    main = ttk.Frame(root)
    main.pack(fill="both", expand=True, padx=8, pady=8)

    if mode == "A":
        list_frame = ttk.LabelFrame(main, text=" 视频列表 ")
        list_frame.pack(fill="both", expand=True, pady=8)
        logf = ttk.LabelFrame(main, text=" 日志 ", padding=4)
        logf.pack(fill="both", expand=True, pady=(0, 0))
    elif mode == "B":
        logf = ttk.LabelFrame(main, text=" 日志 ", padding=4)
        logf.pack(side="bottom", fill="x", pady=(0, 0))
        list_frame = ttk.LabelFrame(main, text=" 视频列表 ")
        list_frame.pack(fill="both", expand=True, pady=8)
    else:
        main.rowconfigure(0, weight=3)
        main.rowconfigure(1, weight=1)
        main.columnconfigure(0, weight=1)
        list_frame = ttk.LabelFrame(main, text=" 视频列表 ")
        list_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 6))
        logf = ttk.LabelFrame(main, text=" 日志 ", padding=4)
        logf.grid(row=1, column=0, sticky="nsew")

    tree = ttk.Treeview(list_frame, columns=("a",), show="headings", height=8)
    tree.heading("a", text="标题")
    for i in range(15):
        tree.insert("", "end", values=(f"视频 {i}",))
    tree.pack(fill="both", expand=True)

    log_text = tk.Text(logf, height=8, bg="#0b1020", fg="#d1d5db",
                       font=("Consolas", 9), wrap="word")
    log_text.pack(fill="both", expand=True)
    for i in range(10):
        log_text.insert("end", f"[{i:02d}] 日志内容测试行 —— 可见性验证\n")

    root.update()
    root.update_idletasks()
    root.lift()
    root.attributes("-topmost", True)
    root.update()
    time.sleep(1.0)

    hwnd = int(root.frame(), 16) if isinstance(root.frame(), str) else root.winfo_id()
    # 取顶层窗口
    hwnd = _user32.GetAncestor(root.winfo_id(), 2)   # GA_ROOT

    geo = {"log_h": logf.winfo_height(), "text_h": log_text.winfo_height(),
           "list_h": list_frame.winfo_height()}

    shot = Path(f"C:/Users/Dancing/AppData/Local/Temp/logtest_{mode}.png")
    r = grab_window(hwnd, shot)
    root.destroy()
    return geo, shot, (r[1] if r else False)


print("=" * 74)
print("Win32 PrintWindow 截取 Tk 窗口，验证日志区是否真的绘制")
print("=" * 74)

best = None
for mode in ("A", "B", "C"):
    geo, shot, ok = build(mode)
    dr = dark_ratio(shot) if shot.exists() else 0
    print(f"\n【{mode}】PrintWindow={'OK' if ok else 'FAIL'}")
    print(f"  geometry: 日志 {geo['log_h']}px 文本 {geo['text_h']}px "
          f"列表 {geo['list_h']}px")
    print(f"  ★深色占比 {dr*100:.1f}%   {shot}")
    if dr > 0.05:
        print(f"  ✅ 日志区真的画出来了")
        if best is None or dr > best[1]:
            best = (mode, dr)
    else:
        print(f"  ❌ 没画出来")

print()
print("=" * 74)
if best:
    print(f"→ 可行方案: {best[0]}（深色占比 {best[1]*100:.1f}%）")
else:
    print("→ 三种都不理想")
