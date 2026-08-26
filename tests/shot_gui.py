"""Screenshot the sphdl GUI window via PrintWindow (works even if not foreground)."""
import sys, time, ctypes
import win32gui, win32ui, win32con
from PIL import Image  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else r"E:\myHermesWorkSpace\wechat-channels-downloader\docs\screenshots\main.png"

def find_sphdl():
    res = []
    def cb(hwnd, _):
        try:
            if win32gui.IsWindowVisible(hwnd):
                t = win32gui.GetWindowText(hwnd)
                if "微信视频号下载器" in t:
                    res.append((hwnd, t))
        except Exception:
            pass
        return True
    win32gui.EnumWindows(cb, None)
    return res

def shoot(hwnd, path):
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w, h = r - l, b - t
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, w, h)
    save_dc.SelectObject(bmp)
    ok = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2)  # PW_RENDERFULLCONTENT
    # 读 DIB 位（32bpp BGRA，DIB section 自上而下）→ Pillow 存 PNG
    bits = save_dc.GetBitmapBits(w * h * 4)
    img = Image.frombuffer("RGB", (w, h), bits, "raw", "BGRX", 0, 1)
    img.save(path)
    save_dc.Detach()
    mfc_dc.DeleteDC()
    win32gui.DeleteObject(bmp.GetHandle())
    win32gui.ReleaseDC(hwnd, hwnd_dc)
    return ok, w, h

# wait for window up to 25s
found = None
for _ in range(50):
    found = find_sphdl()
    if found:
        break
    time.sleep(0.5)
if not found:
    print("NOT_FOUND")
    sys.exit(1)
hwnd, title = found[0]
print("FOUND:", title)
time.sleep(1.5)  # let tkinter settle
ok, w, h = shoot(hwnd, OUT)
print(f"saved {OUT} ok={ok} size={w}x{h}")
