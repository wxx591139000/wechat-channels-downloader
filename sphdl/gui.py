"""
sphdl GUI —— tkinter 桌面界面（零新增依赖，标准库自带）
=====================================================
功能：
  1. 嗅探引擎（本地代理）一键 启动 / 停止，可选自动设置系统代理
  2. mitmproxy CA 证书状态检测 + 一键唤起 Windows 证书安装向导
  3. 下载目录选择与持久化
  4. 视频下载列表：状态/进度实时刷新、重试、打开文件/文件夹、删除记录/文件
  5. 实时日志 + 状态栏
  6. 列表与配置持久化（records.json / config.json），重启后列表不丢

线程模型：
  - tkinter 主线程只做 UI 刷新（root.after 轮询队列）
  - 嗅探引擎在独立线程跑 asyncio 事件循环
  - 下载/解密在 Pipeline 工作线程
  - 所有跨线程 UI 更新都通过 queue.Queue 投递，绝不跨线程碰 tkinter
"""
import datetime
import json
import os
import platform
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass, asdict, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# 应用主目录（开发=sphdl/，冻结打包=exe 所在目录 → 便携布局）
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sphdl import config, __version__   # noqa: E402
from sphdl.entities import VideoData           # noqa: E402
from sphdl.logger import logger                # noqa: E402
from sphdl.pipeline import Pipeline            # noqa: E402
from sphdl.addon import SphAddon               # noqa: E402
from sphdl.proxy_manager import ProxyManager, restore_proxy   # noqa: E402
from sphdl.downloader import format_size       # noqa: E402

CONFIG_FILE = BASE_DIR / "config.json"
RECORDS_FILE = BASE_DIR / "records.json"
MAX_RECORDS = 500

STATUS_TEXT = {
    "waiting": "待下载",
    "pending": "排队中",
    "downloading": "下载中",
    "decrypting": "解密中",
    "done": "已完成 ✓",
    "failed": "失败 ✗",
    "stopped": "已停止",
}
STATUS_TAG = {
    "done": "tag_done", "failed": "tag_failed", "downloading": "tag_downloading",
    "decrypting": "tag_decrypting", "pending": "tag_pending",
    "waiting": "tag_waiting", "stopped": "tag_stopped",
}


# ============================== 配置持久化 ==============================
class AppConfig:
    def __init__(self):
        self.port = config.PROXY_PORT
        self.download_dir = str(config.DOWNLOAD_DIR)
        self.auto_proxy = True
        self.auto_download = False   # 默认手动：捕获只进列表，用户点选后下载（v1.2.5 开关可开）
        self.prefer_highest_quality = False  # 默认下载最高清晰度（原始画质优先）
        self.download_images = True   # 默认下载图片：图片显示在列表并可下载（v2.0.4 开关）
        self.natural_capture = True   # 默认自然捕获：当前播放+随后几条，不整页入库（v2.0.7）
        self._load()

    def _load(self):
        try:
            d = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            self.port = int(d.get("port", self.port))
            self.download_dir = d.get("download_dir", self.download_dir)
            self.auto_proxy = bool(d.get("auto_proxy", True))
            self.auto_download = bool(d.get("auto_download", False))
            self.prefer_highest_quality = bool(d.get("prefer_highest_quality", False))
            self.download_images = bool(d.get("download_images", True))
            self.natural_capture = bool(d.get("natural_capture", True))
        except Exception:
            pass

    def save(self):
        try:
            CONFIG_FILE.write_text(json.dumps(
                {"port": self.port, "download_dir": self.download_dir,
                 "auto_proxy": self.auto_proxy,
                 "auto_download": self.auto_download,
                 "prefer_highest_quality": self.prefer_highest_quality,
                 "download_images": self.download_images,
                 "natural_capture": self.natural_capture},
                ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


# ============================== 下载记录（持久化列表） ==============================
@dataclass
class Record:
    id: str
    title: str
    url: str
    decode_key: str
    size: int
    source: str
    created: str
    status: str = "pending"
    file: str = ""
    error: str = ""
    progress: int = 0
    is_image: bool = False
    video_id: str = ""   # 稳定视频标识（md5sum），用于跨会话去重
    renditions: list = field(default_factory=list)  # [{url,size,width,height,bitrate,label}]
    quality_note: str = ""  # 清晰度说明（spec 档位阶梯，仅展示用）


class RecordStore:
    def __init__(self, path: Path):
        self.path = path
        self.items: dict = {}      # id -> Record
        self.order: list = []      # 新 → 旧
        self._load()

    def _load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for d in raw[:MAX_RECORDS]:
                kw = {k: d.get(k, "") for k in
                      ("id", "title", "url", "decode_key", "size",
                       "source", "created", "status", "file", "error",
                       "progress", "is_image", "video_id", "quality_note")}
                kw["renditions"] = d.get("renditions") or []
                r = Record(**kw)
                # 上次退出时正在处理中的，标记为失败；
                # "waiting"（还没开始下载）保持不变，重启后仍可点选下载
                if r.status in ("pending", "downloading", "decrypting"):
                    r.status = "failed"
                    r.error = "已中断（程序重启）"
                    r.progress = 0
                self.items[r.id] = r
                self.order.append(r.id)
        except FileNotFoundError:
            pass
        except Exception:
            self.items, self.order = {}, []

    def add(self, rec: Record):
        self.items[rec.id] = rec
        self.order.insert(0, rec.id)
        while len(self.order) > MAX_RECORDS:
            old = self.order.pop()
            self.items.pop(old, None)

    def update(self, rid, **kw):
        if rid in self.items:
            for k, v in kw.items():
                setattr(self.items[rid], k, v)

    def remove(self, rid):
        self.items.pop(rid, None)
        if rid in self.order:
            self.order.remove(rid)

    def by_status(self, status):
        return [self.items[i] for i in self.order if i in self.items
                and self.items[i].status == status]

    def save(self):
        try:
            data = [asdict(self.items[i]) for i in self.order if i in self.items]
            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
        except Exception:
            pass


# ============================== 嗅探引擎（线程封装） ==============================
class SniffEngine:
    def __init__(self, app):
        self.app = app
        self.port = config.PROXY_PORT
        self.master = None
        self.loop = None
        self.thread = None
        self.pm = None
        self._running = False
        self.addon = None

    @property
    def running(self):
        return self._running

    def start(self, port: int, auto_proxy: bool) -> bool:
        if self._running or (self.thread and self.thread.is_alive()):
            return False
        from mitmproxy import options
        opts = options.Options(listen_host=config.PROXY_HOST,
                               listen_port=port, ssl_insecure=True)

        self.pm = None
        if auto_proxy:
            pm = ProxyManager(config.PROXY_HOST, port)
            if pm.setup(backup_path=config.PROXY_BACKUP_FILE):
                self.pm = pm
                self.app.push_log("ok", f"系统代理已设为 127.0.0.1:{port}（原代理状态已快照，停止时自动恢复）")
            else:
                self.app.push_log("warn", "自动设置系统代理失败，请手动将代理设为 127.0.0.1:%d" % port)

        self.port = port
        self._running = True
        # 注入 JS 的 bridge 回传端口必须与本次启动端口一致，否则改端口后
        # 钩子仍回传 8899，bridge 捕获静默失效（2026-08-26 审查发现）
        if self.app.addon is not None:
            try:
                self.app.addon.set_bridge_port(port)
            except Exception:
                pass
        self.thread = threading.Thread(target=self._run, args=(opts,), daemon=True)
        self.thread.start()

        # 等待端口真正监听（v1.2.5：5s 次数循环 → 30s 墙钟窗口）。
        # 本机特性：loopback 无监听端口的 SYN 不回 RST 而是挂起，
        # 每次探测占满 0.3s 超时——按次数循环会把窗口拉长到 ~20s
        # 且无进度提示，故改为按墙钟计时（≤30s），每 10s 提示一次。
        deadline = time.time() + 30
        last_note = 0.0
        while self._running and time.time() < deadline:
            if self._port_open(port):
                self.app.push_log("ok", f"代理已监听 127.0.0.1:{port}，开始嗅探。请打开微信视频号播放视频")
                return True
            now = time.time()
            if now - last_note >= 10:
                self.app.push_log("info",
                                   "等待代理就绪…（首次启动可能因杀毒扫描稍慢；"
                                   "若持续等待请重启本工具后重试）")
                last_note = now
            time.sleep(0.1)
        # 失败诊断：区分「引擎线程已退出（查异常日志）」与「仍在启动」
        # （替代误导性的「端口被占用」——2026-08-26 启动嗅探失败事故）
        if self.thread and not self.thread.is_alive():
            self.app.push_log("error",
                               f"代理启动失败：引擎线程已退出——请查看上方"
                               "「代理引擎异常退出 / 未捕获异常」日志行")
        else:
            self.app.push_log("error",
                               f"代理启动失败：引擎仍在启动但 30 秒内端口 {port} 未就绪"
                               "（若为首次解压启动，杀毒扫描可能拖慢首次加载，请再点一次『启动嗅探』）")
        self.stop()
        return False

    def _port_open(self, port):
        import socket
        s = socket.socket()
        s.settimeout(0.3)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False
        finally:
            s.close()

    def _run(self, opts):
        import asyncio
        # 分阶段日志（v1.2.5）：引擎卡在哪一步/死在哪一步一眼可见
        # （2026-08-26 启动嗅探失败事故——引擎线程静默死亡，无异常无 finally）
        self.app.push_log("debug", "[引擎] 线程已启动，开始导入 mitmproxy")
        from mitmproxy import addons
        from mitmproxy.master import Master
        self.app.push_log("debug", "[引擎] mitmproxy 导入完成")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.loop = loop
        self.app.push_log("debug", "[引擎] 事件循环已创建，进入 run_until_complete")

        # mitmproxy 11 的 Master.__init__ 及 addons 配置阶段都要求
        # 事件循环“正在运行”（内部调 asyncio.get_running_loop()）。
        # 因此把 Master 构造 / 加插件 / run 全部放进 async 函数，
        # 在 run_until_complete 内部执行（与 CLI 模式一致）。
        async def _amain():
            master = Master(opts)
            self.app.push_log("debug", "[引擎] Master 已构造，开始监听")
            master.addons.add(*addons.default_addons())
            master.addons.add(self.app.addon)
            self.master = master
            # 事件循环延迟自测（v1.2.5，兜底侦测）：循环被同步操作阻塞时
            # 本任务会被延迟唤醒——延迟即拥塞证据，>1s 打警告
            wd = asyncio.ensure_future(self._loop_watchdog())
            try:
                await master.run()
            finally:
                wd.cancel()

        # 真正运行事件循环：Master 构造 / 监听 / run 全部在
        # run_until_complete 内执行（v1.2.1 结构）。
        # ⚠️ v1.2.2 事故根因：本 try/except/finally 曾随 _loop_watchdog
        # 的插入被误并入该协程（缩进不变、位置后移），导致 _run 定义完
        # 协程后直接返回——引擎线程无异常、无 finally 静默退出，
        # 「启动嗅探」30s 后必报「引擎线程已退出」。
        try:
            loop.run_until_complete(_amain())
        except Exception as e:
            self.app.push_log("error", f"代理引擎异常退出: {e}")
        finally:
            self._running = False
            try:
                if self.master is not None:
                    self.master.shutdown()
            except Exception:
                pass
            # 干净收尾：取消仍在挂起的任务（如 Windows IOCP 的 accept 任务）
            # 并等待其完成，确保监听端口被释放，避免再次启动时
            # 出现「端口 8899 可能被占用」。
            try:
                pending = asyncio.all_tasks(loop)
                for t in pending:
                    t.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            self.master = None
            try:
                loop.close()
            except Exception:
                pass
            self.loop = None
            # 引擎自行退出（异常/崩溃）而未走 stop()：必须补恢复系统代理，
            # 否则浏览器/微信会一直指向已死的代理而无法联网。
            if self.pm is not None:
                try:
                    self.pm.cleanup()
                    self.app.push_log("ok", "系统代理已恢复（引擎已退出）")
                except Exception:
                    self.app.push_log("warn",
                                       "⚠️ 恢复系统代理失败，请到 设置→网络和 Internet→代理 手动关闭")
                self.pm = None
            self.app.push_event(("engine_state", False))

    async def _loop_watchdog(self):
        """每 5s 自测一次事件循环延迟（asyncio.sleep 的唤醒延迟）。
        v1.2.5 起重解析已在独立线程（sphdl-parse），循环应保持亚毫秒
        延迟；>1s 说明有同步操作卡循环（第三方/意外路径），打警告。
        延迟值写入 addon.loop_delay_ms，供 60s 心跳日志展示。"""
        import asyncio
        interval = 5.0
        warned = False
        next_wake = time.time() + interval
        try:
            while True:
                await asyncio.sleep(interval)
                now = time.time()
                delay = now - next_wake
                next_wake = now + interval
                self.app.addon.loop_delay_ms = max(0, int(delay * 1000))
                if delay > 1.0 and not warned:
                    warned = True
                    self.app.push_log(
                        "warn",
                        f"⚠️ 代理事件循环延迟 {delay:.1f}s（超过 1 秒）：循环拥塞，"
                        "捕获回调可能被延迟。v1.2.5 重解析已在独立线程，"
                        "正常不应出现——请保留日志反馈")
                elif delay < 0.5:
                    warned = False
        except asyncio.CancelledError:
            pass

    def stop(self):
        if not self._running:
            return
        self._running = False
        self.app.push_log("info", "正在停止代理引擎...")
        loop, master = self.loop, self.master
        if loop and master:
            try:
                loop.call_soon_threadsafe(master.should_exit.set)
            except Exception:
                pass
        t = self.thread
        if t and t.is_alive():
            t.join(timeout=6)
        if self.pm:
            ok = self.pm.cleanup()
            self.app.push_log("ok" if ok else "warn",
                              "系统代理已恢复" if ok else "⚠️ 恢复系统代理失败，请到 设置→网络和 Internet→代理 手动关闭")
            self.pm = None
        self.app.push_log("ok", "嗅探已停止")
        self.app.push_event(("engine_state", False))


# ============================== 主界面 ==============================
class App:
    POLL_MS = 300

    def __init__(self):
        self.cfg = AppConfig()
        self.ui_q: "queue.Queue" = queue.Queue()
        self.log_q: "queue.Queue" = queue.Queue()
        logger.subscribe(self._on_log)

        self.store = RecordStore(RECORDS_FILE)
        self.engine = SniffEngine(self)

        self.ca_state = "checking"     # checking / installed / missing / unknown
        self._dirty = False
        self._row_of: dict = {}        # record id -> treeview iid
        self._url_to_rid: dict = {}    # url -> record id
        self._known_logged: set = set()  # 已提示过"列表中已有"的 video_id/url（防刷屏）
        self._rid_seq = 0              # 捕获记录 id 递增序号（防同秒碰撞）
        # 钩子看门狗状态（防"捕获静默停摆"，2026-08-25 v1.2.1）
        self._hook_warned = False
        self._last_hb_log = 0.0
        for rid in self.store.order:
            r = self.store.items[rid]
            self._url_to_rid[r.url] = rid

        # 「🖼 下载图片」关闭时（v2.0.4）：启动即移除历史图片记录，
        # 列表不显示、不参与下载/去重；已下载的图片文件保留在磁盘，
        # 重新开启开关后需重新捕获图片
        if not self.cfg.download_images:
            dropped = 0
            for rid in [r.id for r in self.store.items.values() if r.is_image]:
                self.store.remove(rid)
                for k in [k for k, v in self._url_to_rid.items() if v == rid]:
                    self._url_to_rid.pop(k, None)
                dropped += 1
            if dropped:
                self._dirty = True
                self.push_log("warn",
                              f"🖼 下载图片：已关闭 —— 启动时移除 {dropped} 条历史图片记录"
                              "（图片文件保留，重新开启后需重新捕获）")

        self._build_root()
        self._build_ui()

        # 下载管线 + 嗅探插件（管线独立于代理，停止嗅探后仍可下载/重试）
        # GUI 模式：默认捕获只进列表不自动下载；「⚙ 自动下载」开关打开后
        # 捕获即自动入队（v1.2.5，状态持久化 config.json）
        self.pipeline = Pipeline(self.cfg.download_dir, on_event=self._on_pipeline_event)
        self.addon = SphAddon(self.pipeline, on_capture=self._on_capture,
                              auto_download=self.cfg.auto_download,
                              allow_images=self.cfg.download_images)
        self.addon.natural_capture = self.cfg.natural_capture
        # 自动下载提交前的跨会话去重（records.json 已有同视频/链接 → 不重复下载）
        self.addon.duplicate_check = self._is_known_video

        threading.Thread(target=self._check_ca, daemon=True).start()

        # 兜底：解释器异常退出（未捕获异常等）时尽力恢复系统代理，
        # 避免浏览器/微信一直指向已死的代理而无法联网。
        import atexit

        def _safety_proxy_cleanup():
            try:
                eng = self.engine
                if eng.pm is not None and not eng._running:
                    eng.pm.cleanup()
                    eng.pm = None
            except Exception:
                pass

        atexit.register(_safety_proxy_cleanup)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(self.POLL_MS, self._poll)

    # ---------------- 跨线程安全工具 ----------------
    def push_event(self, item):
        self.ui_q.put(item)

    def push_log(self, level, msg):
        self.log_q.put((level, msg))
        logger.log_file(level, msg)  # 同步落盘，进程消失也能查到全过程

    def _on_log(self, level, msg):
        self.log_q.put((level, msg))

    def _on_capture(self, vd, source):
        self.push_event(("capture", vd, source))

    def _is_known_video(self, vd) -> bool:
        """跨会话去重判断（供 addon 在自动下载提交前调用，v2.0.5）：
        同一视频（video_id=内容哈希）或同一 URL 已在列表 → True。"""
        if vd.video_id and any(r.video_id == vd.video_id
                               for r in self.store.items.values()):
            return True
        return vd.url in self._url_to_rid

    def _on_pipeline_event(self, event, vd, *args):
        self.push_event((event, vd, *args))

    # ---------------- 窗口与控件 ----------------
    def _build_root(self):
        self.root = tk.Tk()
        self.root.title(f"微信视频号下载器  sphdl v{__version__}")
        self.root.geometry("1240x780")
        self.root.minsize(1080, 660)
        try:
            from tkinter import font as tkfont
            f = tkfont.nametofont("TkDefaultFont")
            f.configure(family="Microsoft YaHei UI", size=9)
            f2 = tkfont.nametofont("TkTextFont")
            f2.configure(family="Microsoft YaHei UI", size=9)
        except Exception:
            pass

    def _build_ui(self):
        root = self.root
        main = ttk.Frame(root, padding=8)
        main.pack(fill="both", expand=True)

        # ===== ① 引擎控制区 =====
        eng = ttk.LabelFrame(main, text=" 嗅探引擎 ", padding=8)
        eng.pack(fill="x")

        self.btn_engine = ttk.Button(eng, text="▶  启动嗅探", width=14,
                                     command=self._toggle_engine)
        self.btn_engine.pack(side="left")

        ttk.Label(eng, text="代理端口").pack(side="left", padx=(16, 2))
        self.port_var = tk.StringVar(value=str(self.cfg.port))
        self.ent_port = ttk.Entry(eng, textvariable=self.port_var, width=7)
        self.ent_port.pack(side="left")

        self.auto_var = tk.BooleanVar(value=self.cfg.auto_proxy)
        ttk.Checkbutton(eng, text="自动设置系统代理", variable=self.auto_var
                        ).pack(side="left", padx=14)

        # 应急按钮：把系统代理恢复到本工具接管前的状态（快照在 config 目录的
        # proxy_backup.json；无快照时兜底关闭代理）。引擎运行中点它会先确认。
        self.btn_restore_proxy = ttk.Button(eng, text="↩  恢复网络代理", width=13,
                                            command=self._on_restore_proxy)
        self.btn_restore_proxy.pack(side="left", padx=14)

        ttk.Label(eng, text="CA证书:").pack(side="left", padx=(18, 2))
        self.ca_label = ttk.Label(eng, text="检测中…", foreground="#d97706")
        self.ca_label.pack(side="left")
        ttk.Button(eng, text="安装/检测", width=10,
                   command=self._on_cert_btn).pack(side="left", padx=8)

        self.eng_label = ttk.Label(eng, text="状态: 已停止", foreground="#6b7280")
        self.eng_label.pack(side="right")

        # ===== ② 捕获设置（捕获后的行为） =====
        capf = ttk.LabelFrame(main, text=" 捕获设置 ", padding=6)
        capf.pack(fill="x", pady=(8, 0))

        # 自动下载开关（v1.2.5）：开 = 新捕获立即自动入队下载；
        # 关（默认）= 捕获只进列表，用户选中后手动下载（原行为）
        self.auto_dl_var = tk.BooleanVar(value=self.cfg.auto_download)
        ttk.Checkbutton(capf, text="⚙ 自动下载（捕获即入队）",
                        variable=self.auto_dl_var,
                        command=self._on_auto_dl_toggle
                        ).pack(side="left", padx=(2, 16))

        # 「🖼 下载图片」开关（v2.0.4）：勾选（默认）= 图片显示在列表并可下载；
        # 取消 = 列表不显示图片、新捕获的图片直接跳过、下载操作跳过图片。
        self.images_var = tk.BooleanVar(value=self.cfg.download_images)
        ttk.Checkbutton(capf, text="🖼 下载图片",
                        variable=self.images_var,
                        command=self._on_images_toggle
                        ).pack(side="left", padx=16)

        # 「🎯 自然捕获」开关（v2.0.7）：开（默认）= 播放到哪条才捕获哪条；
        # 关 = 整页列表捕获（v2.0.6 行为）
        self.natural_var = tk.BooleanVar(value=self.cfg.natural_capture)
        ttk.Checkbutton(capf, text="🎯 自然捕获",
                        variable=self.natural_var,
                        command=self._on_natural_toggle
                        ).pack(side="left", padx=16)

        # 「⚡ 批量拉取」按钮（v2.0.8）：整页入库 + 页面自动滚动拉取整个视频号
        self.btn_batch = ttk.Button(capf, text="⚡ 批量拉取", width=10,
                                    command=self._on_batch_toggle)
        self.btn_batch.pack(side="left", padx=(16, 2))

        # 当前模式提示（随开关实时变化）
        self.mode_label = ttk.Label(capf, foreground="#6b7280")
        self.mode_label.pack(side="right", padx=4)

        # ===== ③ 下载目录 =====
        dirf = ttk.Frame(main)
        dirf.pack(fill="x", pady=(8, 0))
        ttk.Label(dirf, text="下载目录:").pack(side="left")
        self.dir_var = tk.StringVar(value=self.cfg.download_dir)
        ttk.Entry(dirf, textvariable=self.dir_var, width=52).pack(side="left", padx=6)
        ttk.Button(dirf, text="更改…", width=8,
                   command=self._choose_dir).pack(side="left")
        ttk.Button(dirf, text="打开文件夹", width=10,
                   command=self._open_download_dir).pack(side="left", padx=4)

        # ===== ③ 下载列表 =====
        # 标题随「⚙ 自动下载」开关联动（v1.2.5）
        self.list_frame = ttk.LabelFrame(
            main,
            text=" 视频列表（自动下载已开启：捕获即入队下载） "
            if self.cfg.auto_download
            else " 视频列表（捕获不会自动下载：选中后点『下载』或双击行） ",
            padding=4)
        self.list_frame.pack(fill="both", expand=True, pady=8)
        listf = self.list_frame

        # 工具栏分两行：先 pack 管理行（最底），再 pack 下载行（其上）
        btns2 = ttk.Frame(listf)   # 文件与记录管理
        btns2.pack(side="bottom", fill="x", pady=(0, 0))
        btns1 = ttk.Frame(listf)   # 下载与清晰度
        btns1.pack(side="bottom", fill="x", pady=(6, 2))

        cols = ("status", "title", "size", "source", "time", "file")
        self.tree = ttk.Treeview(listf, columns=cols, show="headings", height=10,
                                 selectmode="extended")
        heads = {"status": ("状态", 110), "title": ("标题", 250), "size": ("大小", 80),
                 "source": ("来源", 150), "time": ("时间", 90), "file": ("文件", 190)}
        for c in cols:
            self.tree.heading(c, text=heads[c][0])
            self.tree.column(c, width=heads[c][1],
                             stretch=(c in ("title", "file")))
        vsb = ttk.Scrollbar(listf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.tag_configure("tag_done", foreground="#15803d")
        self.tree.tag_configure("tag_failed", foreground="#dc2626")
        self.tree.tag_configure("tag_downloading", foreground="#2563eb")
        self.tree.tag_configure("tag_decrypting", foreground="#d97706")
        self.tree.tag_configure("tag_pending", foreground="#6b7280")
        self.tree.tag_configure("tag_waiting", foreground="#0f766e")
        self.tree.tag_configure("tag_stopped", foreground="#9ca3af")
        self.tree.bind("<Double-1>", self._on_tree_dclick)
        self.tree.bind("<Control-a>", lambda e: self._select_all())

        # ---- 工具栏 A：下载操作 ----
        self.btn_download = ttk.Button(btns1, text="⬇ 下载选中", width=10,
                                       command=lambda: self._download_selected())
        self.btn_download.pack(side="left")
        ttk.Button(btns1, text="停止下载", width=9,
                   command=lambda: self._stop_selected()).pack(side="left", padx=4)
        ttk.Button(btns1, text="下载全部待下载", width=12,
                   command=lambda: self._download_all_waiting()).pack(side="left", padx=4)
        ttk.Button(btns1, text="重试失败", width=9,
                   command=lambda: self._retry()).pack(side="left", padx=4)
        ttk.Button(btns1, text="↻ 重新下载", width=10,
                   command=lambda: self._redownload_selected()).pack(side="left", padx=4)
        ttk.Separator(btns1, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(btns1, text="清晰度", width=8,
                   command=lambda: self._pick_quality()).pack(side="left", padx=4)
        self.prefer_hq_var = tk.BooleanVar(value=self.cfg.prefer_highest_quality)
        ttk.Checkbutton(btns1, text="首选原始画质", variable=self.prefer_hq_var,
                        command=self._on_prefer_hq_toggle).pack(side="left", padx=8)

        # ---- 工具栏 B：文件与记录管理 ----
        ttk.Button(btns2, text="打开文件", width=9,
                   command=lambda: self._open_file()).pack(side="left", padx=4)
        ttk.Button(btns2, text="打开文件夹", width=9,
                   command=lambda: self._open_folder()).pack(side="left")
        ttk.Separator(btns2, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(btns2, text="删除记录", width=9,
                   command=lambda: self._del_record()).pack(side="left", padx=4)
        ttk.Button(btns2, text="删除文件+记录", width=11,
                   command=lambda: self._del_file()).pack(side="left")
        ttk.Separator(btns2, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(btns2, text="清空已完成", width=10,
                   command=lambda: self._clear_done()).pack(side="left", padx=4)
        ttk.Button(btns2, text="清空失败", width=9,
                   command=lambda: self._clear_failed()).pack(side="left")

        # ===== ④ 日志区 =====
        logf = ttk.LabelFrame(main, text=" 日志 ", padding=4)
        logf.pack(fill="both", pady=(0, 0))
        self.log_text = tk.Text(logf, height=9, bg="#0b1020", fg="#d1d5db",
                                insertbackground="#d1d5db", relief="flat",
                                font=("Consolas", 9), wrap="word", state="disabled")
        lsb = ttk.Scrollbar(logf, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=lsb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")
        for tag, color in (("info", "#7dd3fc"), ("ok", "#86efac"),
                           ("warn", "#fde047"), ("error", "#fca5a5"), ("dim", "#9ca3af")):
            self.log_text.tag_configure(tag, foreground=color)

        self.auto_scroll_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(logf, text="自动滚动", variable=self.auto_scroll_var
                        ).pack(anchor="e", pady=(4, 0))
        ttk.Button(logf, text="📋 复制日志", width=10,
                   command=self._copy_log).pack(side="right", anchor="e",
                                                pady=(4, 0), padx=(0, 4))
        ttk.Button(logf, text="清空", width=6,
                   command=self._clear_log).pack(side="right", anchor="e", pady=(4, 0))

        # ===== ⑥ 状态栏 =====
        self.status_bar = ttk.Label(root, text="", relief="sunken", anchor="w",
                                    padding=(8, 2))
        self.status_bar.pack(fill="x", side="bottom")

        self._refresh_tree_all()
        self._update_capture_hint()

    # ---------------- 引擎控制 ----------------
    def _on_restore_proxy(self):
        """应急：恢复网络代理 + 同步重置引擎状态。
        恢复代理后流量不再经过本工具 → 嗅探必然失效，所以若引擎在运行
        就一并停止（按钮回到「启动嗅探」），避免界面状态与实际能力不符。
        代理恢复：有快照 → 按快照恢复（原 Clash/VPN 端口 or 原无代理）；
        无快照 → 兜底关闭系统代理。"""
        if self.engine.running and not messagebox.askyesno(
                "恢复网络代理",
                "嗅探引擎正在运行。点击后将：\n"
                "  1) 停止嗅探引擎\n"
                "  2) 把系统代理恢复到本工具接管前的状态\n"
                "（进行中的下载不受影响）\n\n继续？"):
            return

        def _job():
            # 先停引擎：stop() 内部会恢复代理、释放端口，
            # 并推送 engine_state → 按钮自动回到「▶ 启动嗅探」
            if self.engine.running:
                self.engine.stop()
            # 再以持久快照为准做一次兜底恢复（文件快照 = 接管前状态）
            ok, desc = restore_proxy(config.PROXY_BACKUP_FILE)
            self.push_log("ok" if ok else "error", f"网络代理：{desc}")
            if ok:
                self._update_status_bar()

        threading.Thread(target=_job, daemon=True).start()

    def _on_auto_dl_toggle(self):
        """「⚙ 自动下载」开关（v1.2.5）：开 = 新捕获立即自动入队下载；
        关（默认）= 捕获只进列表，用户选中后手动下载。
        状态持久化到 config.json；列表框标题随开关联动。
        注意：只对开关打开后新捕获的视频生效，已在列表的
        「待下载」条目不会自动补下（手动点选即可）。"""
        on = bool(self.auto_dl_var.get())
        self.addon.auto_download = on
        self.cfg.auto_download = on
        self.cfg.save()
        self.list_frame.config(
            text=" 视频列表（自动下载已开启：捕获即入队下载） " if on
            else " 视频列表（捕获不会自动下载：选中后点『下载』或双击行） ")
        self.push_log("info",
            "自动下载：开 —— 新捕获的视频将立即自动加入下载队列" if on
            else "自动下载：关 —— 请在列表选中视频后点「⬇ 下载选中」")
        self._update_capture_hint()

    def _on_images_toggle(self):
        """「🖼 下载图片」开关（v2.0.4）：
        勾选（默认）= 图片显示在列表并可下载；
        取消 = 列表不显示图片、新捕获的图片直接跳过、下载操作跳过图片。"""
        on = bool(self.images_var.get())
        self.cfg.download_images = on
        self.addon.allow_images = on
        self.cfg.save()
        if on:
            self.push_log("ok", "🖼 下载图片：开 —— 图片会显示在列表并可下载")
        else:
            dropped = 0
            for rid in [r.id for r in self.store.items.values() if r.is_image]:
                self._remove_row(rid)  # 同步清树行 / store / url 映射
                dropped += 1
            self.push_log("warn",
                f"🖼 下载图片：关 —— 已从列表移除 {dropped} 条图片记录，"
                "之后的图片捕获将直接跳过（已下载的图片文件保留在磁盘，"
                "重新开启后需重新捕获）" if dropped else
                "🖼 下载图片：关 —— 之后的图片捕获将被跳过，不再显示/下载")
        self._dirty = True
        self._update_status_bar()
        self._update_capture_hint()

    def _on_natural_toggle(self):
        """「🎯 自然捕获」开关（v2.0.7）：
        开（默认）= 列表数据先缓存为候选，播放到哪条才捕获哪条
        （加上紧随其后的几条缓存），不会整页一次性入库；
        关 = 恢复整页列表捕获（v2.0.6 行为）。"""
        on = bool(self.natural_var.get())
        self.cfg.natural_capture = on
        self.addon.natural_capture = on
        self.cfg.save()
        self.push_log("ok",
            "🎯 自然捕获：开 —— 只捕获当前播放的视频 + 随后几条缓存，"
            "不会整页入库" if on else
            "🎯 自然捕获：关 —— 列表数据将整页捕获（v2.0.6 行为）")
        self._update_capture_hint()

    def _on_batch_toggle(self):
        """「⚡ 批量拉取」按钮（v2.0.8）：
        开启 = 整页列表入库（临时关闭自然捕获）+ 往视频号页面注入
        自动滚动驱动，尽量拉取整个视频号的所有视频，拉到底自动停止；
        再点一次停止并恢复自然捕获。"""
        on = not self.addon.batch_mode
        self.addon.batch_mode = on
        self.addon.batch_count = 0
        if on:
            # 批量模式：列表数据全部入列（与「🎯 自然捕获」互斥）
            self.addon.natural_capture = False
            self.natural_var.set(False)
            self.addon._batch_active = True
            self.btn_batch.config(text="■ 停止批量")
            self.push_log("ok",
                "⚡ 批量拉取已开启：列表数据将整页入库。\n"
                "页面驱动会在数秒内自动向下滚动加载全部视频，拉到底自动停止；"
                "再点一次按钮可提前停止。\n"
                "（若页面是旧版本注入的未带驱动，请重新打开一次视频号页面）")
        else:
            self.addon.natural_capture = self.cfg.natural_capture
            self.natural_var.set(self.cfg.natural_capture)
            self.addon._batch_active = False
            self.btn_batch.config(text="⚡ 批量拉取")
            self.push_log("info",
                f"⚡ 批量拉取已停止（本次新增 {self.addon.batch_count} 条，"
                "已恢复自然捕获）")
        self._update_status_bar()
        self._update_capture_hint()

    def _update_capture_hint(self):
        """捕获设置区的实时模式提示（v2.0.9 UI 布局）。"""
        if not hasattr(self, "mode_label"):
            return
        parts = ["自动下载: 开" if self.cfg.auto_download else "自动下载: 关",
                 "图片: 开" if self.cfg.download_images else "图片: 关"]
        if getattr(getattr(self, "addon", None), "batch_mode", False):
            parts.append("⚡ 批量拉取中")
        else:
            parts.append("🎯 自然捕获" if self.cfg.natural_capture else "整页捕获")
        self.mode_label.config(text=" | ".join(parts))

    def _toggle_engine(self):
        if self.engine.running:
            self.btn_engine.config(state="disabled")
            threading.Thread(target=self._stop_engine_job, daemon=True).start()
            return
        # 解析端口
        try:
            port = int(self.port_var.get())
            assert 1 <= port <= 65535
        except Exception:
            messagebox.showerror("端口无效", "请输入 1-65535 的端口号")
            return
        auto = bool(self.auto_var.get())
        # CA 证书检查
        if self.ca_state == "missing":
            if messagebox.askyesno("CA 证书未安装",
                                   "检测到未安装 mitmproxy CA 证书。\n"
                                   "不安装则 HTTPS 流量无法解密，嗅探大概率失败。\n\n"
                                   "现在打开证书安装向导吗？"):
                self._launch_cert_wizard()
                return
        self.btn_engine.config(state="disabled", text="启动中…")
        self.cfg.port, self.cfg.auto_proxy = port, auto
        self.cfg.save()
        threading.Thread(target=self._start_engine_job, args=(port, auto),
                         daemon=True).start()

    def _start_engine_job(self, port, auto):
        ok = self.engine.start(port, auto)
        self.push_event(("engine_state", ok))

    def _stop_engine_job(self):
        self.engine.stop()
        self.push_event(("engine_state", False))

    def _apply_engine_state(self, running: bool):
        self.btn_engine.config(state="normal",
                               text="■  停止嗅探" if running else "▶  启动嗅探")
        self.ent_port.config(state="disabled" if running else "normal")
        self.ca_label  # noqa
        self.eng_label.config(text=f"状态: {'运行中 (127.0.0.1:%d)' % self.engine.port if running else '已停止'}",
                              foreground="#15803d" if running else "#6b7280")

    # ---------------- CA 证书 ----------------
    def _check_ca(self):
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-ChildItem Cert:\\CurrentUser\\Root, "
                 "Cert:\\LocalMachine\\Root -ErrorAction SilentlyContinue | "
                 "Where-Object { $_.Subject -like '*mitmproxy*' } | "
                 "Measure-Object).Count"],
                capture_output=True, text=True, timeout=25).stdout.strip()
            n = int(out.splitlines()[-1]) if out else -1
            self.ca_state = "installed" if n > 0 else "missing"
        except Exception:
            self.ca_state = "unknown"
        self.push_event(("ca_state", self.ca_state))

    def _apply_ca_state(self, state):
        self.ca_state = state
        txt, color = {"installed": ("已安装 ✓", "#15803d"),
                      "missing": ("未安装 ⚠", "#dc2626"),
                      "unknown": ("未知 ?", "#d97706"),
                      "checking": ("检测中…", "#d97706")}[state]
        self.ca_label.config(text=txt, foreground=color)
        self._update_status_bar()

    def _on_cert_btn(self):
        threading.Thread(target=self._check_ca, daemon=True).start()
        self._launch_cert_wizard()

    def _launch_cert_wizard(self):
        # PyInstaller 6 的 onedir 会把 --add-data 放进 _internal/，
        # 开发模式则在 exe/main.py 同级目录 → 两处都找
        bat = BASE_DIR / "install-cert.bat"
        if not bat.exists():
            bat = BASE_DIR / "_internal" / "install-cert.bat"
        if not bat.exists():
            self.push_log("error", "找不到 install-cert.bat（证书安装脚本）")
            return
        try:
            subprocess.Popen(f'"{bat}"', shell=True, cwd=str(bat.parent))
        except Exception as e:
            self.push_log("error", f"无法启动证书安装向导: {e}")
            return
        self.push_log("info", "已打开证书安装向导：按向导 5 步完成安装后，回来点『安装/检测』确认")

    # ---------------- 下载目录 ----------------
    def _choose_dir(self):
        d = filedialog.askdirectory(initialdir=self.dir_var.get() or str(BASE_DIR),
                                    title="选择下载目录")
        if not d:
            return
        d = str(Path(d))
        self.dir_var.set(d)
        self.cfg.download_dir = d
        self.cfg.save()
        try:
            self.pipeline.download_dir = Path(d)
            Path(d).mkdir(parents=True, exist_ok=True)
            self.push_log("ok", f"下载目录已改为: {d}")
        except Exception as e:
            self.push_log("error", f"设置下载目录失败: {e}")

    def _open_download_dir(self):
        d = self.dir_var.get()
        try:
            Path(d).mkdir(parents=True, exist_ok=True)
            os.startfile(d)  # noqa
        except Exception as e:
            messagebox.showerror("打开失败", str(e))

    # ---------------- 列表操作 ----------------
    def _select_all(self):
        for iid in self.tree.get_children():
            self.tree.selection_add(iid)

    def _selected_rid(self):
        sel = self.tree.selection()
        if not sel:
            return None
        iid = sel[0]
        for rid, r_iid in self._row_of.items():
            if r_iid == iid:
                return rid
        return None

    def _selected_rids(self):
        """当前所有选中的 record id（多选，按列表顺序）。"""
        sel = set(self.tree.selection())
        out = []
        for rid, iid in self._row_of.items():
            if iid in sel and rid in self.store.items:
                out.append(rid)
        # 保持列表从上到下的顺序
        order = {rid: i for i, rid in enumerate(self.store.order)}
        out.sort(key=lambda r: order.get(r, 1 << 30))
        return out

    def _submit_record(self, rid):
        """把一条记录提交到下载管线（waiting/failed → pending）。"""
        rec = self.store.items[rid]
        # 「🖼 下载图片」关闭时的兜底拦截：图片不下载
        # （正常情况下图片记录已被移出列表，这里防残留/历史状态）
        if rec.is_image and not self.cfg.download_images:
            self.push_log("info", f"已跳过图片下载（下载图片已关闭）: {rec.title[:30]}")
            return False
        url, size = rec.url, rec.size
        # 「首选原始画质」开启时，自动选用可用版本中最大的（原始/全尺寸优先）
        if self.cfg.prefer_highest_quality:
            rds = rec.renditions or []
            if rds:
                best = max(rds, key=lambda r: (int(r.get("size") or 0),
                                               1 if "原始" in r.get("label", "") else 0))
                best_size = int(best.get("size") or 0)
                is_orig = "原始" in best.get("label", "")
                if best["url"] != url and (best_size > size or (best_size == 0 and is_orig)):
                    url, size = best["url"], int(best.get("size") or 0)
                    rec.url, rec.size = url, size
                    # 新 url 也要映射到本记录（下载事件按 vd.url 回写状态行）
                    self._url_to_rid[rec.url] = rid
                    self._dirty = True
                    self.push_log("info", f"首选原始画质: {best.get('label')}")
        vd = VideoData(url=url, decode_key=rec.decode_key, size=size,
                       description=rec.title, is_image=rec.is_image)
        self.store.update(rid, status="pending", progress=0, error="")
        self._dirty = True
        self.pipeline.submit(vd)
        self._update_row(rid)
        return True

    def _on_prefer_hq_toggle(self):
        """「首选原始画质」开关：开 = 下载时自动选最大清晰度版本。"""
        self.cfg.prefer_highest_quality = bool(self.prefer_hq_var.get())
        self.cfg.save()
        self.push_log("info", "首选原始画质：开 —— 下载将自动选用最大清晰度版本"
                      if self.cfg.prefer_highest_quality
                      else "首选原始画质：关 —— 使用捕获到的默认版本（可点『清晰度』手动切换）")

    def _pick_quality(self):
        """为选中记录选择清晰度（标准/原始等），选后立即加入下载队列。"""
        rid = self._selected_rid()
        if not rid:
            messagebox.showinfo("提示", "请先选中一条视频记录")
            return
        rec = self.store.items[rid]
        renditions = rec.renditions or []
        if len(renditions) <= 1:
            note = rec.quality_note or ""
            msg = "该记录只有 1 个可下载地址。"
            if note:
                msg += f"\n{note}，但微信未下发其他清晰度的下载链接" \
                       "（原始画质 fullUrl 为空）。"
            else:
                msg += "\n微信未提供其他清晰度版本（或这是旧版本捕获的记录）。"
            msg += "\n下载后列表会显示实际文件大小，以实际为准。"
            messagebox.showinfo("提示", msg)
            return
        # 预选当前记录正在使用的 url
        preselect = 0
        for i, rd in enumerate(renditions):
            if rd.get("url") == rec.url:
                preselect = i
                break
        top = tk.Toplevel(self.root)
        top.title("选择清晰度")
        top.transient(self.root)
        top.grab_set()
        ttk.Label(top, text=(rec.title or "")[:40], wraplength=320,
                  foreground="#6b7280").pack(anchor="w", padx=12, pady=(10, 4))
        var = tk.IntVar(value=preselect)
        for i, rd in enumerate(renditions):
            ttk.Radiobutton(top, text=rd.get("label") or "版本", variable=var,
                            value=i).pack(anchor="w", padx=16, pady=2)

        def _apply_and_download():
            i = var.get()
            rd = renditions[i]
            if rd.get("url") != rec.url:
                # 更新记录的下载来源；旧/新 url 都映射到本记录（去重 + 事件回写）
                self._url_to_rid[rec.url] = rid
                rec.url = rd["url"]
                rec.size = int(rd.get("size") or 0)
                self._url_to_rid[rec.url] = rid
                self._dirty = True
                self.push_log("ok", f"已切换清晰度: {rd.get('label')}，开始下载")
            top.destroy()
            self._submit_record(rid)

        btns2 = ttk.Frame(top)
        btns2.pack(pady=(8, 10))
        ttk.Button(btns2, text="下载该清晰度", command=_apply_and_download
                   ).pack(side="left", padx=6)
        ttk.Button(btns2, text="取消", command=top.destroy).pack(side="left", padx=6)

    def _download_selected(self):
        rids = self._selected_rids()
        if not rids:
            messagebox.showinfo("提示", "请先在列表里选中要下载的视频"
                                       "（可 Ctrl/Shift 多选）")
            return
        submitted, skipped = 0, []
        for rid in rids:
            rec = self.store.items[rid]
            if rec.status in ("waiting", "failed", "stopped"):
                self._submit_record(rid)
                submitted += 1
            else:
                skipped.append(f"{rec.title[:20]}（{STATUS_TEXT.get(rec.status, rec.status)}）")
        if submitted:
            self.push_log("ok", f"已加入下载队列 {submitted} 个: "
                          + "、".join(self.store.items[r].title[:15] for r in rids
                                     if self.store.items[r].status in
                                     ("pending", "downloading"))[:80])
        if skipped:
            self.push_log("warn", f"跳过 {len(skipped)} 条不可下载的记录: "
                          + "；".join(skipped[:5])[:100])
        if not submitted:
            messagebox.showinfo("提示", "选中的记录都不可下载：\n"
                               "· 已完成 / 下载中 / 排队中的无需重复操作\n"
                               "· 只有「待下载」或「失败」的可以点下载\n"
                               "· 「已完成」但文件打不开/无法播放 → 用『↻ 重新下载』")

    def _stop_selected(self):
        rids = self._selected_rids()
        if not rids:
            messagebox.showinfo("提示", "请先选中要停止的记录"
                                       "（排队中/下载中/解密中 可停止）")
            return
        stopped, skipped = 0, 0
        for rid in rids:
            rec = self.store.items[rid]
            if rec.status in ("pending", "downloading", "decrypting"):
                try:
                    self.pipeline.cancel(rec.url)
                except Exception:
                    pass
                stopped += 1
                self.store.update(rid, status="stopped", error="用户停止")
                self._update_row(rid)
                self._dirty = True
                self.push_log("ok", f"已停止: {rec.title[:30]}")
            else:
                skipped += 1
        if skipped:
            self.push_log("warn", f"{skipped} 条状态不可停止（待下载/已完成/已停止等）")
        if not stopped:
            messagebox.showinfo("提示", "选中的记录没有正在进行的下载"
                               "（只有 排队中/下载中/解密中 可以停止）")

    def _download_all_waiting(self):
        rids = [r.id for r in self.store.by_status("waiting")]
        if not rids:
            messagebox.showinfo("提示", "没有待下载的视频")
            return
        if not messagebox.askyesno("下载全部待下载",
                                   f"将 {len(rids)} 个待下载视频全部加入下载队列？\n"
                                   "（下载速度受 CDN 限制，排队依次执行）"):
            return
        for rid in rids:
            self._submit_record(rid)
        self.push_log("ok", f"已加入下载队列 {len(rids)} 个（全部待下载）")

    def _retry(self):
        rids = [r for r in self._selected_rids()
                if self.store.items[r].status in ("failed", "stopped")]
        if not rids:
            sel = self._selected_rid()
            if not sel:
                messagebox.showinfo("提示", "请先选中失败的记录（可多选）")
            else:
                messagebox.showinfo("提示", "选中的记录不是「失败」状态，"
                                       "无需重试（待下载的直接点『下载』）")
            return
        for rid in rids:
            rec = self.store.items[rid]
            if rec.status in ("downloading", "decrypting"):
                continue
            self._submit_record(rid)
        self.push_log("info", f"已重新加入下载队列 {len(rids)} 个: "
                      + "、".join(self.store.items[r].title[:15] for r in rids)[:80])

    def _redownload_selected(self):
        """重新下载：针对 已完成/失败/已停止 的记录（典型场景：
        「已完成」但文件打不开/无法播放 —— 多为 CDN 提前掐断交付的不完整文件）。
        重新入队；下载器写 .tmp 成功后原子覆盖旧文件，
        若失败（如 CDN 链接过期）旧文件保留，状态回到失败。"""
        rids = self._selected_rids()
        if not rids:
            messagebox.showinfo("提示", "请先在列表里选中要重新下载的记录（可 Ctrl/Shift 多选）")
            return
        allowed = ("done", "failed", "stopped")
        targets = [r for r in rids if self.store.items[r].status in allowed]
        if not targets:
            messagebox.showinfo("提示",
                "选中的记录都不在可重新下载的状态\n"
                "（只有「已完成 / 失败 / 已停止」可重新下载；「待下载」直接点『下载』即可）")
            return
        for rid in targets:
            rec = self.store.items[rid]
            if rec.status == "done" and rec.file and os.path.exists(rec.file):
                self.push_log("info", f"重新下载（成功后将覆盖旧文件）: {rec.title[:30]}")
            self._submit_record(rid)
        self.push_log("info", f"已重新加入下载队列 {len(targets)} 个。"
                      "注意：CDN 链接可能已过期，若失败请重新嗅探后再下载")

    def _open_file(self):
        rid = self._selected_rid()
        if not rid:
            return
        rec = self.store.items[rid]
        if not rec.file or not os.path.exists(rec.file):
            messagebox.showwarning("提示", "文件不存在（可能已被移动或删除）")
            return
        try:
            os.startfile(rec.file)  # noqa
        except Exception as e:
            messagebox.showerror("打开失败", str(e))

    def _open_folder(self):
        rid = self._selected_rid()
        rec = self.store.items.get(rid) if rid else None
        target = None
        if rec and rec.file and os.path.exists(rec.file):
            target = str(Path(rec.file).parent)
        else:
            target = self.dir_var.get()
        try:
            Path(target).mkdir(parents=True, exist_ok=True)
            os.startfile(target)  # noqa
        except Exception as e:
            messagebox.showerror("打开失败", str(e))

    def _del_record(self):
        rid = self._selected_rid()
        if not rid:
            return
        rec = self.store.items[rid]
        if not messagebox.askyesno("删除记录",
                                   f"删除这条记录？（视频文件会保留）\n\n{rec.title[:40]}"):
            return
        self._remove_row(rid)
        self._dirty = True
        self.push_log("info", f"已删除记录: {rec.title[:30]}")

    def _del_file(self):
        rid = self._selected_rid()
        if not rid:
            return
        rec = self.store.items[rid]
        if not rec.file:
            messagebox.showinfo("提示", "该记录没有文件")
            return
        if not messagebox.askyesno("删除文件",
                                   f"删除视频文件和记录？\n\n{rec.file}"):
            return
        try:
            if os.path.exists(rec.file):
                os.remove(rec.file)
        except Exception as e:
            messagebox.showerror("删除失败", str(e))
            return
        self._remove_row(rid)
        self._dirty = True
        self.push_log("info", f"已删除文件+记录: {rec.title[:30]}")

    def _clear_done(self):
        done = self.store.by_status("done")
        if not done:
            messagebox.showinfo("提示", "没有已完成的记录")
            return
        if not messagebox.askyesno("清空已完成",
                                   f"删除 {len(done)} 条已完成记录？（文件保留）"):
            return
        for r in done:
            self._remove_row(r.id)
        self._dirty = True
        self.push_log("info", f"已清空 {len(done)} 条已完成记录")

    def _clear_failed(self):
        failed = self.store.by_status("failed")
        if not failed:
            messagebox.showinfo("提示", "没有失败的记录")
            return
        if not messagebox.askyesno("清空失败记录",
                                   f"删除 {len(failed)} 条失败记录？（不删文件）\n"
                                   "要重下某条请先用『重试失败』，确认不要了再清。"):
            return
        for r in failed:
            self._remove_row(r.id)
        self._dirty = True
        self.push_log("info", f"已清空 {len(failed)} 条失败记录")

    def _on_tree_dclick(self, _evt):
        rid = self._selected_rid()
        if not rid:
            return
        rec = self.store.items[rid]
        if rec.status == "waiting":
            # 双击「待下载」行 = 直接下载
            self._submit_record(rid)
            self.push_log("ok", f"已开始下载: {rec.title[:30]}")
        elif rec.status == "done" and rec.file and os.path.exists(rec.file):
            try:
                os.startfile(rec.file)  # noqa
            except Exception as e:
                messagebox.showerror("打开失败", str(e))
        elif rec.status in ("failed", "stopped"):
            if rec.status == "failed":
                messagebox.showwarning("失败原因", rec.error or "未知错误")
            else:
                messagebox.showinfo("已停止", "该视频已被手动停止。"
                                      "\n点『重试失败』或『下载选中』可重新下载")

    # ---------------- 日志 ----------------
    def _append_log(self, level, msg):
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"[{datetime.datetime.now():%H:%M:%S}] {msg}\n",
                             level if level in ("info", "ok", "warn", "error") else "dim")
        n = int(self.log_text.index("end-1c").split(".")[0])
        if n > 1200:
            self.log_text.delete("1.0", f"{n - 1000}.0")
        if self.auto_scroll_var.get():
            self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

    def _copy_log(self):
        """把日志区内容（含版本/时间头）复制到剪贴板，便于发给开发者排查。"""
        try:
            body = self.log_text.get("1.0", "end-1c")
            header = (f"sphdl v{__version__}  |  {time.strftime('%Y-%m-%d %H:%M:%S')}"
                      f"  |  {platform.system()} {platform.release()}\n"
                      f"引擎: {'运行中' if self.engine.running else '已停止'}"
                      f" | 捕获 {self.addon.capture_count} | "
                      f"循环延迟 {self.addon.loop_delay_ms}ms\n"
                      + "-" * 56 + "\n")
            text = header + body
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            n = len(body.splitlines())
            self.push_log("ok", f"已复制 {n} 行日志到剪贴板（可直接粘贴发给开发者）")
        except Exception as e:
            self.push_log("error", f"复制日志失败: {e}")

    # ---------------- 树刷新 ----------------
    def _row_values(self, r: Record):
        st = STATUS_TEXT.get(r.status, r.status)
        if r.status == "downloading" and r.progress > 0:
            st += f" {r.progress}%"
        if r.status == "failed" and r.error:
            st += " ✗"
        title = r.title or "(无标题)"
        if r.is_image:
            title = "🖼 " + title  # 图片帖（不是视频），避免误以为是视频
        return (st, title,
                format_size(r.size) if r.size else "-",
                r.source, r.created[5:], Path(r.file).name if r.file else "")

    def _update_row(self, rid):
        r = self.store.items.get(rid)
        if not r:
            return
        iid = self._row_of.get(rid)
        if iid and self.tree.exists(iid):
            self.tree.item(iid, values=self._row_values(r),
                           tags=(STATUS_TAG.get(r.status, "tag_pending"),))

    def _remove_row(self, rid):
        # 同步清理 url 映射（可能有多条：切换清晰度后旧/新 url 都映射本记录）。
        # 否则删除记录后同 URL 再捕获会被误判「重复链接」而无法重新入列表。
        for k in [k for k, v in self._url_to_rid.items() if v == rid]:
            self._url_to_rid.pop(k, None)
        self.store.remove(rid)
        iid = self._row_of.pop(rid, None)
        if iid and self.tree.exists(iid):
            self.tree.delete(iid)

    def _add_row(self, r: Record):
        iid = self.tree.insert("", 0, values=self._row_values(r),
                               tags=(STATUS_TAG.get(r.status, "tag_pending"),))
        self._row_of[r.id] = iid
        self._url_to_rid[r.url] = r.id

    def _refresh_tree_all(self):
        self.tree.delete(*self.tree.get_children())
        self._row_of.clear()
        for rid in self.store.order:
            r = self.store.items.get(rid)
            if r:
                self._add_row(r)

    # ---------------- 事件处理（主线程） ----------------
    def _handle_event(self, item):
        kind = item[0]
        if kind == "engine_state":
            self._apply_engine_state(bool(item[1]))
        elif kind == "ca_state":
            self._apply_ca_state(item[1])
        elif kind == "capture":
            vd, source = item[1], item[2]
            # 跨会话去重：同一视频（video_id=md5sum）或同一 URL 已在列表 → 跳过。
            # （addon 的内存去重只在本次 GUI 运行内有效，重启后靠这里兜底）
            if vd.video_id and any(r.video_id == vd.video_id
                                   for r in self.store.items.values()):
                if vd.video_id not in self._known_logged:
                    self._known_logged.add(vd.video_id)
                    self.push_log("info", f"⏭ 重复视频（列表中已有），跳过: {(vd.description or '')[:30]}")
                return
            if vd.url in self._url_to_rid:
                if vd.url not in self._known_logged:
                    self._known_logged.add(vd.url)
                    self.push_log("info", f"⏭ 重复链接（列表中已有），跳过: {(vd.description or '')[:30]}")
                return
            rid = time.strftime("%m%d_%H%M%S") + "_" + str(self._rid_seq).zfill(4)
            self._rid_seq += 1
            rec = Record(
                id=rid, title=(vd.description or "")[:60], url=vd.url,
                decode_key=str(vd.decode_key or ""), size=int(vd.size or 0),
                source=source, created=time.strftime("%m-%d %H:%M:%S"),
                status="waiting", is_image=vd.is_image, video_id=vd.video_id,
                renditions=vd.renditions or [], quality_note=vd.quality_note or "")
            self.store.add(rec)
            self._add_row(rec)
            self._dirty = True
            self.push_log("ok", f"已捕获，加入待下载列表: {rec.title[:30]}"
                              f"（选中后点『下载选中』开始下载）")
        elif kind in ("start", "progress", "decrypting", "done", "failed", "cancelled"):
            vd = item[1]
            rid = self._url_to_rid.get(vd.url)
            if not rid:
                return
            if kind == "start":
                self.store.update(rid, status="downloading", progress=0, error="")
            elif kind == "progress":
                done, total = item[2], item[3]
                if total and total > 0:
                    pct = min(99, done * 100 // total)
                    if pct >= (self.store.items[rid].progress or 0):
                        self.store.update(rid, progress=pct)
            elif kind == "decrypting":
                self.store.update(rid, status="decrypting")
            elif kind == "done":
                rec = self.store.items[rid]
                actual = 0
                try:
                    if item[2] and os.path.exists(item[2]):
                        actual = os.path.getsize(item[2])
                except Exception:
                    pass
                # 元数据大小（最高档）仍保存在 renditions 里，用于对比提示
                meta = 0
                if rec.renditions:
                    meta = int(rec.renditions[0].get("size") or 0)
                if meta and actual and actual < meta * 0.5:
                    self.push_log(
                        "warn",
                        f"实际大小 {format_size(actual)} 仅为微信元数据"
                        f"（{format_size(meta)}，对应最高画质）的"
                        f"{actual * 100 // meta}%。\n"
                        "下载地址是微信客户端当时播放的档位，文件完整可播。"
                        "若想下载更高画质：在微信视频号播放器里把画质调到"
                        "最高（或保证网络良好）后重新播放，再重新捕获；"
                        "若抓包含『原始画质』选项，可用『清晰度』切换。")
                self.store.update(rid, status="done", progress=100,
                                  file=item[2], error="",
                                  size=actual or rec.size)
            elif kind == "failed":
                self.store.update(rid, status="failed", error=item[2])
            elif kind == "cancelled":
                # 用户停止（管线确认）：以 stopped 收尾
                self.store.update(rid, status="stopped",
                                  error=item[2] if len(item) > 2 else "用户停止")
            self._update_row(rid)
            self._dirty = True
            self._update_status_bar()

    def _update_status_bar(self):
        engine_txt = (f"嗅探: 运行中 127.0.0.1:{self.engine.port}"
                      if self.engine.running else "嗅探: 已停止")
        proxy_txt = "系统代理: 已接管" if self.engine.running and self.engine.pm \
            else "系统代理: 未接管"
        ca_txt = {"installed": "CA: ✓", "missing": "CA: ⚠ 未安装",
                  "unknown": "CA: ?", "checking": "CA: …"}[self.ca_state]
        if self.engine.running:
            a = self.addon
            if a.last_bridge_ts:
                age = int(time.time() - a.last_bridge_ts)
                hook_txt = (f"钩子: {age}s前" if age < 120
                            else f"钩子: {age // 60}分钟前 ⚠")
            else:
                hook_txt = "钩子: 无 ⚠"
        else:
            hook_txt = "钩子: -"
        done = len(self.store.by_status("done"))
        failed = len(self.store.by_status("failed"))
        waiting = len(self.store.by_status("waiting"))
        total = len(self.store.order)
        batch_txt = ""
        if self.addon.batch_mode:
            batch_txt = (f"   |   ⚡批量: "
                         f"{'拉取中' if self.addon._batch_active else '待重进页面'}"
                         f"(+{self.addon.batch_count})")
        self.status_bar.config(
            text=f"  {engine_txt}   |   {proxy_txt}   |   {ca_txt}   |   "
                 f"{hook_txt}   |   列表: 共 {total} 条 · 待下载 {waiting}"
                 f" · 完成 {done} · 失败 {failed}{batch_txt}")

    def _hook_watchdog(self):
        """钩子存活看门狗：视频正在播放（206 流经过代理）但长时间收不到
        任何钩子数据（心跳/数据）→ 页面大概率已重载/被回收、钩子丢失，
        捕获处于静默停摆 → 打醒目日志引导用户重新进入页面，替代以前
        "无日志、只能靠重启引擎碰运气"的状态。"""
        eng = self.engine
        if not eng.running:
            self._hook_warned = False
            return
        a = self.addon
        now = time.time()
        bridge_age = now - a.last_bridge_ts if a.last_bridge_ts else 1e9
        stream_age = now - a.last_stream_ts if a.last_stream_ts else 1e9
        streaming = stream_age < 90  # 90s 内有视频流 = 用户正在播放
        if streaming and bridge_age > 90 and not self._hook_warned:
            self._hook_warned = True
            self.push_log(
                "warn",
                "⚠️ 视频在播放，但 90 秒以上没有收到视频号页面的钩子数据"
                "——页面可能已刷新/被回收、钩子丢失（捕获暂停）。\n"
                "处理：返回视频号页面重新进入（或重启微信后再播放）；"
                "也可重启嗅探引擎，下次打开页面会重新注入。")
        if not streaming or bridge_age < 90:
            self._hook_warned = False
        # 周期性心跳日志（60s 一条）：引擎/钩子健康状态可追溯
        if now - self._last_hb_log >= 60:
            self._last_hb_log = now

            def _age(t):
                return "无" if not t else f"{int(now - t)}s前"

            batch_txt = ""
            if a.batch_mode:
                batch_txt = (f" | 批量 {_age(a.last_batch_ts)}"
                             f"·新增{a.batch_count}条")
            self.push_log(
                "info",
                f"💓 嗅探运行中 | 钩子数据 {_age(a.last_bridge_ts)} "
                f"(心跳×{a.hb_count} / 数据×{a.bridge_count}) | "
                f"流活动 {_age(a.last_stream_ts)} | "
                f"循环延迟 {a.loop_delay_ms}ms | "
                f"页面注入 {a.bridge_hooked_pages} 次 | 捕获 {a.capture_count} 个"
                f"{batch_txt}")

    def _poll(self):
        try:
            while True:
                lvl, msg = self.log_q.get_nowait()
                self._append_log(lvl, msg)
        except queue.Empty:
            pass
        try:
            while True:
                self._handle_event(self.ui_q.get_nowait())
        except queue.Empty:
            pass
        if self._dirty:
            self.store.save()
            self._dirty = False
        self._hook_watchdog()
        self._update_status_bar()
        self.root.after(self.POLL_MS, self._poll)

    # ---------------- 退出 ----------------
    def _on_close(self):
        if self.engine.running:
            if not messagebox.askyesno("退出确认",
                                       "嗅探引擎正在运行。\n停止嗅探（并恢复系统代理）并退出？"):
                return
            self.btn_engine.config(state="disabled", text="退出中…")
            self.engine.stop()
        self.cfg.save()
        self.store.save()
        self.root.destroy()

    def run(self):
        self.push_log("info", "欢迎使用 sphdl 视频号下载器")
        self.push_log("info", "① 点『启动嗅探』→ 打开微信视频号播放视频")
        self.push_log("info", "② 视频被捕获后进入列表（状态=待下载，不会自动下载）")
        self.push_log("info", "③ 选中要下载的视频（可多选）→ 点『⬇ 下载选中』或双击该行")
        self.root.mainloop()


def main():
    # Windows 单实例：命名互斥量。防止重复启动多个 GUI
    # 抢同一个 8899 端口 / 反复改写系统代理。
    if platform.system().lower() == "windows":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        mutex = kernel32.CreateMutexW(None, False, "Local\\sphdl_single_instance")
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(mutex)
            root = tk.Tk()
            root.withdraw()
            messagebox.showwarning(
                "已在运行",
                "sphdl 已经在运行中。\n请找到已有窗口继续使用，不要重复启动多个实例。")
            root.destroy()
            return
        # 句柄须存活至进程结束（GC 回收会提前释放互斥量）
        globals()["_SINGLE_INSTANCE_MUTEX"] = mutex
    App().run()


if __name__ == "__main__":
    main()
