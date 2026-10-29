"""自动翻页抓取（v2.7.0）。

═══ 客户需求（2026-10-29）═══
「林萃心屿」直播回放有几百个，一页 8 条。v2.6.6 已做到「整页全抓」，
但仍需人工往下滑 31 次。客户要「自动翻页」。

═══ 为什么用滚轮而不是调接口 ═══
| 方案 | 说明 | 判断 |
|---|---|---|
| 注入 JS 调分页接口 | 要复刻 `req_json` + 签名参数 + last_buffer 游标 | ❌ 参数结构复杂，改一次接口就废 |
| 模拟滚轮 | 就是让页面自己加载下一页，走它原本的链路 | ✅ 接口怎么变都不影响 |

**核心思路：不碰接口，只帮用户「滑」。** 页面自己会请求下一页，
我们的捕获逻辑（v2.6.6 整页全抓）自然把新数据吃进来。

═══ 关键实现点 ═══
1. **目标是 Chromium 内核窗口**：视频号跑在 `WeChatAppEx.exe`（Chromium），
   PostMessage 合成滚轮常被忽略 → 用 `SendInput` 发**真实鼠标滚轮**，
   并把光标临时移到窗口中心（结束后还原位置）。
2. **到位判定**：连续 N 轮没有新增捕获 = 到底了（或列表不再加载）。
   这比"数固定次数"可靠 —— 245 条可能 31 页，也可能因为分页大小变化而不同。
3. **可中断**：随时 stop，线程 daemon，不阻塞退出。
4. **可测试**：滚动函数和计数函数都是注入的，单测不依赖真实窗口。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading
import time

from .logger import logger

# ── Win32 ──────────────────────────────────────────────────────────
user32 = ctypes.windll.user32
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG),
                ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(wt.ULONG))]


class INPUT(ctypes.Structure):
    class _I(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]
    _anonymous_ = ("i",)
    _fields_ = [("type", wt.DWORD), ("i", _I)]


def get_cursor_pos():
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def set_cursor_pos(x, y):
    user32.SetCursorPos(int(x), int(y))


def send_wheel(delta: int = -WHEEL_DELTA):
    """发一次真实滚轮事件（负=向下）。"""
    inp = INPUT()
    inp.type = 0                      # INPUT_MOUSE
    inp.mi.dx = 0
    inp.mi.dy = 0
    inp.mi.mouseData = ctypes.c_ulong(delta & 0xFFFFFFFF).value
    inp.mi.dwFlags = MOUSEEVENTF_WHEEL
    inp.mi.time = 0
    inp.mi.dwExtraInfo = None
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


# ── 找视频号窗口 ────────────────────────────────────────────────────
# 视频号在 PC 微信里跑在独立进程（Chromium 内核）：
#   微信 3.x → WeChatAppEx.exe
#   微信 4.x → Weixin.exe（也带 AppEx 子进程）
TARGET_PROCS = ("wechatappex.exe", "weixin.exe", "wechat.exe")


def _proc_name(pid: int) -> str:
    """取进程可执行文件名（小写）。

    ★ 不引入 psutil（项目 venv 里没有，打包会漏依赖）。
      优先用 pywin32（项目本来就依赖），失败再退回内核 API。
    """
    # ① pywin32：GetModuleFileNameEx
    try:
        import win32api
        import win32process
        h = win32api.OpenProcess(0x1000, False, pid)     # QUERY_LIMITED_INFORMATION
        if h:
            try:
                path = win32process.GetModuleFileNameEx(h, 0)
                return path.rsplit("\\", 1)[-1].lower()
            finally:
                try:
                    win32api.CloseHandle(h)
                except Exception:
                    pass
    except Exception:
        pass
    # ② 退回 ctypes QueryFullProcessImageNameW
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return buf.value.rsplit("\\", 1)[-1].lower()
        finally:
            k32.CloseHandle(h)
    except Exception:
        pass
    return ""


def find_video_window():
    """找视频号窗口，返回 (hwnd, rect) 或 (None, None)。

    判据（宁可找不到也不要滚错窗口）：
      · 进程名在 TARGET_PROCS 里
      · 窗口可见、标题非空
      · 尺寸 ≥ 500x400（排除 157x25 那种隐藏/托盘窗）
      · 类名含 Chrome_WidgetWin
    """
    import win32gui
    import win32process

    cands = []

    def _cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True
        cls = win32gui.GetClassName(hwnd)
        if "Chrome_WidgetWin" not in cls:
            return True
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return True
        if _proc_name(pid) not in TARGET_PROCS:
            return True
        r = win32gui.GetWindowRect(hwnd)
        w, h = r[2] - r[0], r[3] - r[1]
        if w < 500 or h < 400:
            return True
        cands.append((hwnd, r, w * h, title))
        return True

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        return None, None
    if not cands:
        return None, None

    # ★ 评分：标题像"视频号"的优先，其次看面积。
    #   为什么不能只看面积：微信主窗口也是 Chrome_WidgetWin + 同样进程、
    #   面积更大；滚错窗口会把聊天列表滚走，客户会以为工具在乱搞。
    def _score(item):
        hwnd, r, area, title = item
        t = title.lower()
        bonus = 0
        for kw in ("视频号", "直播", "finder"):
            if kw in t:
                bonus = 10_000_000
                break
        return bonus + area

    cands.sort(key=_score, reverse=True)
    hwnd, r, _, title = cands[0]
    if not any(k in title for k in ("视频号", "直播", "finder")):
        logger.warn("找到的窗口标题不像视频号（" + title[:20] +
                    "），若滚动无效请先单独打开「微信视频号」窗口")
    return hwnd, r


class AutoPager(threading.Thread):
    """自动翻页线程。

    参数全部可注入，便于单测：
      scroll_fn()      → 执行一次滚动
      count_fn()       → 返回当前已捕获条数（用于判"到底"）
      find_fn()        → 返回 (hwnd, rect)，每次滚动前重新找（窗口可能被移动）
    """

    def __init__(self, max_pages: int = 60, interval: float = 3.0,
                 idle_rounds: int = 3,
                 scroll_fn=None, count_fn=None, find_fn=None,
                 on_log=None, on_progress=None, on_done=None):
        super().__init__(daemon=True)
        self.max_pages = max(1, int(max_pages))
        self.interval = max(0.5, float(interval))
        self.idle_rounds = max(1, int(idle_rounds))
        self._scroll_fn = scroll_fn
        self._count_fn = count_fn
        self._find_fn = find_fn or find_video_window
        self._on_log = on_log or (lambda m, level="info": logger.info(m))
        self._on_progress = on_progress or (lambda done, total, added: None)
        self._on_done = on_done
        self._ev_stop = threading.Event()
        self.result = {"pages": 0, "added": 0, "reason": ""}

    # ---- 控制 ----
    def stop(self):
        self._ev_stop.set()

    # ---- 内部 ----
    def _do_scroll(self) -> bool:
        """滚一次。返回 False 表示找不到窗口。"""
        if self._scroll_fn:
            self._scroll_fn()
            return True
        hwnd, rect = self._find_fn()
        if not hwnd or not rect:
            return False
        cx = (rect[0] + rect[2]) // 2
        cy = (rect[1] + rect[3]) // 2
        old = get_cursor_pos()
        try:
            set_cursor_pos(cx, cy)
            time.sleep(0.05)
            send_wheel(-WHEEL_DELTA * 3)   # 一次滑 3 格（约一屏的 1/3）
        finally:
            try:
                set_cursor_pos(*old)        # 还原用户光标，别乱跳
            except Exception:
                pass
        return True

    def run(self):
        base = self._count_fn() if self._count_fn else 0
        idle = 0
        try:
            for page in range(self.max_pages):
                if self._ev_stop.is_set():
                    self.result["reason"] = "用户停止"
                    break
                ok = self._do_scroll()
                if not ok:
                    self._on_log("⚠ 没找到视频号窗口 —— 请先把「微信视频号」窗口打开"
                                 "并显示回放列表，然后重新开始", "warn")
                    self.result["reason"] = "找不到视频号窗口"
                    break
                self.result["pages"] = page + 1
                # 等页面加载 + 捕获处理
                for _ in range(int(self.interval * 4)):
                    if self._ev_stop.is_set():
                        break
                    time.sleep(0.25)

                now = self._count_fn() if self._count_fn else base
                added = now - base
                self.result["added"] = added
                self._on_progress(page + 1, self.max_pages, added)

                if now == (self.result.get("_last_count", base)):
                    idle += 1
                    self._on_log(f"第 {page + 1} 页：本页无新增（连续 {idle}/{self.idle_rounds}）")
                    if idle >= self.idle_rounds:
                        self.result["reason"] = "连续多轮无新增，判定已到底"
                        break
                else:
                    idle = 0
                    self._on_log(f"第 {page + 1} 页：累计新增 {added} 条 ✓")
                self.result["_last_count"] = now

            if not self.result["reason"]:
                self.result["reason"] = f"达到翻页上限 {self.max_pages} 页"
        except Exception as e:                                   # noqa: BLE001
            self.result["reason"] = f"异常：{type(e).__name__}: {e}"
            self._on_log(f"❌ 自动翻页异常：{e}", "err")
        finally:
            self._on_log(f"🔁 自动翻页结束：翻 {self.result['pages']} 页，"
                         f"新增 {self.result['added']} 条（{self.result['reason']}）",
                         "ok")
            if self._on_done:
                try:
                    self._on_done({k: v for k, v in self.result.items()
                                   if not k.startswith("_")})
                except Exception:
                    pass


def selftest() -> bool:
    """CLI 自检（`--selftest-autopage`）：验证冻结包里模块完整可用。

    为什么需要：PyInstaller 漏模块时，客户点按钮才炸（ImportError），
    而 `--selftest` 不覆盖这块。这个自检在打包后跑一遍就能提前发现。
    """
    ok = True

    def _c(name, cond, detail=""):
        nonlocal ok
        print(("  [PASS] " if cond else "  [FAIL] ") + name
              + (f"  {detail}" if detail else ""))
        ok = ok and bool(cond)

    print("自动翻页自检")
    _c("滚轮常量", WHEEL_DELTA == 120 and MOUSEEVENTF_WHEEL == 0x0800)
    _c("INPUT 结构大小", ctypes.sizeof(INPUT) in (28, 40),
       str(ctypes.sizeof(INPUT)))
    try:
        send_wheel(-WHEEL_DELTA)
        _c("send_wheel 可执行", True)
    except Exception as e:                                   # noqa: BLE001
        _c("send_wheel 可执行", False, str(e))

    # 进程名解析（不依赖 psutil）
    try:
        import win32gui
        import win32process
        hwnd = win32gui.GetForegroundWindow()
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        nm = _proc_name(pid)
        _c("进程名解析", bool(nm), nm or "(空)")
    except Exception as e:                                   # noqa: BLE001
        _c("进程名解析", False, str(e))

    # 窗口查找（找不到不算失败：用户没开视频号是常态）
    try:
        hwnd, rect = find_video_window()
        print(f"  [INFO] 视频号窗口：{'已找到 ' + str(hwnd) if hwnd else '未打开（正常）'}")
    except Exception as e:                                   # noqa: BLE001
        _c("窗口查找不报错", False, str(e))

    # 线程与停止
    try:
        p = AutoPager(max_pages=1, interval=0.05, scroll_fn=lambda: None,
                      count_fn=lambda: 0)
        p.start()
        p.join(timeout=10)
        _c("AutoPager 可运行", not p.is_alive(),
           f"pages={p.result['pages']}")
    except Exception as e:                                   # noqa: BLE001
        _c("AutoPager 可运行", False, str(e))

    print("自动翻页自检: " + ("ALL PASS" if ok else "FAIL"))
    return ok
