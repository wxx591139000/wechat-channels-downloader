"""小程序「选集」按钮自动点击（v2.9.0）——★ 实验性模块，未接入界面 ★

╔══════════════════════════════════════════════════════════════════════╗
║ 状态：**未交付、未接入 UI**。保留代码是因为下面两块能力已验证可用：   ║
║   · WGC 截图（能拿到 Chromium 真实像素）                ✅          ║
║   · 两点标定（帧坐标→客户区坐标，误差 0px）              ✅          ║
║ 卡点：**最后一步"真实点击"在测试环境无法完成** ——                     ║
║   SetForegroundWindow 被 Windows 前台锁定拒绝，窗口抢不到前台时       ║
║   SendInput 的鼠标事件会落到别的窗口（实测坐标偏 606px）。             ║
║   这是系统为防止恶意软件打扰用户而**故意设计**的限制，无法绕过。       ║
║                                                                      ║
║ 因此产品的实际方案是：**逐个下载 + 依赖小程序自身的自动连播**，       ║
║ 详见使用说明「🎬 小程序短剧怎么下」一节。                             ║
║                                                                      ║
║ 将来若要重启此方案（例如客户端内由工具自己抢前台），本模块可直接复用。 ║
╚══════════════════════════════════════════════════════════════════════╝


═══ 客户需求 ═══
「之前说的工具自动点选集进下一集实现没」

═══ ★ 为什么最终用 WGC（Windows Graphics Capture）★ ═══
前两套截图方案实测都不可靠：

| 方案 | 实测问题 |
|---|---|
| PrintWindow(hwnd, dc, 2) | **截不到 Chromium 页面内容**（只有背景色+界面杂色） |
| BitBlt 窗口DC | 截到**全黑**（DWM 合成窗口拿不到） |
| BitBlt 桌面DC | 源坐标错→截到**完全无关的窗口**（截进了一个 Excel） |
| **WGC** | ✅ **能拿到真实页面像素**，不受遮挡影响 |

═══ ★ 坐标换算：必须两点标定 ★ ═══
WGC 帧尺寸与窗口尺寸不一致（实测 1970×1645 帧 vs 896×768 窗口），
存在**缩放 + 平移**。实测数据：
```
cal1 帧内(120.9, 268.5)  DOM(55, 55)
cal2 帧内(1432.7,1183.0) DOM(715,515)
→ 缩放 0.5032/0.5030   平移 (-5.9, -80.1)
→ 按钮 帧(706,600) → 客户区(349,221)  真值(348,220)  误差 1.1px ✅
```
所以必须用两个已知颜色的标定块求变换，**不能假设只是平移**。

═══ 诚实说明局限 ═══
· 需要先用「标定」：在短剧播放页上，工具引导用户放到指定位置（或自动识别）
· 小程序改版/换剧/窗口尺寸变化都可能需要重新标定
· 播放器控件自动隐藏时找不到按钮 → 如实报告，不瞎点
· 连续 3 次找不到自动停（避免点到广告/付费）
· WGC 需要 Win10 1903+（本机 Win11 26200 ✅）

═══ 依赖 ═══
windows-capture（纯 WinRT 封装，含 numpy/cv2 依赖，约 +15MB 包体）
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time
from pathlib import Path

from .logger import logger

user32 = ctypes.windll.user32
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


# ══════════════════ WGC 截图 ══════════════════
def grab_wgc(hwnd, wait: float = 3.0):
    """用 WGC 截取窗口当前画面，返回 PIL.Image 或 None。

    WGC 走 DWM 合成层，**不受遮挡影响、能拿到 Chromium 真实内容**。
    （PrintWindow/BitBlt 实测都拿不到，见模块注释）
    """
    try:
        import windows_capture as WC
    except ImportError:
        logger.warn("缺少 windows-capture 依赖，自动选集不可用")
        return None
    try:
        got = {}
        cap = WC.WindowsCapture(window_hwnd=hwnd, cursor_capture=False,
                                draw_border=False)

        @cap.event
        def on_frame_arrived(frame, cc):
            if "buf" not in got:
                got["buf"] = frame.frame_buffer
            try:
                cc.stop()
            except Exception:
                pass

        @cap.event
        def on_closed():
            pass

        cap.start_free_threaded()
        t0 = time.time()
        while time.time() - t0 < wait:
            if "buf" in got:
                break
            time.sleep(0.05)

        if "buf" not in got:
            logger.debug("WGC 没收到帧")
            return None

        import numpy as np
        from PIL import Image
        arr = np.asarray(got["buf"])
        if arr.ndim == 3 and arr.shape[2] == 4:
            arr = arr[:, :, :3]
        return Image.fromarray(arr[:, :, ::-1].copy())    # BGR → RGB
    except Exception as e:                                # noqa: BLE001
        logger.warn(f"WGC 截图失败: {type(e).__name__}: {e}")
        return None


# ══════════════════ 颜色定位 ══════════════════
def find_color_centroid(img, color, tol: int = 45, step: int = 1):
    """找指定颜色的重心，返回 (cx, cy, count) 或 None。"""
    try:
        px = img.load()
        w, h = img.size
        mr, mg, mb = color
        xs, ys = [], []
        for y in range(0, h, step):
            for x in range(0, w, step):
                r, g, b = px[x, y][:3]
                if (abs(r - mr) < tol and abs(g - mg) < tol
                        and abs(b - mb) < tol):
                    xs.append(x)
                    ys.append(y)
        if not xs:
            return None
        return (sum(xs) / len(xs), sum(ys) / len(ys), len(xs))
    except Exception:
        return None


# ══════════════════ 模板匹配（纯 PIL，零额外依赖） ══════════════════
def find_template(img, tpl, tol: int = 24, step: int = 3):
    """在 img 里找 tpl，返回 (cx, cy, score) 或 None。

    两级：粗筛（特征点+步长）→ 精修（逐像素，半径足够大）。
    ★ 精修半径必须 >= 粗筛步长的 5 倍：得分面在纯色区域有"平台"，
      粗筛可能停在平台边缘，实测 co=3 时需 rad>=15。
    """
    try:
        iw, ih = img.size
        tw, th = tpl.size
        if tw > iw or th > ih:
            return None
        px_i = img.load()
        px_t = tpl.load()

        pts = []
        for fx in (1, tw // 2, tw - 2):
            for fy in (1, th // 2, th - 2):
                pts.append((fx, fy))
        for fx in (tw // 4, 3 * tw // 4):
            for fy in (th // 4, 3 * th // 4):
                pts.append((fx, fy))

        def _score(ox, oy):
            d = 0
            for (tx, ty) in pts:
                a = px_i[ox + tx, oy + ty]
                b = px_t[tx, ty]
                d += abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])
            return d / (len(pts) * 3)

        co = max(2, step)
        best, best_score = None, 1e9
        for oy in range(0, ih - th + 1, co):
            for ox in range(0, iw - tw + 1, co):
                sc = _score(ox, oy)
                if sc < best_score:
                    best_score, best = sc, (ox, oy)
        if best is None:
            return None

        bx, by = best
        rad = max(co * 5, 15)
        fine_best, fine_score = None, 1e9
        for oy in range(max(0, by - rad), min(ih - th, by + rad) + 1):
            for ox in range(max(0, bx - rad), min(iw - tw, bx + rad) + 1):
                sc = _score(ox, oy)
                if sc < fine_score:
                    fine_score, fine_best = sc, (ox, oy)
        if fine_best is None or fine_score > tol:
            return None
        return (fine_best[0] + tw // 2, fine_best[1] + th // 2, fine_score)
    except Exception as e:                                # noqa: BLE001
        logger.debug(f"模板匹配失败: {e}")
        return None


# ══════════════════ 标定 ══════════════════
def calibrate(hwnd, img, markers):
    """两点标定：求「WGC 帧坐标 → 客户区坐标」的缩放+平移。

    markers: [(dom_x, dom_y, (r,g,b)), ...] 至少 2 个
             dom_x/dom_y 是标记在**客户区**（≈DOM 视口）里的中心坐标，
             (r,g,b) 是它在画面上的颜色。

    返回 {"sx","sy","ox","oy"} 或 None。
    换算：客户区坐标 = 帧坐标 * s + o
    """
    try:
        if len(markers) < 2:
            return None
        found = []
        for (dx, dy, color) in markers:
            c = find_color_centroid(img, color, tol=55)
            if c is None:
                logger.debug(f"标定块 {color} 未找到")
                return None
            found.append((c[0], c[1], dx, dy))
        (ix1, iy1, dx1, dy1), (ix2, iy2, dx2, dy2) = found[0], found[1]
        if abs(ix2 - ix1) < 5 or abs(iy2 - iy1) < 5:
            return None
        sx = (dx2 - dx1) / (ix2 - ix1)
        sy = (dy2 - dy1) / (iy2 - iy1)
        if sx <= 0 or sy <= 0:
            return None
        ox = dx1 - ix1 * sx
        oy = dy1 - iy1 * sy
        return {"sx": sx, "sy": sy, "ox": ox, "oy": oy,
                "n": len(found)}
    except Exception as e:                                # noqa: BLE001
        logger.debug(f"标定失败: {e}")
        return None


def calib_to_client(img_x, img_y, calib):
    """帧坐标 → 客户区坐标。"""
    if not calib:
        return None
    return (img_x * calib["sx"] + calib["ox"],
            img_y * calib["sy"] + calib["oy"])


def client_to_screen(hwnd, x, y):
    try:
        import win32gui
        return win32gui.ClientToScreen(hwnd, (int(x), int(y)))
    except Exception:
        return (int(x), int(y))


# ══════════════════ 点击 ══════════════════
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wt.ULONG))]


class INPUT(ctypes.Structure):
    class _I(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]
    _anonymous_ = ("i",)
    _fields_ = [("type", wt.DWORD), ("i", _I)]


def click_at(screen_x: int, screen_y: int) -> bool:
    """真实鼠标左键点击（SendInput；PostMessage 会被小程序忽略）。

    带光标到位校验 + mouse_event 兜底。
    """
    try:
        sx, sy = int(screen_x), int(screen_y)
        ok_pos = False
        for _ in range(3):
            user32.SetCursorPos(sx, sy)
            time.sleep(0.05)
            pt = wt.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            if abs(pt.x - sx) <= 1 and abs(pt.y - sy) <= 1:
                ok_pos = True
                break
        if not ok_pos:
            logger.warn(f"光标无法移动到 ({sx},{sy})")
            return False

        def _send(flags):
            inp = INPUT()
            inp.type = 0
            inp.mi.dx = 0
            inp.mi.dy = 0
            inp.mi.mouseData = 0
            inp.mi.dwFlags = flags
            inp.mi.time = 0
            inp.mi.dwExtraInfo = None
            return user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

        n1 = _send(MOUSEEVENTF_LEFTDOWN)
        time.sleep(0.06)
        n2 = _send(MOUSEEVENTF_LEFTUP)
        if n1 and n2:
            return True
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.06)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        return True
    except Exception as e:                                # noqa: BLE001
        logger.warn(f"点击失败: {e}")
        return False


# ══════════════════ 模板持久化 ══════════════════
def template_path(base_dir) -> Path:
    return Path(base_dir) / "小程序短剧-选集按钮.png"


def save_template(base_dir, img, box) -> bool:
    try:
        tpl = img.crop(box)
        p = template_path(base_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        tpl.save(p)
        logger.ok(f"✅ 选集按钮模板已保存（{tpl.size[0]}×{tpl.size[1]}）")
        return True
    except Exception as e:                                # noqa: BLE001
        logger.warn(f"保存模板失败: {e}")
        return False


def load_template(base_dir):
    try:
        from PIL import Image
        p = template_path(base_dir)
        if not p.exists():
            return None
        return Image.open(p).convert("RGB")
    except Exception:
        return None


def calib_path(base_dir) -> Path:
    return Path(base_dir) / "小程序短剧-标定.json"


def save_calib(base_dir, calib) -> bool:
    try:
        import json
        p = calib_path(base_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(calib, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception:
        return False


def load_calib(base_dir):
    try:
        import json
        p = calib_path(base_dir)
        if not p.exists():
            return None
        d = json.loads(p.read_text(encoding="utf-8"))
        if all(k in d for k in ("sx", "sy", "ox", "oy")):
            return d
    except Exception:
        pass
    return None
