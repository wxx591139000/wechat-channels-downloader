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
import uuid
import tkinter as tk
from dataclasses import dataclass, asdict, field
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

# 应用主目录（开发=sphdl/，冻结打包=exe 所在目录 → 便携布局）
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sphdl import config, license as lic, __version__   # noqa: E402
from sphdl.entities import VideoData           # noqa: E402
from sphdl.logger import logger                # noqa: E402
from sphdl.pipeline import Pipeline            # noqa: E402
from sphdl.addon import SphAddon               # noqa: E402
from sphdl.proxy_manager import ProxyManager, restore_proxy   # noqa: E402
from sphdl.downloader import format_size       # noqa: E402

CONFIG_FILE = BASE_DIR / "config.json"
RECORDS_FILE = BASE_DIR / "records.json"

# 列表容量档位（v2.0.17）：真实档位由 AppConfig.max_records 驱动（500/1000/2000/0=不限）。
# 这里保留常量为「默认档」，仅为兼容旧引用与测试。
MAX_RECORDS = config.DEFAULT_MAX_RECORDS
MAX_RECORDS_TIERS = config.MAX_RECORDS_TIERS
MAX_RECORDS_LABELS = config.MAX_RECORDS_LABELS

STATUS_TEXT = {
    "waiting": "待下载",
    "pending": "排队中",
    "downloading": "下载中",
    "decrypting": "解密中",
    "done": "已完成 ✓",
    "failed": "失败 ✗",
    "stopped": "已停止",
}

# 表头排序（v2.0.12）：按「状态」排序时的优先级（升序=正在进行的在前）
STATUS_SORT_ORDER = {"downloading": 0, "decrypting": 1, "pending": 2,
                     "waiting": 3, "done": 4, "failed": 5, "stopped": 6}

# 内置「使用说明」内容（v2.0.16）：首次启动自动弹出，也可点按钮随时查看
GUIDE_TEXT = """\
【快速上手 · 约 2 分钟】
① 首次使用：点「安装/检测」按向导装好 CA 证书（只需一次）
② 点「▶ 启动嗅探」——会自动接管系统代理
③ 打开微信 → 视频号 → 播放/浏览视频
   ⚠ 页面若是在启动嗅探之前打开的，请【完全关闭视频号页面再重新进入】，
     让工具把捕获钩子注入页面；只刷新可能仍走缓存
④ 视频进入列表后：
   · 手动下载：选中行点「⬇ 下载选中」（可 Ctrl/Shift 多选）
   · 自动下载：勾选「⚙ 自动下载」，新捕获的视频自动排队下载
   · 整个视频号全下：点「⚡ 批量拉取」，页面自动滚动拉到底
⑤ 完成后到下载目录查看 MP4；用完点「停止嗅探」自动恢复系统代理

【小技巧】
· 鼠标悬停任意按钮/开关 2 秒，会弹出该控件的用法与注意点说明
· 「🎯 自然捕获」= 只捕获当前播放的视频+随后几条，不会整页入库
· 「列表上限」= 视频清单容量档位（v2.0.17，由发布方授权管控）：
  · 档位共 4 档：轻量（500 条）/ 标准（1000 条）/ 旗舰（2000 条）/
    至尊（2 万条）；当前档位显示在按钮左侧
  · **列表填满档位后不再加载后续视频**（不会顶掉已有记录），日志提示「列表已满」
  · **填满后可删掉部分记录**，列表低于上限即自动恢复加载新视频
    （买小套餐只要勤删记录，就能一直用来拉新视频）
  · 想提升容量：点「🔑 授权管理」→ 粘贴发布方提供的授权码 → 点「激活」，
    **立即生效、无需重启**
  · 换码随时可换（升级/降级都行）；降档会裁掉最旧的超出记录（磁盘文件保留）
  · 想一次拉完整个视频号（几百上千条）：需先授权「旗舰」或「至尊」
  · 满档期间未加载的视频不会被记成「已捕获」，升级档位或删记录后重新播放/
    重新拉取即可加载；授权码无效/过期时会回落到默认档（记录不删，磁盘文件保留）
· 列表点击表头可按 状态/大小/时间 升降序排列
· 「📋 复制日志」= 出问题一键复制日志发给开发者
· 「停止所有下载 / 取消所有排队 / 删除全部记录」= 批量任务管理

【状态栏怎么看】
· 列表: 共 X 条 / 上限 N = 当前清单条数与容量档位（「上限 不限」= 无上限）
  · 出现「（已满·未加载 N 条）」= 列表已满，这 N 条新视频没有加载
· 钩子: Xs前 = 页面钩子正常；「钩子: 无 ⚠」= 请重新进入视频号页面
· ⚡批量: 拉取中(+N) = 正在批量拉取，被其他窗口遮挡也会继续
"""
STATUS_TAG = {
    "done": "tag_done", "failed": "tag_failed", "downloading": "tag_downloading",
    "decrypting": "tag_decrypting", "pending": "tag_pending",
    "waiting": "tag_waiting", "stopped": "tag_stopped",
}


# ============================== 悬停提示（v2.0.11） ==============================
class ToolTip:
    """控件悬停提示：鼠标停留 delay_ms 毫秒后弹出说明，移开/点击即消失。"""

    def __init__(self, widget, text, delay_ms=2000):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._tip = None
        self._after_id = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel_timer()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel_timer(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self):
        self._after_id = None
        if self._tip is not None:
            return
        try:
            if not self.widget.winfo_exists():
                return
            x = self.widget.winfo_rootx() + 14
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
            top = tk.Toplevel(self.widget)
            top.wm_overrideredirect(True)
            top.wm_geometry(f"+{x}+{y}")
            tk.Label(top, text=self.text, justify="left",
                     bg="#fffbe6", fg="#1f2937", relief="solid", bd=1,
                     padx=10, pady=8, font=("Microsoft YaHei UI", 9),
                     wraplength=430).pack()
            top.update_idletasks()
            # 底部放不下时改到控件上方
            sh = self.widget.winfo_screenheight()
            if y + top.winfo_height() > sh:
                y = self.widget.winfo_rooty() - top.winfo_height() - 8
                top.wm_geometry(f"+{x}+{y}")
            self._tip = top
            top.bind("<Leave>", self._hide)
            top.bind("<ButtonPress>", self._hide)
        except Exception:
            self._tip = None

    def _hide(self, _e=None):
        self._cancel_timer()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


# ============================== 配置持久化 ==============================
class AppConfig:
    def __init__(self):
        self.port = config.PROXY_PORT
        self.download_dir = str(config.DOWNLOAD_DIR)
        self.auto_proxy = True
        self.auto_download = False   # 默认手动：捕获只进列表，用户点选后下载（v1.2.5 开关可开）
        self.prefer_highest_quality = False  # 默认下载最高清晰度（原始画质优先）
        # 图片捕获开关（v2.0.4 引入，v2.0.19 起**默认关闭**）：
        #   本工具主打下载视频，图片帖混进列表会干扰视频挑选与批量操作，
        #   因此出厂默认不捕获图片；需要的用户在「捕获设置」里自行勾选。
        #   关闭时：图片不进列表、不参与下载/去重（已下载的图片文件保留）。
        self.download_images = False
        self.natural_capture = True   # 默认自然捕获：当前播放+随后几条，不整页入库（v2.0.7）
        self.guide_auto_shown = False  # 内置使用说明是否已自动弹出过（v2.0.16）
        # 列表容量档位（v2.0.17）：**客户端不可自行配置**
        #   ① 构建预置档位（release.py --max-records）→ 写死，无需授权码
        #   ② 否则由授权码决定（sphdl/license.py），未授权时用默认档
        self.locked_max_records = config.locked_max_records()
        self.default_max_records = config.build_default_max_records()
        # ★ v2.0.19：授权码**按产品线分别保存**。
        #   视频号 4 档 与 公众号视频 是两条独立产品线，各自一个码；
        #   以前只有单个 license_code 字段，激活新产品会把另一个顶掉
        #   （2026-10-17 客户实测 bug）。现在：
        #     license_codes = {产品号: 码}，互不影响
        #   license_code 保留为"最近一次激活的码"，仅用于兼容旧配置/展示。
        self.license_codes = {}          # {1: 视频号码, 2: 公众号码}
        self.license_activated_at = {}   # {产品号: 首次激活时间戳}
        self.license_code = ""           # 兼容旧字段（最近激活的码）
        self.license_activated_at_legacy = 0.0
        self._load()

    def _load(self):
        # ★ v2.1.8：配置自动备份 + 损坏恢复（客户实测「重启后授权丢失」）
        #
        #   真实原因不是授权失效，而是 config.json 出问题后**静默回默认档**：
        #     · 写入中途断电/磁盘满 → JSON 截断
        #     · 杀软/清理工具误删 config.json
        #   客户看到的就是"授权没了"，而且程序一个字都不提示。
        #
        #   策略：每次成功加载后存一份 config.json.bak；
        #        加载失败 → 尝试从 .bak 恢复 → 恢复不了才回默认，并记下原因
        #        供界面显示（客户不会以为是自己码坏了）。
        self._config_load_error = ""
        data = None
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            # 首次运行 or 文件被删 —— 若有备份，说明是"曾经用过"
            bak = CONFIG_FILE.with_suffix(".json.bak")
            if bak.exists():
                try:
                    data = json.loads(bak.read_text(encoding="utf-8"))
                    self._config_load_error = (
                        f"配置文件 config.json 丢失，已从备份 config.json.bak 恢复"
                        f"（含授权码）。原文件可能被杀毒软件或清理工具删除。")
                except Exception:
                    self._config_load_error = (
                        "配置文件 config.json 丢失，备份也无法读取，授权需要重新激活。")
        except Exception as e:                      # noqa: BLE001
            # JSON 损坏（断电/磁盘满/写入中断）
            bak = CONFIG_FILE.with_suffix(".json.bak")
            if bak.exists():
                try:
                    data = json.loads(bak.read_text(encoding="utf-8"))
                    self._config_load_error = (
                        f"配置文件损坏（{type(e).__name__}），已从备份恢复（含授权码）。"
                        f"损坏原因通常是写入时断电或磁盘满。")
                except Exception:
                    self._config_load_error = (
                        f"配置文件损坏（{type(e).__name__}），备份也无法读取，"
                        f"授权需要重新激活。")
            else:
                self._config_load_error = (
                    f"配置文件损坏（{type(e).__name__}），且没有备份，"
                    f"授权需要重新激活。")

        if data is None:
            return                                  # 没配置可读 → 用出厂默认

        try:
            d = data
            self.port = int(d.get("port", self.port))
            self.download_dir = d.get("download_dir", self.download_dir)
            self.auto_proxy = bool(d.get("auto_proxy", True))
            self.auto_download = bool(d.get("auto_download", False))
            self.prefer_highest_quality = bool(d.get("prefer_highest_quality", False))
            # v2.0.19：缺省改为 False（与出厂默认一致）。老配置文件里
            # 显式存了 true 的仍按 true 生效，不改变老用户既有行为。
            self.download_images = bool(d.get("download_images", False))
            self.natural_capture = bool(d.get("natural_capture", True))
            self.guide_auto_shown = bool(d.get("guide_auto_shown", False))
            # ---- v2.0.19：按产品线恢复授权码 ----
            self.license_codes = {}
            raw_codes = d.get("license_codes") or {}
            if isinstance(raw_codes, dict):
                for k, v in raw_codes.items():
                    try:
                        if v:
                            self.license_codes[int(k)] = str(v)
                    except Exception:
                        continue
            raw_at = d.get("license_activated_at") or {}
            self.license_activated_at = {}
            if isinstance(raw_at, dict):
                for k, v in raw_at.items():
                    try:
                        self.license_activated_at[int(k)] = float(v or 0)
                    except Exception:
                        continue
            # 旧配置迁移：单个 license_code → 按码里的产品号归档
            legacy = str(d.get("license_code", "") or "")
            self.license_code = legacy
            try:
                self.license_activated_at_legacy = float(
                    d.get("license_activated_at", 0) or 0)
            except Exception:
                self.license_activated_at_legacy = 0.0
            if legacy and not self.license_codes:
                try:
                    from sphdl import license as _lic
                    info = _lic.verify_code(legacy)
                    if info:
                        prod = int(info.get("product") or 1)
                        self.license_codes[prod] = legacy
                        self.license_activated_at[prod] = \
                            self.license_activated_at_legacy
                except Exception:
                    pass
        except Exception:
            pass

    def code_of(self, product: int) -> str:
        """取某产品线的授权码（无则空串）。"""
        try:
            return self.license_codes.get(int(product), "") or ""
        except Exception:
            return ""

    def set_code(self, product: int, code: str):
        """设置某产品线的授权码（不影响其他产品线）。"""
        try:
            self.license_codes[int(product)] = code or ""
        except Exception:
            pass

    def activated_at_of(self, product: int) -> float:
        try:
            return float(self.license_activated_at.get(int(product), 0) or 0)
        except Exception:
            return 0.0

    def set_activated_at(self, product: int, ts: float):
        try:
            self.license_activated_at[int(product)] = float(ts or 0)
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
                 "natural_capture": self.natural_capture,
                 "guide_auto_shown": self.guide_auto_shown,
                 # v2.0.19：按产品线分别存（视频号 / 公众号 互不覆盖）
                 "license_codes": {str(k): v
                                   for k, v in (self.license_codes or {}).items()
                                   if v},
                 "license_activated_at": {
                     str(k): v for k, v in
                     (self.license_activated_at or {}).items()},
                 # 兼容旧版读取
                 "license_code": self.license_code,
                 "license_activated_at_legacy":
                     self.license_activated_at_legacy},
                ensure_ascii=False, indent=2), encoding="utf-8")
            # ★ v2.1.8：写成功后同步一份备份（供 _load 损坏时恢复）
            #   用 copy 而不是再序列化一遍，保证 .bak 与主文件字节一致。
            try:
                import shutil as _sh
                _sh.copy2(CONFIG_FILE, CONFIG_FILE.with_suffix(".json.bak"))
            except Exception:
                pass
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
    # v2.0.19：来源平台，用于统一清单里区分/筛选
    #   "视频号"（默认，嗅探捕获） / "公众号"（文章解析）
    platform: str = "视频号"
    # ★ v2.2.1：抓取序号（客户需求）
    #   捕获时递增分配（1,2,3...），**永不改变**。
    #   用途：客户在视频号里按某个顺序刷，希望下载顺序与之一致。
    #   点「序号」表头排序 = 按抓取先后排列。
    #   老记录没有这个字段 → 用 0 兜底（排序时排在最前，由 id 二次排序保证稳定）
    seq: int = 0


class RecordStore:
    """列表持久化。容量档位（v2.0.17）由 max_records 决定：
    500=轻量 / 1000=标准 / 2000=旗舰 / 20000=至尊（全部为确定数值）。

    约定：add() / set_max_records() 只负责数据结构，**返回被淘汰的记录 id 列表**，
    由 GUI 侧负责同步删除 Treeview 行与去重映射（修复 v2.0.16 的
    「幽灵行 + 被淘汰视频永久去重黑洞」，见 docs/SPEC-列表上限四档.md R3/R4）。
    """

    def __init__(self, path: Path, max_records: int = config.DEFAULT_MAX_RECORDS):
        self.path = path
        self.max_records = int(max_records or 0)
        self.items: dict = {}      # id -> Record
        self.order: list = []      # 新 → 旧
        self.trimmed_on_load = 0   # 启动时因上限被裁掉的旧记录数（供 GUI 提示）
        self._load()

    def _load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raw = []
            if self.max_records > 0 and len(raw) > self.max_records:
                self.trimmed_on_load = len(raw) - self.max_records
                raw = raw[:self.max_records]
            for d in raw:
                kw = {k: d.get(k, "") for k in
                      ("id", "title", "url", "decode_key", "size",
                       "source", "created", "status", "file", "error",
                       "progress", "is_image", "video_id", "quality_note",
                       "platform")}
                kw["renditions"] = d.get("renditions") or []
                # ★ v2.2.1：seq 必须显式读回，否则重启后抓取序号全丢
                #   （白名单式读取容易漏字段 —— 加新字段时务必同步这里）
                try:
                    kw["seq"] = int(d.get("seq") or 0)
                except Exception:
                    kw["seq"] = 0
                if not kw.get("platform"):
                    kw["platform"] = "视频号"   # 老数据默认视频号
                r = Record(**kw)
                # 上次退出时正在处理中的，标记为失败；
                # "waiting"（还没开始下载）保持不变，重启后仍可点选下载
                if r.status in ("pending", "downloading", "decrypting"):
                    r.status = "failed"
                    r.error = "已中断（程序重启）"
                    r.progress = 0
                self.items[r.id] = r
                self.order.append(r.id)

            # ★ v2.2.1：给老记录（升级前没有 seq 的）补抓取序号
            #   按 created（时间戳字符串）从小到大补 1,2,3...
            #   这样老客户升级后，序号也能反映"抓取先后"。
            _missing = [rid for rid in self.order
                        if int(getattr(self.items.get(rid), "seq", 0) or 0) <= 0]
            if _missing:
                _sorted_old = sorted(
                    _missing,
                    key=lambda x: (getattr(self.items[x], "created", "") or "", x))
                _mx = max([int(getattr(self.items[r2], "seq", 0) or 0)
                           for r2 in self.order] or [0])
                for _i, _rid in enumerate(_sorted_old, 1):
                    try:
                        self.items[_rid].seq = _mx + _i
                    except Exception:
                        pass
        except FileNotFoundError:
            pass
        except Exception:
            self.items, self.order = {}, []

    def is_full(self) -> bool:
        """列表是否已达档位上限。

        v2.0.17 定稿：4 档全部为确定数值（无「不限量」概念），
        max_records 恒为正数，达到即满。

        ★ v2.0.19：**只统计视频号记录**。公众号视频是独立产品（不分档），
          不占用视频号的档位名额，否则买了轻量的客户解析几十个公众号
          视频后，视频号就再也抓不进来了。

        ★ v2.1.7：未授权（max_records <= 0）不再"永不满档"，改为按
          `UNLICENSED_CAPTURE_LIMIT`（500 条）封顶。原因见 config.py 的说明：
          完全不限会让客户抓几万条，排序 5 万条要 38 秒（PITFALLS #7），
          界面会卡死。500 条足够看清工具确实有用，又不会拖慢。
        """
        limit = self.max_records
        if limit <= 0:
            limit = config.UNLICENSED_CAPTURE_LIMIT
        if limit <= 0:
            return False
        n = sum(1 for rid in self.order
                if (getattr(self.items.get(rid), "platform", "视频号")
                    or "视频号") == "视频号")
        return n >= limit

    def _trim_to_limit(self) -> list:
        """把超出档位的部分裁掉（只可能发生在**下调档位**时）。

        返回被裁掉的 **Record 对象** 列表——返回对象而非 id，是因为记录已从
        items 删除、取不回 video_id，而 GUI 必须用它清去重索引（v2.0.17）。

        ★ v2.0.19：**只统计/裁剪视频号记录**。公众号视频是独立产品（不分档），
          既不占用档位名额，也不该在下调档位时被误删。
        """
        removed = []
        if self.max_records > 0:
            def _is_video(rid):
                r = self.items.get(rid)
                return r is not None and (getattr(r, "platform", "视频号")
                                          or "视频号") == "视频号"
            while sum(1 for x in self.order if _is_video(x)) > self.max_records:
                # 从最旧的一端找第一条视频号记录裁掉
                for rid in reversed(self.order):
                    if _is_video(rid):
                        self.order.remove(rid)
                        rec = self.items.pop(rid, None)
                        if rec is not None:
                            removed.append(rec)
                        break
                else:
                    break
        return removed

    def add(self, rec: Record) -> bool:
        """新记录入列表头。**已满则拒收**（v2.0.17：满档 = 不再加载后续视频，
        不顶掉已有记录），返回是否入库成功。

        ★ v2.0.19：满档只对**视频号**生效。公众号视频是独立产品（不分档），
          即使视频号已满也必须能入库 —— 否则客户买了公众号版却加不进去。
        """
        is_video = (getattr(rec, "platform", "视频号") or "视频号") == "视频号"
        if is_video and self.is_full():
            return False
        self.items[rec.id] = rec
        self.order.insert(0, rec.id)
        return True

    def set_max_records(self, n: int) -> list:
        """调整档位；仅当**下调**时裁掉最旧的超出部分（返回被裁掉的 Record 列表）。

        上调档位不删除任何记录；上调后若列表不再满，会自动恢复接收新视频。
        """
        self.max_records = int(n or 0)
        return self._trim_to_limit()

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

    def save(self) -> bool:
        """原子落盘：先写 .tmp 再 os.replace 替换。

        避免 v2.0.16 全量直接改写 records.json 时（大数据量下）掉电/崩溃
        留下半截 JSON → 下次启动整份记录丢失（_load 会兜底清空）。
        """
        try:
            data = [asdict(self.items[i]) for i in self.order if i in self.items]
            blob = json.dumps(data, ensure_ascii=False, indent=1)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(blob, encoding="utf-8")
            os.replace(tmp, self.path)
            return True
        except Exception:
            return False


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
                # ★ v2.0.19：记录嗅探生效时刻，供「零流量」诊断计时
                try:
                    self.app._sniff_started_ts = time.time()
                    self.app._no_traffic_warned = False
                except Exception:
                    pass
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
def _win32_parent(hwnd):
    """取父窗口句柄（无则 0）。失败返回 0，不抛异常。"""
    try:
        import ctypes
        return int(ctypes.windll.user32.GetParent(int(hwnd)) or 0)
    except Exception:
        return 0


def _win32_foreground():
    """当前前台窗口句柄（失败返回 0）。"""
    try:
        import ctypes
        return int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except Exception:
        return 0


class App:
    POLL_MS = 300
    SAVE_MIN_INTERVAL = 3.0   # records.json 落盘最小间隔（秒，v2.0.17 节流）

    def __init__(self):
        # ★ v2.0.19 性能：**第一件事**就启动后台采集设备指纹。
        #   首次采集要调 PowerShell（1~2 秒），放在主线程会让启动卡住
        #   （用户实测"启动卡顿 4 秒"）。这里让它在后台线程跑，
        #   与界面构建并行，界面秒出；水印/横幅先显示版本号占位，
        #   就绪后自动补上设备码。
        try:
            from . import telemetry as _tel0
            _tel0.warm_up_async()
        except Exception:
            pass
        self.cfg = AppConfig()
        self.ui_q: "queue.Queue" = queue.Queue()
        self.log_q: "queue.Queue" = queue.Queue()
        logger.subscribe(self._on_log)

        self.store = RecordStore(RECORDS_FILE, self._resolve_tier())
        self.engine = SniffEngine(self)

        self.ca_state = "checking"     # checking / installed / missing / unknown
        self._dirty = False
        self._row_of: dict = {}        # record id -> treeview iid
        self._url_to_rid: dict = {}    # url -> record id
        self._vid_index: set = set()   # video_id 集合（v2.0.17：去重 O(1)，不再线性扫全表）
        self._known_logged: set = set()  # 已提示过"列表中已有"的 video_id/url（防刷屏）
        self._sort_col = None          # 表头排序（v2.0.12）：None=默认最新在前
        self._sort_desc = False
        self._rid_seq = 0              # 捕获记录 id 递增序号（防同秒碰撞）
        # 钩子看门狗状态（防"捕获静默停摆"，2026-08-25 v1.2.1）
        self._hook_warned = False
        self._last_hb_log = 0.0
        # 「零流量」诊断（v2.0.19）：嗅探启动时间戳 + 是否已提示过
        self._sniff_started_ts = 0.0
        self._no_traffic_warned = False
        # 列表容量档位（v2.0.17）：淘汰提示只打一次；落盘节流时间戳
        self._limit_warned = False
        self._rejected_count = 0       # 满档后拒收（未加载）的新视频数
        # ★ v2.1.6：批量拉取是否因「满档」被自动停掉（决定升档后要不要自动恢复）
        self._batch_stopped_by_full = False
        # ★ v2.2.0：点「停止嗅探」时选了「等下载完再停」
        self._pending_stop_after_downloads = False
        self._stop_timer_armed = False
        self._pending_slots = 0        # 已投递未处理的捕获占位数（并发防穿透）
        self._full_event_pending = False   # 满档事件是否已投递（防刷屏）
        self._last_save = 0.0
        for rid in self.store.order:
            r = self.store.items[rid]
            self._url_to_rid[r.url] = rid
            if r.video_id:
                self._vid_index.add(r.video_id)

        # 「🖼 下载图片」关闭时（v2.0.4）：启动即移除历史图片记录，
        # 列表不显示、不参与下载/去重；已下载的图片文件保留在磁盘，
        # 重新开启开关后需重新捕获图片
        if not self.cfg.download_images:
            dropped = 0
            for rid in [r.id for r in self.store.items.values() if r.is_image]:
                rec = self.store.items.get(rid)
                if rec is not None and rec.video_id:
                    self._vid_index.discard(rec.video_id)
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

        # 启动裁剪提示（v2.0.17）：records.json 里超出当前档位的旧记录已被裁掉，
        # 必须显式告知（v2.0.16 是静默丢弃 → 用户以为"数据丢了"）。
        if self.store.trimmed_on_load:
            self.push_log(
                "warn",
                f"⚠️ 列表上限 {self._limit_text()}：启动时已裁掉 {self.store.trimmed_on_load} "
                "条最早的历史记录（磁盘文件不受影响）。\n"
                "如需保留更多记录，请把「列表上限」调到更大档位后重新拉取。")
        if self.store.is_full():
            self.push_log(
                "warn",
                f"⚠️ 列表已满（{self._tier_label_of()}）："
                "后续捕获的视频将不再加载。\n"
                "如需继续加载：点「🔑 授权管理」升级档位；"
                "或删除部分记录后继续拉取。")

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

        # 档位显示/状态栏首次刷新（此时 self.addon 已就绪）
        self._refresh_limit_display()

        # ★ v2.2.0：清理过期的断点文件（.tmp）
        #   保留 .tmp 能续传，但失败任务多了会占磁盘 →
        #   启动时清掉 7 天以上的残留（正在用的不受影响，改不了 mtime）
        try:
            threading.Thread(target=self._cleanup_stale_tmp, daemon=True).start()
        except Exception:
            pass

        # ★ v2.1.8：配置文件读取异常必须在界面上讲明白
        #   否则客户看到"授权没了"只会以为是自己码坏了 / 软件坏了，
        #   实际是 config.json 丢失或损坏（已从 .bak 恢复或需重新激活）。
        _cerr = getattr(self.cfg, "_config_load_error", "")
        if _cerr:
            try:
                self.push_log("warn", "⚠ " + _cerr)
                # 延迟一点弹窗，等主窗口画完再弹（否则弹窗可能跑到主窗后面）
                self.root.after(600, lambda m=_cerr: messagebox.showwarning(
                    "配置文件异常",
                    m + "\n\n"
                    "当前配置路径：\n"
                    f"{CONFIG_FILE}\n\n"
                    "如果授权需要重新激活，请点「🔑 授权管理」粘贴你原来"
                    "的授权码（同一个码可以重复激活，不会失效）。",
                    parent=self.root))
            except Exception:
                pass

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

    # ---------------- 列表上限档位（v2.0.17，客户端不可自配） ----------------
    def _limit_text(self, n=None) -> str:
        """档位容量文案（纯数字，如「1000」）；n 省略时取当前档位。"""
        n = self.store.max_records if n is None else int(n)
        return str(n)

    def _tier_name_of(self, n=None) -> str:
        """档位名称（轻量/标准/旗舰/至尊）；n 省略时取当前档位。"""
        n = self.store.max_records if n is None else int(n)
        return config.name_of(n)

    def _tier_label_of(self, n=None) -> str:
        """档位完整展示名（如「标准（1000 条）」）。"""
        n = self.store.max_records if n is None else int(n)
        return config.label_of(n)

    def _license_info(self, product: int = None):
        """授权解析（v2.0.19：按产品线）。

        product=None → 返回**任一有效产品**的信息（优先视频号），用于
                       界面通用展示；两条产品线的产品号都在返回值里。
        product=N    → 只返回该产品线的授权信息（无/无效 → None）。

        ★ 为什么按产品线：视频号与公众号是两条独立产品线，各自一个码；
          以前读单个 license_code，激活一个就把另一个顶掉（实测 bug）。

        ★ v2.0.19 设备绑定：绑定码若与本机不匹配，**视为无效授权**
          （返回 None → 档位回落到默认档、公众号功能锁定）。
          这样"拷到别人电脑上"的绑定码自动失效，无需额外流程。
          取不到本机设备码时不判失败（宁可放过，不可误伤）。
        """
        if product is not None:
            code = self.cfg.code_of(product)
            info = lic.verify_code(code) if code else None
            if info and int(info.get("product") or 0) == int(product):
                return self._check_bound(info)
            return None
        # 无指定产品：优先视频号，其次公众号
        for p in (lic.PRODUCT_VIDEO, lic.PRODUCT_MP_ARTICLE):
            code = self.cfg.code_of(p)
            info = lic.verify_code(code) if code else None
            if info:
                return self._check_bound(info)
        return None

    def _check_bound(self, info):
        """设备绑定校验（info 无效 / 非绑定码 → 原样返回；不匹配 → None）。

        ⚠️ 性能：设备采集要调 PowerShell（200~500ms），而 _license_info()
        会被界面频繁调用（状态栏、横幅等），**必须缓存**，只采一次。

        ★ v2.0.19：传**分量列表**给 check_device，才能享受"4 项取 2"的
        抗硬件变动能力（客户换硬盘/重装系统不会被误判为换机）。
        """
        if not info or not info.get("device_bound"):
            return info
        comps = getattr(self, "_dev_comps_cache", None)
        if comps is None:
            try:
                from . import telemetry as _tel
                comps = _tel.device_components()
            except Exception:
                comps = []
            self._dev_comps_cache = comps
        ok, _why = lic.check_device(info, components=comps)
        return info if ok else None

    def _license_expired(self) -> bool:
        """当前视频号授权是否过期（档位相关）。"""
        info = self._license_info(lic.PRODUCT_VIDEO)
        if not info:
            return False
        return lic.license_expired(
            info["days"], self.cfg.activated_at_of(lic.PRODUCT_VIDEO))

    def _resolve_tier(self) -> int:
        """档位解析（唯一入口）：
        ① 构建预置档位（写死）→ 用它
        ② 有效授权码（未过期）→ 用码里的档位
        ③ 否则 → 构建默认档（一般 500）

        ★ v2.0.19 关键修复：**公众号产品不影响视频号档位**。
          公众号码的 tier 是 0（不分档），如果直接返回 0，界面会显示
          "上限 0 条"、逻辑上等于一条视频都抓不进来（2026-10-17 实测 bug：
          客户粘了公众号码后列表上限从 500 变成 0）。
          公众号产品与视频号档位是两回事 → 遇到公众号码，档位回落到
          构建默认档（未买视频号就该是最小档），而不是 0。
        """
        if self.cfg.locked_max_records is not None:
            return self.cfg.locked_max_records
        # 档位只跟**视频号**产品线有关（公众号不分档）
        info = self._license_info(lic.PRODUCT_VIDEO)
        if info and not self._license_expired():
            tier = int(info.get("tier") or 0)
            if tier > 0:
                return tier
        return self.cfg.default_max_records
        return self.cfg.default_max_records

    def _tier_source_text(self) -> str:
        """档位来源文案（界面提示用）。"""
        if self.cfg.locked_max_records is not None:
            return "版本预置"
        info = self._license_info()
        if not info:
            return "未授权 · 默认档"
        if self._license_expired():
            return "授权已过期 · 默认档"
        if info["days"]:
            left = lic.days_left(info["days"], self.cfg.license_activated_at)
            return f"已授权 · 剩余 {left} 天"
        return "已授权 · 永久"

    def _refresh_limit_hint(self):
        """档位说明文案（随档位/授权状态变化）。"""
        n = self.store.max_records
        # v2.0.19：公众号产品不涉及视频号档位，说明文案要写清楚，
        # 否则客户看到「满 500 条即停止」会以为公众号视频也受限。
        info = self._license_info()
        if info and lic.is_mp_product(info) and not self._license_expired():
            head = ("当前授权为「公众号视频版」，不含视频号功能"
                    f"（视频号保持默认档 {n} 条）")
        else:
            head = (f"满 {n} 条即停止加载后续视频"
                    f"（删掉部分记录后可继续加载）")
        try:
            self.limit_hint.config(text=f"{head}　｜　{self._tier_source_text()}")
        except Exception:
            pass
        try:
            self.limit_value_lbl.config(text=self._tier_label_of(n))
        except Exception:
            pass
        try:
            # 「🔑 授权管理」按钮文案随授权状态变化：未授权→「🔑 授权管理」，
            # 已授权→「🔑 授权管理」（保持一致，状态在提示里体现）
            self.btn_license.config(text="🔑 授权管理")
        except Exception:
            pass

    def _refresh_limit_display(self):
        """界面控件建好后的首次刷新（等价 _refresh_limit_hint + 状态栏）。"""
        self._refresh_limit_hint()
        self._update_status_bar()

    def _apply_tier(self, n: int, reason: str):
        """切换生效档位（**立即生效，无需重启**）：调小会裁掉最旧记录
        （只删列表记录，磁盘文件保留）。"""
        old = self.store.max_records
        if int(n) == old:
            self._refresh_limit_hint()
            self._update_status_bar()
            return
        removed = self.store.set_max_records(n)
        self._drop_records_ui(removed)
        self._limit_warned = False          # 新档位下允许再次提示满档
        self._full_event_pending = False
        self._force_save()
        if removed:
            self.push_log("warn",
                          f"列表上限 {config.label_of(old)} → "
                          f"{config.label_of(n)}："
                          f"已裁掉最旧的 {len(removed)} 条记录（磁盘文件保留）")
        self.push_log("ok", f"列表上限 {config.label_of(old)} → "
                            f"{config.label_of(n)}（{reason}）")
        if not self.store.is_full():
            self.push_log("ok", "✅ 列表未满，已恢复加载新捕获的视频（无需重启）")
        # ★ v2.1.6：升档后若批量是被满档停的 → 自动恢复
        #   （_apply_tier 不一定经过 _after_records_removed，这里必须显式调一次）
        self._maybe_resume_batch()
        self._refresh_limit_hint()
        self._update_status_bar()

    def _clear_license(self, product: int = None):
        """清除授权（售后用）：回落到默认档。返回是否真的清除过。

        product=None → 清除**所有**产品线的授权（旧行为）
        product=N    → 只清除该产品线，另一条产品线保持不变

        ★ v2.0.19：必须同时清 license_codes（按产品线存的码）——
          只清旧的 license_code 字段会导致「点了清除授权但实际还有效」
          （2026-10-17 修复）。
        """
        had = bool((self.cfg.license_codes or {}).get(
            int(product))) if product is not None else bool(self.cfg.license_codes)
        if not had and not getattr(self.cfg, "license_code", ""):
            return False

        if product is None:
            self.cfg.license_codes = {}
            self.cfg.license_activated_at = {}
            self.cfg.license_code = ""
            self.cfg.license_activated_at_legacy = 0.0
            self.push_log("warn", "🗑 已清除全部授权，档位回落到默认档")
        else:
            try:
                self.cfg.license_codes.pop(int(product), None)
                self.cfg.license_activated_at.pop(int(product), None)
            except Exception:
                pass
            name = ("公众号视频版" if int(product) == lic.PRODUCT_MP_ARTICLE
                    else "视频号下载")
            self.push_log("warn", f"🗑 已清除「{name}」授权")
        self.cfg.save()
        self._apply_tier(self._resolve_tier(), reason="清除授权")
        try:
            self._refresh_mp_button()
            self._refresh_watermark()
        except Exception:
            pass
        return True

    # ---------------- 授权管理弹窗（v2.0.17） ----------------
    def _on_license_input(self):
        """「🔑 授权管理」：查看当前授权 / 更换授权码 / 清除授权。

        换码**立即生效、无需重启**：激活后直接刷新列表上限与界面状态。
        """
        if self.cfg.locked_max_records is not None:
            messagebox.showinfo(
                "无需授权",
                f"本版本档位已由发布方预置为「{config.label_of(self.cfg.locked_max_records)}」，"
                "无需（也不能）输入授权码。")
            return

        win = tk.Toplevel(self.root)
        win.title("授权管理")
        win.transient(self.root)
        win.resizable(False, False)

        pad = ttk.Frame(win, padding=14)
        pad.pack(fill="both", expand=True)

        info = self._license_info()
        cur_label = config.label_of(self.store.max_records)
        source = self._tier_source_text()

        # ---- 当前状态区 ----
        ttk.Label(pad, text="当前档位：", font=("Microsoft YaHei UI", 9)).grid(
            row=0, column=0, sticky="e", pady=(0, 2))
        ttk.Label(pad, text=cur_label,
                  font=("Microsoft YaHei UI", 10, "bold"),
                  foreground="#0f766e").grid(row=0, column=1, columnspan=3,
                                             sticky="w", pady=(0, 2))
        ttk.Label(pad, text="授权状态：").grid(row=1, column=0, sticky="e",
                                               pady=(0, 2))
        ttk.Label(pad, text=source, foreground="#374151").grid(
            row=1, column=1, columnspan=3, sticky="w", pady=(0, 2))

        # ---- 功能授权（v2.0.19）：视频号 / 公众号文章视频 ----
        # 公众号视频是**附加模块**，默认关闭，需专门授权码开启。
        _has_vc = lic.has_feature(info, lic.FLAG_VIDEO_CHANNEL) or info is None
        _has_mp = lic.has_feature(info, lic.FLAG_MP_ARTICLE_VIDEO)
        feat_row = ttk.Frame(pad)
        feat_row.grid(row=1, column=2, columnspan=2, sticky="w",
                      padx=(12, 0), pady=(0, 2))
        ttk.Label(feat_row, text="功能：", foreground="#6b7280",
                  font=("Microsoft YaHei UI", 8)).pack(side="left")
        ttk.Label(feat_row, text="视频号✓",
                  foreground="#0f766e" if _has_vc else "#9ca3af",
                  font=("Microsoft YaHei UI", 8)).pack(side="left")
        ttk.Label(feat_row, text="公众号视频" + ("✓" if _has_mp else "✗"),
                  foreground="#0f766e" if _has_mp else "#b91c1c",
                  font=("Microsoft YaHei UI", 8, "bold")
                  ).pack(side="left", padx=(6, 0))

        # ---- 本机设备码（v2.0.19 设备绑定）----
        # 客户买绑定版时，需要把这个码发给作者，作者据此生成绑定码。
        # 放在显眼处 + 一键复制按钮，避免客户手抄出错。
        try:
            from . import telemetry as _tel
            # 一次采集，同时得到展示码和绑定串（PowerShell 200~500ms，别采两遍）
            import hashlib as _hl
            _comps0 = _tel.device_components()
            _fp = _hl.sha256("|".join(_comps0).encode()).hexdigest()
            _dev_txt = _tel.device_display(_fp) or "（读取失败）"
            _tok = _tel.bind_token(_comps0)
        except Exception:
            _dev_txt = "（读取失败）"
            _tok = ""
        ttk.Label(pad, text="本机设备码：").grid(row=2, column=0, sticky="e",
                                            pady=(0, 4))
        tk.Label(pad, text=_dev_txt, fg="#1e3a5f",
                 font=("Consolas", 11, "bold")).grid(
            row=2, column=1, sticky="w", pady=(0, 4))

        def _copy_dev():
            # 用已缓存的绑定串（避免重复采集：PowerShell 每次 200~500ms）
            txt = f"{_dev_txt}" + (f"  (绑定串 {_tok})" if _tok else "")
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(txt)
                hint_var.set(f"✅ 已复制：{txt}")
            except Exception:
                hint_var.set("复制失败，请手动选中设备码复制")

        _dev_btn = ttk.Button(pad, text="📋 复制", width=8, command=_copy_dev)
        _dev_btn.grid(row=2, column=2, sticky="w", padx=(8, 0), pady=(0, 4))
        self._tt(_dev_btn,
                 "复制本机设备码 + 绑定串，发给发布方开通「设备绑定版」授权。\n"
                 "绑定后该授权码只能在本机使用；换电脑请联系发布方重新绑定。")
        # 绑定串（v2.0.19）：8 位十六进制，用于"4 项取 2"的宽容比对
        try:
            from . import telemetry as _tel2
            _tok = _tel2.bind_token()
        except Exception:
            _tok = ""
        if _tok:
            tk.Label(pad, text=f"（绑定串 {_tok}）", fg="#9ca3af",
                     font=("Consolas", 9)).grid(
                row=2, column=3, sticky="w", padx=(6, 0), pady=(0, 4))
        else:
            ttk.Label(pad, text="（购买设备绑定版时需发给发布方；普通版可忽略）",
                      foreground="#9ca3af").grid(row=2, column=3, sticky="w",
                                                  padx=(6, 0), pady=(0, 4))

        # ---- 当前授权码 ----
        ttk.Label(pad, text="当前授权码：").grid(row=3, column=0, sticky="e",
                                                 pady=(0, 8))
        code_shown = (info["code"] if info else
                      (getattr(self.cfg, "license_code", "") or "（未输入）"))
        code_var = tk.StringVar(value=code_shown)
        code_entry = ttk.Entry(pad, textvariable=code_var, width=44,
                               state="readonly")
        code_entry.grid(row=3, column=1, columnspan=3, sticky="we", pady=(0, 8))

        ttk.Separator(pad, orient="horizontal").grid(
            row=4, column=0, columnspan=4, sticky="we", pady=(2, 10))

        # ---- 更换授权码区 ----
        ttk.Label(pad, text="更换授权码：",
                  font=("Microsoft YaHei UI", 9, "bold")).grid(
            row=5, column=0, columnspan=4, sticky="w", pady=(0, 4))
        ttk.Label(pad, text="把发布方发来的授权码粘到下面，点「激活」立即生效"
                           "（无需重启）：",
                  foreground="#6b7280", wraplength=380, justify="left").grid(
            row=5, column=0, columnspan=4, sticky="w", pady=(0, 4))

        new_var = tk.StringVar()
        new_entry = ttk.Entry(pad, textvariable=new_var, width=44)
        new_entry.grid(row=6, column=0, columnspan=4, sticky="we", pady=(0, 4))

        hint_var = tk.StringVar(value="")
        hint_lbl = ttk.Label(pad, textvariable=hint_var, foreground="#b91c1c",
                             wraplength=380, justify="left")
        hint_lbl.grid(row=7, column=0, columnspan=4, sticky="w", pady=(0, 8))

        def do_activate():
            code = (new_var.get() or "").strip()
            if not code:
                hint_var.set("请先输入授权码。")
                return
            info2 = lic.verify_code(code)
            if not info2:
                hint_var.set("❌ 授权码无效：请核对是否完整复制"
                             "（含 SPHDL- 前缀，注意区分 0/O、1/I）。")
                return
            old_label = config.label_of(self.store.max_records)
            self._activate_license(code)          # 立即生效（无需重启）
            new_label = config.label_of(self.store.max_records)
            hint_var.set("")
            code_var.set(info2["code"])
            messagebox.showinfo(
                "授权成功",
                f"授权码已生效（无需重启）。\n\n"
                f"档位：{old_label}  →  {new_label}\n"
                f"有效期：{'永久' if not info2['days'] else str(info2['days']) + ' 天（自首次激活）'}\n\n"
                f"当前列表上限 {self.store.max_records} 条，"
                + ("已恢复加载新捕获的视频。"
                   if not self.store.is_full() else "列表仍处于满档状态。"),
                parent=win)
            win.destroy()

        def do_paste():
            """从剪贴板取码填入输入框（客户从微信复制后一键贴上）。"""
            try:
                txt = self.root.clipboard_get()
            except Exception:
                txt = ""
            txt = (txt or "").strip()
            if not txt:
                hint_var.set("剪贴板为空，请先复制授权码（选中后 Ctrl+C）。")
                return
            new_var.set(txt)
            hint_var.set("")

        def do_clear():
            if not messagebox.askyesno(
                    "清除授权",
                    f"确定清除当前授权码吗？\n\n"
                    f"清除后档位回落到默认档（{config.label_of(self.cfg.default_max_records)}），"
                    "超出部分记录会被裁掉（磁盘文件保留）。\n"
                    "如需恢复请重新输入授权码。",
                    parent=win):
                return
            if self._clear_license():
                code_var.set("（未输入）")
                new_var.set("")
                hint_var.set("")
                messagebox.showinfo("已清除授权",
                                    f"档位已回落到 {config.label_of(self.store.max_records)}。",
                                    parent=win)

        btns = ttk.Frame(pad)
        btns.grid(row=9, column=0, columnspan=4, sticky="we", pady=(2, 0))
        ttk.Button(btns, text="激活", width=10,
                   command=do_activate).pack(side="left")
        ttk.Button(btns, text="📋 粘贴并激活", width=14,
                   command=lambda: (do_paste(), do_activate())[-1]).pack(
            side="left", padx=6)
        ttk.Button(btns, text="🗑 清除授权", width=12,
                   command=do_clear).pack(side="left", padx=6)
        ttk.Button(btns, text="关闭", width=8,
                   command=win.destroy).pack(side="right")

        new_entry.focus_set()
        win.bind("<Return>", lambda e: do_activate())
        win.update_idletasks()
        # 居中到主窗
        try:
            x = self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_width()) // 2
            y = self.root.winfo_rooty() + 80
            win.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

    def _activate_license(self, code: str) -> bool:
        """校验并激活授权码（供界面按钮与测试调用）。成功返回 True。

        换码立即生效（调 _apply_tier 刷新内存档位与界面），**无需重启**。

        ★ v2.0.19：按**产品线**分别保存 —— 激活视频号码不会顶掉公众号码，
          反之亦然（2026-10-17 客户实测 bug：激活视频号码后公众号授权消失）。
        """
        info = lic.verify_code(code)
        if not info:
            self.push_log("error", "❌ 授权码校验失败（格式/签名不符），授权不变")
            return False

        prod = int(info.get("product") or lic.PRODUCT_VIDEO)
        is_mp = (prod == lic.PRODUCT_MP_ARTICLE)

        # ★ v2.0.19 设备绑定校验：绑定码必须在本机激活
        #   传分量列表 → 支持"4 项取 2"，抗硬件变动（换硬盘/重装系统仍可用）
        #   取不到本机设备信息时不阻止（宁可放过，不可误伤正版客户）。
        try:
            from . import telemetry as _tel
            _comps = _tel.device_components()
            _dev = _tel.device_display()
        except Exception:
            _comps, _dev = [], ""
        _ok, _why = lic.check_device(info, _dev, components=_comps)
        if not _ok:
            self.push_log("error", "❌ 该授权码绑定的不是本机：" + _why.replace("\n", " "))
            try:
                messagebox.showerror(
                    "授权码与本机不匹配",
                    _why + f"\n\n本机设备码：{_dev or '（读取失败）'}\n\n"
                    "如需在本机使用，请把上面的设备码发给发布方重新绑定。")
            except Exception:
                pass
            return False

        # ★ v2.0.19 台数上限校验：限台数码必须联网确认还有余量
        #   （"最多 N 台"无法纯离线实现 —— 本机不知道别的机器用过没有。
        #    服务器不可达时放行，不因网络问题拦正版客户。）
        if info.get("device_limited") and info.get("max_devices"):
            self.push_log(
                "info", f"🔍 校验设备数（此码允许 {info['max_devices']} 台）…")
            try:
                _allowed, _lmsg, _linfo = _tel.check_device_limit(
                    info["code"], info["max_devices"],
                    version=__import__("sphdl").__version__)
            except Exception as _e:                        # noqa: BLE001
                _allowed, _lmsg, _linfo = True, "", {}
                self.push_log("warn", f"⚠ 设备数校验异常（已放行）：{_e}")
            if not _allowed:
                self.push_log("error", "❌ " + _lmsg.replace("\n", " "))
                try:
                    messagebox.showerror("设备数已达上限", _lmsg)
                except Exception:
                    pass
                return False
            _used = (_linfo or {}).get("devices")
            _rem = (_linfo or {}).get("remaining")
            if _used is not None:
                self.push_log(
                    "info", f"✅ 设备数校验通过：已用 {_used}/"
                            f"{info['max_devices']} 台"
                            + (f"，还可激活 {_rem} 台" if _rem is not None else ""))
            else:
                self.push_log("warn", "⚠ 无法连接授权服务器，本次跳过台数校验"
                                      "（离线可用，联网后会自动记录）")

        # 该产品线是否首次激活（决定要不要记 activated_at 起点）
        first_activation = not self.cfg.code_of(prod) \
            or not self.cfg.activated_at_of(prod)
        self.cfg.set_code(prod, info["code"])
        if first_activation:
            self.cfg.set_activated_at(prod, time.time())
        # 兼容旧字段：记住"最近一次激活的码"
        self.cfg.license_code = info["code"]
        self.cfg.save()

        # 日志：按产品线分别说清楚，避免客户以为自己买了视频号却没生效
        name = ("公众号视频版" if is_mp
                else config.label_of(info["tier"]))
        other = lic.PRODUCT_VIDEO if is_mp else lic.PRODUCT_MP_ARTICLE
        other_txt = ""
        if self.cfg.code_of(other):
            other_name = ("公众号视频版" if other == lic.PRODUCT_MP_ARTICLE
                          else "视频号下载")
            other_txt = f"（另一产品「{other_name}」的授权保持不变）"
        self.push_log("ok",
                      f"🔑 授权成功：{name}，"
                      + ("永久有效" if not info["days"]
                         else f"{info['days']} 天（自首次激活）")
                      + other_txt)

        # 档位统一由 _resolve_tier() 决定（内部已处理公众号产品）：
        #   · 视频号码 → 用码里的档位
        #   · 公众号码 → 回落到默认档，**绝不设成 0**
        #     （公众号 tier=0，误用会把上限设成 0 —— 客户实测过的 bug）
        self._apply_tier(self._resolve_tier(),
                         reason=("公众号产品（不影响视频号档位）" if is_mp
                                 else "授权码解锁"))

        # 换码后刷新公众号按钮（可能新解锁/失去该功能）+ 顶部防盗横幅
        try:
            self._refresh_mp_button()
            self._refresh_watermark()
        except Exception:
            pass
        # 激活上报（防盗版追溯）。总开关关闭时为空操作。
        try:
            from . import telemetry as _tel
            _tel.report_activation(
                info["code"],
                version=__import__("sphdl").__version__,
                max_devices=info.get("max_devices"))
        except Exception:
            pass
        return True

    # ---------------- 跨线程安全工具 ----------------
    def push_event(self, item):
        self.ui_q.put(item)

    def push_log(self, level, msg):
        self.log_q.put((level, msg))
        logger.log_file(level, msg)  # 同步落盘，进程消失也能查到全过程

    def _on_log(self, level, msg):
        self.log_q.put((level, msg))

    def _on_capture(self, vd, source):
        """（解析工作线程调用）投递捕获事件。

        返回 False = 列表已满 → 本次捕获被拒收（不再加载后续视频）。
        addon 收到 False 会把该视频从「已见」集合里撤回，这样用户调高
        档位后重新拉取还能再次捕获到它（不会被永久判重挡掉）。

        ★ v2.2.1：可选把原始 object_desc 落盘（诊断开关）。
          用途：确认视频号订阅流里是否带「位置序号 / 发布时间」字段，
                为实现「下载顺序 = 视频号顺序」提供依据。
          开关：在程序目录建一个空文件 `开启原始数据记录.txt` 即生效，
                抓几条后看 `diag_object_desc/*.json`，发回样本即可。
        """
        try:
            if self._diag_raw_enabled():
                self._dump_raw_desc(vd)
        except Exception:
            pass

        if self._capture_full():
            self._rejected_count += 1
            if not self._full_event_pending:
                # 满档提示/自动停批量都要动 tkinter → 只能在主线程做，
                # 这里只投一个轻量事件（每次满档只投一次，防刷屏）
                self._full_event_pending = True
                self.push_event(("full",))
            return False
        self._pending_slots += 1       # 占位：防并发投递穿透上限
        self.push_event(("capture", vd, source))
        return True

    def _capture_full(self) -> bool:
        """是否已满（把「已投递未处理」的捕获一起算上，避免并发超额）。

        v2.0.17 定稿：无「不限量」档，max_records 恒为正数。
        """
        n = self.store.max_records
        if n <= 0:
            return False
        return len(self.store.order) + self._pending_slots >= n

    def _is_known_video(self, vd) -> bool:
        """跨会话去重判断（供 addon 在自动下载提交前调用，v2.0.5）：
        同一视频（video_id=内容哈希）或同一 URL 已在列表 → True。"""
        if vd.video_id and vd.video_id in self._vid_index:
            return True
        return vd.url in self._url_to_rid

    def _on_pipeline_event(self, event, vd, *args):
        self.push_event((event, vd, *args))

    # ---------------- 窗口与控件 ----------------
    def _build_root(self):
        self.root = tk.Tk()
        _ed_sfx = ""
        if self.cfg.locked_max_records is not None:
            _ed_sfx = "·" + config.label_of(self.cfg.locked_max_records)
        self.root.title(f"微信视频号下载器  sphdl v{__version__}{_ed_sfx}")
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

    def _tt(self, widget, text):
        """给控件挂悬停提示：鼠标停留 2 秒显示用法与注意点（v2.0.11）。"""
        ToolTip(widget, text, delay_ms=2000)

    def _open_guide(self):
        """打开内置「使用说明」窗口（首次启动自动弹出，也可点按钮查看）。"""
        if getattr(self, "_guide_win", None) is not None:
            try:
                if self._guide_win.winfo_exists():
                    self._guide_win.lift()
                    return
            except Exception:
                pass
        win = tk.Toplevel(self.root)
        win.title("使用说明")
        win.geometry("620x540")
        win.minsize(540, 440)
        win.transient(self.root)
        self._guide_win = win

        frm = ttk.Frame(win, padding=10)
        frm.pack(fill="both", expand=True)
        txt = tk.Text(frm, wrap="word", font=("Microsoft YaHei UI", 10),
                      bg="#f8fafc", fg="#111827", relief="flat",
                      padx=12, pady=10, state="normal")
        txt.insert("1.0", GUIDE_TEXT)
        txt.config(state="disabled")
        sb = ttk.Scrollbar(frm, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.guide_dont_var = tk.BooleanVar(value=True)

        def _on_close():
            self.cfg.guide_auto_shown = bool(self.guide_dont_var.get())
            self.cfg.save()
            win.destroy()

        bar = ttk.Frame(win, padding=(10, 4, 10, 8))
        bar.pack(fill="x")
        ttk.Checkbutton(bar, text="下次启动不再自动显示",
                        variable=self.guide_dont_var).pack(side="left")
        ttk.Button(bar, text="知道了", width=10,
                   command=_on_close).pack(side="right")
        win.protocol("WM_DELETE_WINDOW", _on_close)

    def _build_watermark_bar(self, parent):
        """防盗声明横幅（v2.0.19）。

        设计要点：
          · 放在最顶部、整行铺满，视觉上第一眼就能看到
          · 深蓝底 + 浅色字：与界面其它灰色区域区分，像"正式声明"而非普通标签
          · 右侧显示**本机设备码**：截图流传出去时，凭设备码能查到是谁
          · 授权状态不同 → 文案不同（已授权 / 未授权）

        ⚠️ 诚实说明它的作用边界：
          界面文字**拦不住**有技术的人（改代码/截图裁掉即可）。它的价值是
          ① 让普通买家知道"这软件有登记设备" → 降低购买二手的意愿
          ② 让转卖者截图时带上设备码 → 你能反查来源
          真正的溯源靠激活上报 + 台账（ledger track）。
        """
        bar = tk.Frame(parent, bg="#1e3a5f", height=30)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        # 左侧：防盗声明（常驻）
        self.wm_left = tk.Label(
            bar,
            text="🔒  正版授权软件 · 每份授权绑定设备并登记备案 · 严禁转售/破解",
            bg="#1e3a5f", fg="#e8f0f8",
            font=("Microsoft YaHei UI", 9, "bold"),
            anchor="w")
        self.wm_left.pack(side="left", padx=(10, 0))

        # 右侧：授权归属 + 设备码（随授权状态刷新）
        self.wm_right = tk.Label(
            bar, text="未授权 · 请点「🔑 授权管理」激活",
            bg="#1e3a5f", fg="#ffd54f",
            font=("Microsoft YaHei UI", 9), anchor="e")
        self.wm_right.pack(side="right", padx=(0, 10))

        self._refresh_watermark()

        # 悬停说明：讲清"为什么显示设备码"，避免客户以为是监控
        self._tt(bar, "【关于这行声明】\n"
                      "本软件为正版授权软件，每次激活会登记授权码与本机设备码，\n"
                      "用于授权管理与售后支持（如换机、重装）。\n\n"
                      "· 不会上传您的视频、文件、浏览记录或微信信息\n"
                      "· 不联网校验、不限制使用，软件可离线工作\n"
                      "· 详细的收集范围见「使用说明 → 隐私说明」")

    def _refresh_watermark(self):
        """刷新防盗声明右侧的授权状态文案（v2.0.19）。"""
        lbl = getattr(self, "wm_right", None)
        if lbl is None:
            return
        try:
            vc = self._license_info(lic.PRODUCT_VIDEO)
            mp = self._license_info(lic.PRODUCT_MP_ARTICLE)
            parts = []
            if vc and not self._license_expired():
                if vc.get("days"):
                    left = lic.days_left(
                        vc["days"], self.cfg.activated_at_of(lic.PRODUCT_VIDEO))
                    parts.append(f"视频号 {left}天")
                else:
                    parts.append("视频号 永久")
            if mp:
                parts.append("公众号 永久" if not mp.get("days")
                             else f"公众号 {mp['days']}天")
            if parts:
                txt = "✅ 已授权：" + " ｜ ".join(parts)
                fg = "#a5f3a0"
            else:
                txt = "未授权 · 请点「🔑 授权管理」激活"
                fg = "#ffd54f"
            # 附本机设备码（截图流传时可反查来源）
            # ★ v2.0.19 性能：只读缓存，不触发采集（否则启动卡 1~2 秒）
            try:
                from . import telemetry as _t
                comps = _t.cached_components()
                dev = ""
                if comps:
                    import hashlib as _h
                    dev = _t.device_display(
                        _h.sha256("|".join(comps).encode()).hexdigest())
                if dev:
                    txt += f"   ｜  设备 {dev}"
            except Exception:
                pass
            lbl.config(text=txt + "  ", fg=fg)
        except Exception:
            pass

    def _init_overlay_watermark(self, anchor):
        """创建浮层水印窗口（v2.0.19）。

        anchor: 要覆盖的容器（这里是视频列表 LabelFrame）。

        技术要点：
          · overrideredirect(True) → 无标题栏、无边框
          · -alpha 0.18 → 整体半透明（够淡，能读列表内容）
          · -transparentcolor 白色 → 除文字外全部透传鼠标
            这样用户点列表、拖滚动条都不受水印影响。
        """
        import random
        self._wm_rng = random.Random()

        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        # ★ v2.0.19 关键修复：必须保持 topmost，否则主窗口一激活就把水印压在
        #   下面（用户"看不到水印"的直接原因）。overrideredirect 的 Toplevel
        #   虽然无边框，但仍参与 Z 序竞争。
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        # ★ 透明度策略（v2.0.19 三次调整，关键）：
        #   之前用窗口 -alpha 做半透明 → 整个窗口被整体淡化，
        #   文字边缘一起发虚，中文小字糊成一团（实测"看不清、变形严重"）。
        #   改为 **-alpha 1.0（不透明）+ 靠文字颜色本身取浅色**：
        #   Canvas 的抗锯齿只作用于字形，笔画边缘锐利清晰，
        #   视觉上同样是"半透明水印"，但可读性完全不同。
        try:
            win.attributes("-alpha", 1.0)
        except Exception:
            pass
        # 让画布背景色成为"透明色"→ 只有文字可见、可点击穿透
        KEY = "#ff00ff"          # 洋红作键控色（很少与水印文字撞）
        try:
            win.configure(bg=KEY)
            win.attributes("-transparentcolor", KEY)
        except Exception:
            KEY = "white"
            try:
                win.configure(bg=KEY)
                win.attributes("-transparentcolor", KEY)
            except Exception:
                pass

        cv = tk.Canvas(win, bg=KEY, highlightthickness=0, bd=0)
        cv.pack(fill="both", expand=True)
        win.withdraw()
        self._wm_win = win
        self._wm_cv = cv
        self._wm_key = KEY
        # 启动水印主循环（唯一 tick，60ms 一次，平滑飘动）
        self._wm_state = None
        self._wm_visible = False      # 当前是否处于显示状态（topmost 需要显式管理）
        self._wm_behind = False       # 主窗口是否在后台（缓存，每 400ms 刷新）
        self._wm_fg_ts = 0            # 上次前台检查的时间戳（ms）
        self._wm_after_id = self.root.after(800, self._wm_tick)

    def _device_text_for_watermark(self) -> str:
        """水印文字：优先"设备码"，取不到则用版本号。

        ★ v2.0.19 性能：设备码由后台线程预热（telemetry.warm_up_async），
        这里只读缓存 —— 如果后台还没采完，先用版本号占位，
        由 _wm_tick 检测到就绪后自动补上（不阻塞、不卡启动）。
        """
        try:
            from . import telemetry as _t
            comps = _t.cached_components()
            if comps:
                fp = __import__("hashlib").sha256(
                    "|".join(comps).encode()).hexdigest()
                d = _t.device_display(fp)
                if d:
                    return d
        except Exception:
            pass
        try:
            from sphdl import __version__ as v
            return f"v{v}"
        except Exception:
            return "sphdl"

    def _main_window_obscured(self) -> bool:
        """主窗口是否被**别的程序**挡住（v2.1.3）。

        判断方法：Win32 WindowFromPoint 命中测试。
          取主窗口中心点 → 问系统"这个点上最顶层是谁"
            · 返回的窗口属于本进程（主窗口或其子控件）→ 没被挡
            · 返回的是别的程序的窗口 → 被挡住

        ★ 为什么用这个而不是"前台窗口"：
          前台窗口判定会把"窗口打开了但没点它"也算成后台（实测水印一直
          不显示）。而命中测试问的是"用户眼睛能不能看到这个窗口"，
          和真实观感一致。

        返回 True = 被遮挡 / 最小化 / 不可见 → 水印应隐藏。
        """
        try:
            import ctypes
            import ctypes.wintypes as wt
            u = ctypes.windll.user32
            # ① 最小化 / 不可见
            if self.root.state() == "iconic":
                return True
            if not self.root.winfo_viewable():
                return True
            top = self.root.winfo_id()
            for _ in range(8):
                p = _win32_parent(top)
                if not p:
                    break
                top = p
            if u.IsIconic(int(top)):
                return True
            # ② 命中测试：窗口中心点上最顶层的是谁
            rect = wt.RECT()
            u.GetWindowRect(int(top), ctypes.byref(rect))
            cx = (rect.left + rect.right) // 2
            cy = (rect.top + rect.bottom) // 2
            hit = int(u.WindowFromPoint(wt.POINT(cx, cy)))
            if not hit:
                return False          # 取不到 → 当作没被挡（宁可显示）
            # ③ hit 是否属于本进程（含子控件）
            pid = ctypes.c_ulong(0)
            u.GetWindowThreadProcessId(hit, ctypes.byref(pid))
            if int(pid.value) == os.getpid():
                return False
            return True               # 别的程序的窗口 → 被挡住
        except Exception:
            return False              # 任何异常都不隐藏（宁可显示）

    def _wm_tick(self):
        """水印主循环（v2.0.19 重写：平滑飘动 + 永不消失 + 不闪烁）。

        ★ 为什么重写（原实现两个致命问题）：
          ① **界面卡死**：_draw_watermark 里有 6 处提前 return，每处都调用
             _schedule_watermark 重新排任务。而 after 任务里再排任务时，
             旧的 after_cancel 未必生效 → 定时器指数增殖，最终界面无响应。
             现在改为**唯一 tick 循环**：只在函数末尾排下一次，其他分支直接
             return（由末尾统一续命）。
          ② **闪烁 + 可被抓拍**：原实现每 2.5~5.5 秒"消失→在新位置出现"，
             中间有 withdraw/deiconify 空档，用户截图正好能抓到无水印的瞬间。
             现在改为**每 60ms 平移 1~2 像素**，窗口从不隐藏、文字从不重绘，
             只有位置在动 → 视觉上是缓慢飘动，任何时刻截图都带水印。
        """
        win = getattr(self, "_wm_win", None)
        if win is None:
            return                       # 没有水印窗（初始化失败）→ 不续命
        try:
            if not self.root.winfo_exists() or not win.winfo_exists():
                return                   # 窗口已销毁 → 停止循环
        except Exception:
            return

        # ★ v2.1.3：水印只在**主窗口真正可见（没被别的程序盖住）**时显示。
        #
        #   历史教训（三次反复，写清楚免得再改错）：
        #   ① topmost=False → 被主窗口盖住，用户看不到水印。
        #   ② topmost=True  → 切到后台后水印浮在桌面最上层，挡住别的软件。
        #   ③ 用"前台窗口"判定 → 太严：窗口没被点击激活时也判为后台，
        #      水印一直不显示。
        #
        #   最终方案：用 Win32 **WindowFromPoint 命中测试**。
        #     取主窗口中心点，问系统"这个点上最顶层的是哪个窗口"：
        #       · 是本程序（或其子窗口）→ 可见，显示水印
        #       · 是别的程序 → 被遮挡，隐藏水印
        #     这个判断和"用户眼睛看到的"一致：别的程序挡在前面就隐藏，
        #     主窗口露出来就显示。不依赖焦点，也不会误判。
        #   另外仍然处理最小化（iconic / IsIconic）。
        # ★ v2.1.3 关键修复：先完成初始化（画文字），再判断要不要显示。
        #
        #   原逻辑：被遮挡时直接 return，导致 _wm_state 一直是 None
        #   → 水印文字从未被绘制 → 用户解除遮挡后也永远看不到水印
        #   （实测：item 数 0，块尺寸乱）。
        #   现在：首次 tick 一定把文字画好（窗口先 withdraw），
        #   之后只管理"显示/隐藏"，恢复可见时立刻就有文字。
        if getattr(self, "_wm_state", None) is None:
            try:
                was_visible = self._wm_visible
                self._wm_visible = False
                self._wm_step()          # 画文字 + 设定初始位置
                if not was_visible and win.winfo_exists():
                    win.withdraw()       # 画好先藏起来，等下面判断再决定显示
                self._wm_visible = False
            except Exception:
                pass

        try:
            now_ms = int(time.time() * 1000)
            if now_ms - getattr(self, "_wm_fg_ts", 0) >= 300:
                self._wm_fg_ts = now_ms
                self._wm_behind = self._main_window_obscured()
            behind = getattr(self, "_wm_behind", False)
            if behind:
                if self._wm_visible:
                    win.withdraw()
                    self._wm_visible = False
                self._wm_after_id = self.root.after(200, self._wm_tick)
                return
            if not self._wm_visible and self._wm_state is not None:
                win.deiconify()
                win.attributes("-topmost", True)
                self._wm_visible = True
        except Exception:
            pass

        try:
            self._wm_step()
        except Exception:
            pass                      # 任何异常都不让循环断掉

        # ★ 设备码就绪后回填（v2.0.19）：启动时水印先用版本号占位，
        #   后台采集完成后（约 1~2 秒）把文字换成设备码，只换一次。
        try:
            from . import telemetry as _t
            if not getattr(self, "_wm_dev_filled", False) and _t.is_device_ready():
                self._wm_dev_filled = True
                dev = self._device_text_for_watermark()
                cv = getattr(self, "_wm_cv", None)
                if cv is not None:
                    for it in cv.find_all():
                        if cv.type(it) == "text":
                            cv.itemconfigure(it, text=f"正版授权 {dev}")
                # 横幅也刷新一下，补上"｜ 设备 xxxx"
                try:
                    self._refresh_watermark()
                except Exception:
                    pass
        except Exception:
            pass

        # ★ 唯一的续命点：整个循环只有这一处 after
        try:
            self._wm_after_id = self.root.after(60, self._wm_tick)
        except Exception:
            self._wm_after_id = None

    def _wm_step(self):
        """移动一次水印（约 60ms 一次）。首次调用时完成初始化+绘制文字。"""
        win = self._wm_win
        anchor = self._wm_anchor
        if anchor is None:
            return
        ax = anchor.winfo_rootx()
        ay = anchor.winfo_rooty()
        aw = anchor.winfo_width()
        ah = anchor.winfo_height()
        if aw < 200 or ah < 120:
            return                      # 列表区还没布局好

        rng = self._wm_rng
        st = getattr(self, "_wm_state", None)
        # ★ v2.1.3：一行水印 → 块做成长条（320×36），不再占半个列表区
        bw, bh = min(340, aw - 16), 36
        if bw < 120 or ah < 80:
            return

        if st is None:
            # ---------- 首次：画文字 + 放在中央偏上，并给一个随机方向 ----------
            self._wm_cv.delete("all")
            self._wm_cv.configure(width=bw, height=bh)
            dev = self._device_text_for_watermark()
            # ★ v2.1.3：水印改为**一行**（按用户要求）
            #   原来 3×2 六行铺得满屏都是，太抢眼；一行足够"截图必带"，
            #   又不打扰看列表。文字仍然只画这一次，之后只移动窗口。
            line = f"正版授权 {dev}"
            self._wm_cv.create_text(
                16, bh // 2, text=line, anchor="w",
                fill="#8fa0b0",
                font=("Microsoft YaHei UI", 11))
            # 初始位置：居中
            x = ax + max(0, (aw - bw) // 2)
            y = ay + max(0, (ah - bh) // 2)
            # 速度：每 tick 移动 1~2 px，60ms 一次 → 约 25~35 px/秒，很慢
            vx = rng.choice([-2, -1, 1, 2])
            vy = rng.choice([-1, 0, 1])
            if vy == 0:
                vy = rng.choice([-1, 1])
            self._wm_state = {"x": x, "y": y, "vx": vx, "vy": vy,
                              "bw": bw, "bh": bh}
            try:
                win.geometry(f"{bw}x{bh}+{x}+{y}")
                win.deiconify()          # 只在首次显示一次
                win.attributes("-topmost", True)   # 确保浮在最上层
                win.lift()
                self._wm_visible = True
            except Exception:
                pass
            return

        # ---------- 后续：只平移，不重绘文字 ----------
        if st["bw"] != bw or st["bh"] != bh:
            st["bw"], st["bh"] = bw, bh
            try:
                self._wm_cv.configure(width=bw, height=bh)
                win.geometry(f"{bw}x{bh}+{st['x']}+{st['y']}")
            except Exception:
                pass

        minx, maxx = ax, max(ax, ax + aw - bw)
        miny, maxy = ay, max(ay, ay + ah - bh)
        x = st["x"] + st["vx"]
        y = st["y"] + st["vy"]
        # 撞到边界 → 随机换一个方向（仍然平滑，不瞬移）
        if x < minx or x > maxx:
            st["vx"] = -st["vx"]
            x = min(max(x, minx), maxx)
        if y < miny or y > maxy:
            st["vy"] = -st["vy"]
            y = min(max(y, miny), maxy)
        # 极小概率微调速度方向，让轨迹不呆板（但不瞬移）
        if rng.random() < 0.01:
            st["vx"] = rng.choice([-2, -1, 1, 2])
            st["vy"] = rng.choice([-2, -1, 1, 2])

        st["x"], st["y"] = x, y
        try:
            win.geometry(f"{bw}x{bh}+{x}+{y}")
        except Exception:
            pass
    def _build_ui(self):
        root = self.root
        # ★ v2.2.3：main 必须 side="top"。
        #
        #   踩过的坑：原来只写 pack(fill="both", expand=True)（默认 side="top"），
        #   但它先于底部元素 pack，expand 会把整个 root 吃掉，
        #   之后创建的 filter_bar / status_bar 即使 side="bottom" 也只剩 1px 高
        #   （实测 winfo_height() == 1，屏幕上完全看不见）。
        #
        #   显式 side="top" 让 pack 管理器先给底部元素留位，main 只占剩余空间。
        # ★★★ v2.2.3 关键顺序：底部元素必须**先 pack**，main 后 pack。
        #
        #   Tk 的 pack 是"按调用顺序分配空间"：
        #     main 先 pack(expand=True) → 吃掉整个 root
        #     → 之后 side="bottom" 的控件只剩 1px（实测 h=1，完全看不见）
        #   把底部先 pack，它们先占住自己的高度，main 再拿剩下的。
        #   （踩了两次：第一次只改 side="top" 不够，顺序才是根因）
        # ===== ⑥ 来源筛选横幅（v2.2.3）=====
        #   ★ 客户报障（2026-10-23）：
        #     「点击『在主清单中查看』后，找不到回到之前列的视频号视频清单了」
        #
        #   根因：_goto_main("公众号") 会把清单筛成「只显示公众号」，
        #   而旧版取消筛选的入口只有「双击来源列」和「状态栏文字提示」，
        #   客户根本发现不了 → 以为视频号记录丢了（其实一条没少）。
        #   更糟的是：旧提示写着「点状态栏『显示全部』」，但那个按钮从来不存在。
        #
        #   修法：有筛选时在状态栏上方显示一条**醒目的可点击横幅**，
        #   上面直接放「显示全部」按钮。
        self.filter_bar = tk.Frame(root, bg="#fff7ed")
        self.lbl_filter = tk.Label(
            self.filter_bar, text="", bg="#fff7ed", fg="#92400e",
            font=("Microsoft YaHei UI", 10, "bold"), anchor="w")
        self.lbl_filter.pack(side="left", padx=(10, 6), pady=6)
        self.btn_filter_all = ttk.Button(
            self.filter_bar, text="显示全部", width=10,
            command=lambda: self._filter_by_platform(""))
        self.btn_filter_all.pack(side="left", padx=(0, 6), pady=6)
        self.btn_filter_video = ttk.Button(
            self.filter_bar, text="只看视频号", width=12,
            command=lambda: self._filter_by_platform("视频号"))
        self.btn_filter_video.pack(side="left", padx=(0, 10), pady=6)
        self._tt(self.btn_filter_all,
                 "取消来源筛选，显示全部记录（视频号 + 公众号）")
        self._tt(self.btn_filter_video,
                 "只显示「视频号」来源的记录（公众号记录只是被隐藏，不删除）")

        # ===== ⑦ 状态栏 =====
        self.status_bar = ttk.Label(root, text="", relief="sunken", anchor="w",
                                    padding=(8, 2))
        self.status_bar.pack(fill="x", side="bottom")

        main = ttk.Frame(root, padding=8)
        main.pack(side="top", fill="both", expand=True)

        # ===== ⓿ 防盗声明横幅（v2.0.19）=====
        # 目的：让客户一眼看到这是"有授权追溯的正版"，同时让转卖者有所顾忌。
        # 内容随授权状态变化：已授权显示授权归属，未授权提示需授权。
        # ⚠ 注意：这只是界面层提示，能被截图/改代码规避；真正的溯源靠
        #   「激活上报 + 台账」（见 docs/发码台账使用说明.md 的 track 命令）。
        self._build_watermark_bar(main)

        # ===== ① 引擎控制区 =====
        eng = ttk.LabelFrame(main, text=" 嗅探引擎 ", padding=8)
        eng.pack(fill="x")

        self.btn_engine = ttk.Button(eng, text="▶  启动嗅探", width=14,
                                     command=self._toggle_engine)
        self.btn_engine.pack(side="left")
        self._tt(self.btn_engine,
                 "启动/停止嗅探引擎（本地代理）。\n"
                 "启动后打开微信视频号播放视频即可捕获。\n"
                 "注意：首次使用请先装好 CA 证书；停止时自动恢复系统代理。")

        ttk.Label(eng, text="代理端口").pack(side="left", padx=(16, 2))
        self.port_var = tk.StringVar(value=str(self.cfg.port))
        self.ent_port = ttk.Entry(eng, textvariable=self.port_var, width=7)
        self.ent_port.pack(side="left")
        self._tt(self.ent_port,
                 "本地代理端口，默认 8899。\n"
                 "一般无需修改；若被占用，改端口后重启嗅探。")

        self.auto_var = tk.BooleanVar(value=self.cfg.auto_proxy)
        self.auto_proxy_chk = ttk.Checkbutton(eng, text="自动设置系统代理",
                                              variable=self.auto_var)
        self.auto_proxy_chk.pack(side="left", padx=14)
        self._tt(self.auto_proxy_chk,
                 "勾选：启动嗅探时自动接管系统代理，停止时自动还原。\n"
                 "不勾选：需手动把系统代理设为 127.0.0.1:端口。")

        # 应急按钮：把系统代理恢复到本工具接管前的状态（快照在 config 目录的
        # proxy_backup.json；无快照时兜底关闭代理）。引擎运行中点它会先确认。
        self.btn_restore_proxy = ttk.Button(eng, text="↩  恢复网络代理", width=13,
                                            command=self._on_restore_proxy)
        self.btn_restore_proxy.pack(side="left", padx=14)
        self._tt(self.btn_restore_proxy,
                 "应急：把系统代理恢复到本工具接管前的状态（会同时停止嗅探）。\n"
                 "网络突然打不开时优先点它。")

        ttk.Label(eng, text="CA证书:").pack(side="left", padx=(18, 2))
        self.ca_label = ttk.Label(eng, text="检测中…", foreground="#d97706")
        self.ca_label.pack(side="left")
        self.btn_cert = ttk.Button(eng, text="安装/检测", width=10,
                                   command=self._on_cert_btn)
        self.btn_cert.pack(side="left", padx=8)
        self._tt(self.btn_cert,
                 "检测并安装 mitmproxy CA 证书。\n"
                 "首次使用必须安装，否则 HTTPS 解密失败、无法捕获。")

        self.eng_label = ttk.Label(eng, text="状态: 已停止", foreground="#6b7280")
        self.eng_label.pack(side="right")
        self.btn_guide = ttk.Button(eng, text="❓ 使用说明", width=10,
                                    command=self._open_guide)
        self.btn_guide.pack(side="right", padx=(0, 10))
        self._tt(self.btn_guide,
                 "打开内置「使用说明」：快速上手 + 小技巧。\n"
                 "首次启动会自动弹出一次；鼠标悬停任意按钮 2 秒"
                 "也有单按钮说明。")

        # ===== ② 捕获设置（捕获后的行为） =====
        capf = ttk.LabelFrame(main, text=" 捕获设置 ", padding=6)
        capf.pack(fill="x", pady=(8, 0))

        # 自动下载开关（v1.2.5）：开 = 新捕获立即自动入队下载；
        # 关（默认）= 捕获只进列表，用户选中后手动下载（原行为）
        self.auto_dl_var = tk.BooleanVar(value=self.cfg.auto_download)
        self.auto_dl_chk = ttk.Checkbutton(capf, text="⚙ 自动下载（捕获即入队）",
                                           variable=self.auto_dl_var,
                                           command=self._on_auto_dl_toggle)
        self.auto_dl_chk.pack(side="left", padx=(2, 16))
        self._tt(self.auto_dl_chk,
                 "勾选：新捕获的视频立即自动加入下载队列。\n"
                 "不勾（默认）：只进列表，选中后手动下载。\n"
                 "注意：只对勾选之后新捕获的视频生效。")

        # 「🖼 下载图片」开关（v2.0.4 引入，v2.0.19 起**默认不勾**）：
        # 勾选 = 图片显示在列表并可下载；
        # 不勾（默认）= 列表不显示图片、新捕获的图片直接跳过、下载操作跳过图片。
        self.images_var = tk.BooleanVar(value=self.cfg.download_images)
        self.images_chk = ttk.Checkbutton(capf, text="🖼 下载图片",
                                          variable=self.images_var,
                                          command=self._on_images_toggle)
        self.images_chk.pack(side="left", padx=16)
        self._tt(self.images_chk,
                 "勾选：图片帖也会进列表并可下载。\n"
                 "不勾（默认）：只处理视频，图片不进列表、不下载。\n"
                 "说明：本工具主打下载视频；图片混进列表会干扰挑选与批量操作，\n"
                 "因此默认关闭。需要时随时勾上（只影响勾选之后新捕获的内容）。")

        # 「🎯 自然捕获」开关（v2.0.7）：开（默认）= 播放到哪条才捕获哪条；
        # 关 = 整页列表捕获（v2.0.6 行为）
        self.natural_var = tk.BooleanVar(value=self.cfg.natural_capture)
        self.natural_chk = ttk.Checkbutton(capf, text="🎯 自然捕获",
                                           variable=self.natural_var,
                                           command=self._on_natural_toggle)
        self.natural_chk.pack(side="left", padx=16)
        self._tt(self.natural_chk,
                 "勾选（默认）：只捕获当前播放的视频 + 随后几条缓存，不整页入库。\n"
                 "不勾：整页列表捕获（v2.0.6 行为）。")

        # 「⚡ 批量拉取」按钮（v2.0.8）：整页入库 + 页面自动滚动拉取整个视频号
        self.btn_batch = ttk.Button(capf, text="⚡ 批量拉取", width=10,
                                    command=self._on_batch_toggle)
        self.btn_batch.pack(side="left", padx=(16, 2))
        self._tt(self.btn_batch,
                 "批量拉取整个视频号：整页入库，并让当前选中的视频号窗口"
                 "自动滚动加载全部视频。\n"
                 "拉到底自动停止；再点一次提前停止。\n"
                 "注意：被其他窗口遮挡时仍持续滚动；切换拉取对象需"
                 "点击目标视频号窗口。")

        # 当前模式提示（随开关实时变化）
        self.mode_label = ttk.Label(capf, foreground="#6b7280")
        self.mode_label.pack(side="right", padx=4)

        # ===== ②-a2 「📄 公众号视频」按钮（v2.0.19 附加模块） =====
        # 默认不可用；仅当授权码含 FLAG_MP_ARTICLE_VIDEO 时才能真正解析下载。
        # 未授权时按钮仍在（客户能看到有这个功能、知道要加钱解锁），
        # 点击后弹出说明并引导去「🔑 授权管理」——比藏起来更利于成交。
        # ★ v2.0.19：公众号入口做成**独立配色**，让用户一眼找到。
        #   ttk.Button 不支持 bg 参数，必须走 Style 定义。
        #   选暖橙底 + 深色字：与整界面的灰/蓝区分开，又不刺眼；
        #   未授权时换成浅灰底（暗示需解锁），但**不禁用**（仍可点，给引导）。
        try:
            st = ttk.Style()
            st.configure("Mp.TButton", background="#ffe0b2",
                         foreground="#8a4b00")
            st.map("Mp.TButton",
                   background=[("active", "#ffcc80"),
                               ("pressed", "#ffb74d")])
            st.configure("MpLocked.TButton", background="#eceff1",
                         foreground="#78909c")
            st.map("MpLocked.TButton",
                   background=[("active", "#e0e4e7")])
        except Exception:
            pass
        self.btn_mp = ttk.Button(capf, text="📄 公众号视频", width=16,
                                 style="MpLocked.TButton",
                                 command=self._on_mp_article)
        self.btn_mp.pack(side="left", padx=(16, 2))
        self._tt(self.btn_mp,
                 "【公众号文章视频】下载公众号文章里的视频（附加功能）。\n"
                 "\n"
                 "用法：粘贴公众号文章链接（形如 https://mp.weixin.qq.com/s/xxx）\n"
                 "→ 自动解析出文章内全部视频 → 选清晰度下载。\n"
                 "\n"
                 "⚠ 与「视频号」是两个不同入口：\n"
                 "  · 视频号：微信→发现→视频号（用嗅探抓）\n"
                 "  · 公众号：文章页里的视频（粘贴链接解析）\n"
                 "\n"
                 "此功能需专用授权码开启；未授权时点它会提示如何解锁。")
        self._refresh_mp_button()

        # ===== ②-b 列表上限档位（v2.0.17：档位由发布方掌控，客户端不可自配） =====
        limf = ttk.Frame(main)
        limf.pack(fill="x", pady=(8, 0))
        ttk.Label(limf, text="列表上限:").pack(side="left")
        self.limit_value_lbl = ttk.Label(
            limf, text="", font=("Microsoft YaHei UI", 9, "bold"),
            foreground="#0f766e")
        self.limit_value_lbl.pack(side="left", padx=(6, 2))
        self.btn_license = ttk.Button(limf, text="🔑 授权管理", width=14,
                                      command=self._on_license_input)
        if self.cfg.locked_max_records is None:
            self.btn_license.pack(side="left", padx=6)
        self._tt(self.btn_license,
                 "查看/更换授权码，解锁对应的列表上限档位：\n"
                 "轻量（500 条）/ 标准（1000 条）/ 旗舰（2000 条）/\n"
                 "至尊（2 万条）。\n"
                 "授权码与档位绑定，输入后立即生效并永久记住（config.json）。\n"
                 "换码随时可换：把新码粘进弹窗点「激活」即可，"
                 "降档会裁掉超出记录（磁盘文件保留）。\n"
                 "本版本档位由发布方预置时，无需也不能自行修改。")
        self.limit_hint = ttk.Label(limf, foreground="#6b7280")
        self.limit_hint.pack(side="left", padx=6)
        # 注：状态栏首次刷新要等 self.addon 建好（_update_status_bar 会读它），
        # 故不在此处调用，见 App.__init__ 末尾的 _refresh_limit_display()

        # ===== ③ 下载目录 =====
        dirf = ttk.Frame(main)
        dirf.pack(fill="x", pady=(8, 0))
        ttk.Label(dirf, text="下载目录:").pack(side="left")
        self.dir_var = tk.StringVar(value=self.cfg.download_dir)
        self.dir_entry = ttk.Entry(dirf, textvariable=self.dir_var, width=52)
        self.dir_entry.pack(side="left", padx=6)
        self._tt(self.dir_entry,
                 "视频保存目录。\n点『更改…』可修改，点『打开文件夹』直达。")
        self.btn_choose_dir = ttk.Button(dirf, text="更改…", width=8,
                                         command=self._choose_dir)
        self.btn_choose_dir.pack(side="left")
        self._tt(self.btn_choose_dir, "选择新的下载目录，修改后立即生效。")
        self.btn_open_dir = ttk.Button(dirf, text="打开文件夹", width=10,
                                       command=self._open_download_dir)
        self.btn_open_dir.pack(side="left", padx=4)
        self._tt(self.btn_open_dir, "打开当前下载目录。")

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
        btns1 = ttk.Frame(listf)   # 下载操作
        btns1.pack(side="bottom", fill="x", pady=(6, 2))

        # ★ v2.1.9：多选操作说明常驻条（客户反馈"不知道怎么多选"）
        #   放在下载按钮行下面（视觉上属于"操作提示"，跟按钮一起读）。
        #
        #   为什么不用 tooltip：客户根本不会去悬停按钮找说明。
        #
        #   视觉迭代（实测截图两轮才达标）：
        #     ① 纯蓝字小字号 → 被按钮群淹没，读不到
        #     ② 加浅底色框 + font=(...,9,"bold") → 底色有了，但**bold 没生效**
        #        （Windows 上 "Microsoft YaHei UI" 没有独立 bold 字重，
        #         Tk 会静默回退常规体 → 看起来还是不粗）
        #     ③ 字号提到 10pt + tkinter.font 显式 weight="bold"（程序化验证生效）
        #     ④ 客户要"明显"，10pt 与按钮 9pt 差得不够 → 提到 11pt + 加大内边距
        import tkinter.font as _tkfont
        _hint_font = _tkfont.Font(family="Microsoft YaHei UI", size=11,
                                  weight="bold")
        self.hint_bar = tk.Frame(listf, bg="#dce9fa", bd=0,
                                 highlightthickness=1,
                                 highlightbackground="#9dbfe8")
        self.hint_bar.pack(side="bottom", fill="x", pady=(5, 5))
        self.lbl_hint = tk.Label(
            self.hint_bar,
            text=("💡 多选技巧　点一行＝选中一个　│　"
                  "按住 Ctrl 再点＝逐个加选　│　"
                  "按住 Shift 再点＝连续选一段　│　"
                  "Ctrl+A＝全选　│　"
                  "「反选」＝选中的变不选、没选的变选中"
                  "\n　　点「序号」表头＝按抓取先后排序（还原你在视频号里刷的顺序）"),
            bg="#dce9fa", fg="#0d2d5c",
            font=_hint_font,
            anchor="w", justify="left", padx=12, pady=8)
        self.lbl_hint.pack(side="left", fill="x", expand=True)
        # 窗口窄时提示文字会截断 → 允许换行显示（宽度绑定父容器）
        self.hint_bar.bind(
            "<Configure>",
            lambda e: self.lbl_hint.configure(wraplength=max(300, e.width - 30)))

        # v2.0.19：新增「来源」列（platform）用于区分 视频号/公众号。
        # 列宽按实际内容调过：状态列要容下「下载中 100%」，文件列要容下
        # 常见中文文件名（原来太窄会被窗口右边缘截断）。
        # ★ v2.2.0：新增「序号」列（客户需求）
        #   用途：有些客户想"只下前 20 个"，有连续序号才好按数选。
        #   ★ 序号是**当前显示顺序**的编号（1,2,3...），
        #     排序/筛选变化后要重新编号 —— 见 _renumber_rows()。
        #     不做固定 id：客户要的是"第几个"，不是"哪一条"。
        cols = ("no", "status", "title", "platform", "size", "source", "time", "file")
        self.tree = ttk.Treeview(listf, columns=cols, show="headings", height=10,
                                 selectmode="extended")
        heads = {"no": ("序号", 52),
                 "status": ("状态", 120), "title": ("标题", 215),
                 "platform": ("来源", 92), "size": ("大小", 82),
                 "source": ("捕获方式", 115), "time": ("时间", 85),
                 "file": ("文件", 215)}
        self._head_text = {c: heads[c][0] for c in cols}
        for c in cols:
            self.tree.heading(c, text=heads[c][0],
                              command=lambda col=c: self._on_tree_sort(col))
            self.tree.column(c, width=heads[c][1],
                             stretch=(c in ("title", "file")))
        vsb = ttk.Scrollbar(listf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # ===== 随机浮层水印（v2.0.19 防盗）=====
        # 用一个**无边框、半透明、鼠标穿透**的 Toplevel 叠在列表区上方，
        # 定时在随机位置绘制斜排水印（含本机设备码）。
        #
        # 为什么用 Toplevel 而不是 Canvas：
        #   Canvas 盖在 Treeview 上会吃掉鼠标事件（没法点列表），
        #   而 Toplevel + -transparentcolor 只让文字可见、其余像素透传，
        #   鼠标点击照常落到下面的列表上。
        #
        # 效果：
        #   · 转卖者截图时水印必被截进去
        #   · 位置每次随机 → 无法靠"裁掉固定区域"规避
        #   · 半透明浅灰 → 不干扰阅读
        # ⚠ 它拦不住会改代码的人；价值是让流转的截图带上身份。
        self._wm_win = None
        self._wm_after_id = None
        self._wm_seed = ""
        self._wm_anchor = None       # 水印窗口锚在哪个 frame 上
        try:
            self._wm_anchor = listf
            self._init_overlay_watermark(listf)
            # ★ v2.0.19 诊断：把创建结果打进日志，避免"静默失败"查不到原因
            w = getattr(self, "_wm_win", None)
            if w is not None:
                self.push_log("info", "🔒 水印层已创建（列表区浮层，缓慢飘动）")
            else:
                self.push_log("warn", "⚠ 水印层未创建（窗口无浮层，"
                                      "不影响其它功能）")
        except Exception as _e:                             # noqa: BLE001
            self._wm_win = None
            # ★ 关键：以前这里静默吞异常，导致"看不到水印"无法定位。
            #   现在明确记日志（含异常类型），便于排查。
            self.push_log("warn", f"⚠ 水印层创建失败：{type(_e).__name__}: "
                                  f"{_e}（不影响软件正常使用）")
        self.tree.tag_configure("tag_done", foreground="#15803d")
        self.tree.tag_configure("tag_failed", foreground="#dc2626")
        self.tree.tag_configure("tag_downloading", foreground="#2563eb")
        self.tree.tag_configure("tag_decrypting", foreground="#d97706")
        self.tree.tag_configure("tag_pending", foreground="#6b7280")
        self.tree.tag_configure("tag_waiting", foreground="#0f766e")
        self.tree.tag_configure("tag_stopped", foreground="#9ca3af")
        self.tree.bind("<Double-1>", self._on_tree_dclick)
        self.tree.bind("<Control-a>", lambda e: self._select_all())
        # v2.0.19：鼠标移到「来源」列表头时提示「双击可筛选」（见 _on_tree_motion）
        self.tree.bind("<Motion>", self._on_tree_motion)

        # ---- 工具栏 A：下载操作 ----
        self.btn_download = ttk.Button(btns1, text="⬇ 下载选中", width=10,
                                       command=lambda: self._download_selected())
        self.btn_download.pack(side="left")
        self._tt(self.btn_download,
                 "把选中的「待下载/失败/已停止」记录加入下载队列。\n"
                 "支持 Ctrl/Shift 多选；双击待下载行也可下载。\n"
                 "多个一起下：先 Ctrl+A 全选或 Shift 连选，再点这里。")

        # ★ v2.1.9：全选 / 反选（客户反馈不知道 Ctrl+A，给可见按钮）
        self.btn_sel_all = ttk.Button(btns1, text="全选", width=6,
                                      command=self._select_all)
        self.btn_sel_all.pack(side="left", padx=(4, 0))
        self._tt(self.btn_sel_all,
                 "选中列表里所有记录（等同 Ctrl+A）。\n"
                 "选完点『下载选中』即可批量下载。")
        self.btn_sel_inv = ttk.Button(btns1, text="反选", width=6,
                                      command=self._invert_selection)
        self.btn_sel_inv.pack(side="left", padx=2)
        self._tt(self.btn_sel_inv,
                 "把选中的变成不选、没选的变成选中。\n"
                 "常用于：先全选 → 反选 → 只下没下过的。")

        self.btn_stop = ttk.Button(btns1, text="停止下载", width=9,
                                   command=lambda: self._stop_selected())
        self.btn_stop.pack(side="left", padx=4)
        self._tt(self.btn_stop, "停止选中的「排队中/下载中/解密中」任务。")
        self.btn_stop_all = ttk.Button(btns1, text="停止所有下载", width=12,
                                       command=self._stop_all_downloads)
        self.btn_stop_all.pack(side="left", padx=4)
        self._tt(self.btn_stop_all,
                 "一键停止所有「排队中/下载中/解密中」的任务。\n"
                 "注意：已完成的保留；停止后可对选中项点『重试失败』重新下载。")
        self.btn_dl_all = ttk.Button(btns1, text="下载全部待下载", width=12,
                                     command=lambda: self._download_all_waiting())
        self.btn_dl_all.pack(side="left", padx=4)
        self._tt(self.btn_dl_all,
                 "把列表里所有「待下载」记录一次性入队。\n"
                 "注意：数量多时按队列依次下载，速度受 CDN 限制。")
        self.btn_cancel_queued = ttk.Button(btns1, text="取消所有排队", width=11,
                                            command=self._cancel_all_queued)
        self.btn_cancel_queued.pack(side="left", padx=4)
        self._tt(self.btn_cancel_queued,
                 "把所有「排队中」的任务恢复成「待下载」状态。\n"
                 "「待下载」任务尚未入队，无需取消；\n"
                 "正在下载中的不会中断；之后可重新点『下载选中』或"
                 "『下载全部待下载』。")
        self.btn_retry = ttk.Button(btns1, text="重试失败", width=9,
                                    command=lambda: self._retry())
        self.btn_retry.pack(side="left", padx=4)
        self._tt(self.btn_retry,
                 "把选中的「失败/已停止」记录重新入队。\n"
                 "注意：链接可能已过期，仍失败需重新嗅探。")
        self.btn_redl = ttk.Button(btns1, text="↻ 重新下载", width=10,
                                   command=lambda: self._redownload_selected())
        self.btn_redl.pack(side="left", padx=4)
        self._tt(self.btn_redl,
                 "对「已完成/失败/已停止」记录重新下载，成功后覆盖旧文件。\n"
                 "文件打不开/播放不了时用它。")
        # ---- 工具栏 B：文件与记录管理 ----
        self.btn_open_file = ttk.Button(btns2, text="打开文件", width=9,
                                        command=lambda: self._open_file())
        self.btn_open_file.pack(side="left", padx=4)
        self._tt(self.btn_open_file,
                 "用系统默认播放器打开选中记录的已下载文件。")
        self.btn_open_folder = ttk.Button(btns2, text="打开文件夹", width=9,
                                          command=lambda: self._open_folder())
        self.btn_open_folder.pack(side="left")
        self._tt(self.btn_open_folder,
                 "打开选中记录所在文件夹；未选中时打开下载目录。")
        ttk.Separator(btns2, orient="vertical").pack(side="left", fill="y", padx=6)
        self.btn_del_rec = ttk.Button(btns2, text="删除记录", width=9,
                                      command=lambda: self._del_record())
        self.btn_del_rec.pack(side="left", padx=4)
        self._tt(self.btn_del_rec,
                 "删除选中的 N 条记录（视频文件保留）。\n"
                 "支持 Ctrl/Shift 多选。")
        self.btn_del_file = ttk.Button(btns2, text="删除文件+记录", width=11,
                                       command=lambda: self._del_file())
        self.btn_del_file.pack(side="left")
        self._tt(self.btn_del_file,
                 "删除选中的 N 条记录及其已下载文件。\n"
                 "注意：文件删除后不可恢复，谨慎操作。")
        ttk.Separator(btns2, orient="vertical").pack(side="left", fill="y", padx=6)
        self.btn_clr_done = ttk.Button(btns2, text="清空已完成", width=10,
                                       command=lambda: self._clear_done())
        self.btn_clr_done.pack(side="left", padx=4)
        self._tt(self.btn_clr_done, "删除所有「已完成」记录（文件保留）。")
        self.btn_clr_fail = ttk.Button(btns2, text="清空失败", width=9,
                                       command=lambda: self._clear_failed())
        self.btn_clr_fail.pack(side="left")
        self._tt(self.btn_clr_fail, "删除所有「失败」记录（文件保留）。")
        self.btn_del_all = ttk.Button(btns2, text="删除全部记录", width=11,
                                      command=self._del_all_records)
        self.btn_del_all.pack(side="left", padx=(8, 0))
        self._tt(self.btn_del_all,
                 "清空整个视频清单（所有状态）。\n"
                 "只删记录，已下载的文件保留在磁盘。")

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
        self.auto_scroll_chk = ttk.Checkbutton(logf, text="自动滚动",
                                               variable=self.auto_scroll_var)
        self.auto_scroll_chk.pack(anchor="e", pady=(4, 0))
        self._tt(self.auto_scroll_chk,
                 "日志自动滚动到最新；取消勾选可回看历史日志。")
        self.btn_copy_log = ttk.Button(logf, text="📋 复制日志", width=10,
                                       command=self._copy_log)
        self.btn_copy_log.pack(side="right", anchor="e",
                               pady=(4, 0), padx=(0, 4))
        self._tt(self.btn_copy_log,
                 "把日志区内容（含版本/时间/引擎状态）复制到剪贴板，\n"
                 "方便粘贴给开发者排查问题。")
        self.btn_clear_log = ttk.Button(logf, text="清空", width=6,
                                        command=self._clear_log)
        self.btn_clear_log.pack(side="right", anchor="e", pady=(4, 0))
        self._tt(self.btn_clear_log,
                 "清空日志区显示（不影响落盘的完整日志）。")


        self._refresh_tree_all()
        self._update_capture_hint()
        # 首次启动自动弹出「使用说明」（v2.0.16）
        if not self.cfg.guide_auto_shown:
            self.root.after(300, self._open_guide)

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
        """「⚡ 批量拉取」按钮（v2.0.8）：开/关一次。"""
        self._set_batch(not self.addon.batch_mode)

    def _set_batch(self, on: bool, auto: str = ""):
        """批量拉取开关（v2.0.8；v2.0.17 抽出以便「满档自动停止」复用）。

        auto 非空时用于区分自动停止（满档）与用户手动停止的日志文案。
        """
        if on == self.addon.batch_mode and not auto:
            return
        self.addon.batch_mode = on
        self.addon.batch_count = 0
        self.addon._batch_seen_count = self.addon.capture_count  # 续跑信号基线（v2.0.18）
        if on:
            # 批量模式：列表数据全部入列（与「🎯 自然捕获」互斥）
            self.addon.natural_capture = False
            self.natural_var.set(False)
            self.addon._batch_active = True
            self.btn_batch.config(text="■ 停止批量")
            self.push_log("ok",
                "⚡ 批量拉取已开启：列表数据将整页入库。\n"
                "只滚动「当前选中」的视频号窗口（最近点击/获得焦点者）；"
                "即使被 Excel 等窗口遮挡也持续滚动，直到点『停止拉取』"
                "或拉到底自动停止。\n"
                "若要切换拉取对象，点击/选中目标视频号窗口即可。\n"
                "（若页面是旧版本注入的未带驱动，请重新打开一次视频号页面）")
        else:
            self.addon.natural_capture = self.cfg.natural_capture
            self.natural_var.set(self.cfg.natural_capture)
            self.addon._batch_active = False
            self.btn_batch.config(text="⚡ 批量拉取")
            if auto:
                pass          # 自动停止的提示由调用方（满档）给出，避免重复
            else:
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

    def _cleanup_stale_tmp(self):
        """启动时清理超期断点文件（v2.2.0）。

        ★ 背景：为了让"停止嗅探/网络中断"后能续传，失败时保留 .tmp。
          但客户可能有一堆失败任务，.tmp 会占磁盘。
          规则：只删 **7 天以上未修改** 的 .tmp。
          （正在使用的不可能 7 天没动过，安全）
        """
        try:
            d = Path(self.cfg.download_dir)
            if not d.exists():
                return
            cutoff = time.time() - 7 * 86400
            removed = 0
            freed = 0
            for f in d.glob("*.tmp"):
                try:
                    st = f.stat()
                    if st.st_mtime < cutoff:
                        freed += st.st_size
                        f.unlink()
                        removed += 1
                except Exception:
                    continue
            if removed:
                self.push_log(
                    "info",
                    f"🧹 已清理 {removed} 个过期断点文件"
                    f"（{freed / 1024 / 1024:.1f} MB，超过 7 天未使用）")
        except Exception:
            pass

    def _check_pending_stop(self):
        """等待下载完成后停止嗅探（v2.2.0）。

        用户点「停止嗅探」时选了【是】→ 设 _pending_stop_after_downloads，
        这里每 800ms 查一次；没有下载任务了就真正停。

        ★ 为什么需要这个：
          下载流量经 mitmproxy（requests 走系统代理），
          停嗅探会断掉下载网络。客户往往"清单抓够了想收工"，
          但这时可能还有视频在下 —— 直接停会把下载全废掉。
        """
        if not getattr(self, "_pending_stop_after_downloads", False):
            return
        if not getattr(self, "_stop_timer_armed", False):
            self._stop_timer_armed = True

        def _tick():
            self._stop_timer_armed = False
            if not getattr(self, "_pending_stop_after_downloads", False):
                return
            busy = [r for r in self.store.items.values()
                    if r.status in ("downloading", "decrypting", "pending")]
            if busy:
                # 还有任务 → 800ms 后再查（不阻塞 UI）
                self.root.after(800, self._check_pending_stop)
                return
            # 队列空了 → 真正停止
            self._pending_stop_after_downloads = False
            self.push_log("ok", "✅ 下载任务已全部结束，正在停止嗅探…")
            try:
                self.btn_engine.config(state="normal", text="■  停止嗅探")
            except Exception:
                pass
            self._start_pending = False
            self.btn_engine.config(state="disabled")
            threading.Thread(target=self._stop_engine_job,
                             daemon=True).start()

        self.root.after(800, _tick)

    def _toggle_engine(self):
        if self.engine.running:
            # ★ v2.2.0：停止嗅探前先检查有没有下载任务（客户实测反馈）
            #
            #   根因：下载器用 requests（trust_env=True）→ **走系统代理**，
            #   也就是所有下载流量都经 mitmproxy 转发。
            #   「停止嗅探」= mitmproxy 退出 + 系统代理恢复，
            #   → 正在进行的下载连接被 RST → 失败。
            #   （旧版还会删掉 .tmp，导致已下载部分全丢，客户体验"下载停了"）
            #
            #   三种处理，按任务状态给不同选项：
            #     有「下载中」 → 问「一起停 / 等它下完再停 / 取消」
            #     只有「排队中」→ 问「取消排队一起停 / 取消」
            #     没有任务   → 直接停
            busy = [r for r in self.store.items.values()
                    if r.status in ("downloading", "decrypting")]
            queued = [r for r in self.store.items.values()
                      if r.status == "pending"]

            if busy:
                n_dl = len(busy)
                n_q = len(queued)
                msg = (f"现在还有 {n_dl} 个视频正在下载"
                       + (f"、{n_q} 个在排队" if n_q else "") + "。\n\n"
                       "停止嗅探会断开下载用的网络通道，"
                       "正在下载的会中断。\n\n"
                       "【是】= 先等它们下完，自动停止嗅探\n"
                       "【否】= 立刻停止（已下载的部分会保留，之后可续传）\n"
                       "【取消】= 不停嗅探")
                ans = messagebox.askyesnocancel("还有下载任务", msg,
                                                parent=self.root)
                if ans is None:
                    return                       # 取消 → 什么都不做
                if ans:
                    # 是 → 挂起停止，等队列空了再停
                    self._pending_stop_after_downloads = True
                    self.btn_engine.config(state="disabled",
                                           text="等待下载完成后停止…")
                    self.push_log("info",
                                  f"⏳ 将在 {n_dl} 个下载任务完成后自动停止嗅探")
                    self._check_pending_stop()
                    return
                # 否 → 立刻停（.tmp 已由 downloader 保留，可续传）
                self.push_log("warn",
                              "⚠ 立即停止嗅探：正在下载的任务会中断，"
                              "已下载的部分已保留，之后点「重试失败」可续传")

            self.btn_engine.config(state="disabled")
            self._start_pending = False   # 用户主动停止 → 不算启动失败，别弹窗
            threading.Thread(target=self._stop_engine_job, daemon=True).start()
            return
        # ★ v2.1.1：点下去立刻给反馈（原先"点了没反应"，用户以为按钮坏了）
        #   启动流程要等端口监听（最长 30 秒），期间必须有可见状态。
        try:
            self.btn_engine.config(state="disabled", text="启动中…")
            self.push_log("info", "▶ 正在启动嗅探引擎…（首次启动需 3~10 秒，"
                                  "杀毒软件扫描可能更慢）")
            self.root.update_idletasks()
        except Exception:
            pass
        self._start_pending = True    # 标记：这次是用户发起的启动，失败要弹窗
        # 解析端口
        try:
            port = int(self.port_var.get())
            assert 1 <= port <= 65535
        except Exception:
            messagebox.showerror("端口无效", "请输入 1-65535 的端口号")
            try:
                self.btn_engine.config(state="normal", text="▶  启动嗅探")
            except Exception:
                pass
            return
        auto = bool(self.auto_var.get())
        # ★ v2.1.1：把 CA 状态打进日志，用户/售后一眼看到卡在哪
        try:
            _ca_file = self._ca_file_exists()
            self.push_log("info", f"ℹ CA 状态: {self.ca_state}｜证书文件: "
                                  f"{'已生成' if _ca_file else '未生成'}")
        except Exception:
            _ca_file = False
        # ★ CA 证书检查（2026-10-17 修死循环）
        #
        #   原逻辑：CA 未装 → 弹窗问"要装吗" → 用户点"是" → 拉起向导 + return
        #   → **嗅探没启动** → mitmproxy 不生成 CA → 向导找不到证书文件
        #   → 装不上 → 回来再点启动 → 又是未装 → 死循环。
        #   客户实测就卡在这里（看到空白命令行 + "未安装"）。
        #
        #   新逻辑：**先让嗅探启动**（它会在几秒内生成 CA），再提示去装。
        #   这样证书文件必然存在，向导一定能成功。
        if self.ca_state != "installed":
            if not _ca_file:
                # 证书还没生成 → 必须先启动嗅探
                self.push_log(
                    "info", "ℹ 首次使用：先启动嗅探生成证书，再点【安装/检测】安装")
                try:
                    messagebox.showinfo(
                        "首次使用：顺序很重要",
                        "第一次使用需要装一个证书，但证书要等嗅探启动后才会生成。\n\n"
                        "所以顺序是：\n"
                        "  1️⃣ 现在点【确定】→ 嗅探会自动启动\n"
                        "  2️⃣ 等 3~5 秒（证书在这时生成）\n"
                        "  3️⃣ 点【安装/检测】→ 按提示点【是】\n"
                        "  4️⃣ 装好后就能正常抓视频了\n\n"
                        "（放心：这一步只是生成本机证书，不影响其它网站。）",
                        parent=self.root)
                except Exception:
                    pass
            else:
                # 证书文件已存在但没装 → 可以现在就装（也可以先启动，都行）
                if messagebox.askyesno(
                        "CA 证书未安装",
                        "检测到 mitmproxy CA 证书还没安装。\n"
                        "不安装则 HTTPS 流量无法解密，嗅探大概率失败。\n\n"
                        "现在就安装吗？\n"
                        "（点「否」则先启动嗅探，稍后再点【安装/检测】）",
                        parent=self.root):
                    self._launch_cert_wizard()
                    # ★ 注意：这里**不再 return** —— 用户装完回来可以直接用，
                    #   但当前这次点击也让他继续启动，避免"装完还要再点一次"。
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
        # ★ v2.1.2：**启动失败必须弹窗**（客户实测"点了没反应"）
        #   以前失败只写日志，用户不看日志区就完全不知道发生了什么。
        #   用 _start_pending 标记"这次是我发起的启动"，只有这种才弹。
        if not running and getattr(self, "_start_pending", False):
            self._start_pending = False
            try:
                messagebox.showerror(
                    "启动嗅探失败",
                    "嗅探引擎没能启动起来。\n\n"
                    "常见原因和处理办法：\n\n"
                    "① 端口被占用（最常见）\n"
                    "   把端口改成 8898 或 9999 再试\n\n"
                    "② 杀毒软件拦截\n"
                    "   把本程序加入白名单，或临时关闭杀软后重试\n\n"
                    "③ 残留进程\n"
                    "   任务管理器里结束所有「微信视频号下载器」进程再重试\n\n"
                    "④ 首次解压被杀软扫描拖慢\n"
                    "   直接再点一次【启动嗅探】即可\n\n"
                    "如果都不行，请运行压缩包里的「诊断-双击我.bat」，\n"
                    "把弹出的窗口截图发给发布方。",
                    parent=self.root)
            except Exception:
                pass
        elif running:
            self._start_pending = False

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

    def _ca_file_exists(self) -> bool:
        """mitmproxy 的 CA 证书文件是否已生成。

        ★ 为什么需要先查这个（2026-10-17 客户实测踩坑）：
          mitmproxy 的 CA 是**第一次启动嗅探时**才生成到
          %USERPROFILE%\\.mitmproxy\\ 的。如果用户在启动嗅探前就点
          「安装/检测」，脚本找不到证书文件 → 弹出一个空白/报错的命令行，
          用户完全不知道为什么（只会看到"未安装"）。
          所以在拉起向导前先判断，没生成就直接给出**可操作的指引**。
        """
        try:
            base = Path.home() / ".mitmproxy"
            for nm in ("mitmproxy-ca-cert.pem", "mitmproxy-ca-cert.cer"):
                if (base / nm).exists():
                    return True
        except Exception:
            pass
        # 注意：**不再**回退查 BASE_DIR/ca.pem —— 项目里那个是开发时的样例
        # 文件，会被误判成"已有证书"，导致新用户点启动时不提示、装证书时
        # 又用到不对的文件。只认 mitmproxy 真正生成的路径。
        return False

    def _on_cert_btn(self):
        # 先检查证书文件是否已生成 —— 没有就别拉起必然失败的向导
        if not self._ca_file_exists():
            self.push_log(
                "warn", "⚠ 还没有 CA 证书文件 —— 请先点【▶ 启动嗅探】并等 3~5 秒，"
                        "证书会自动生成，然后回来点【安装/检测】")
            try:
                messagebox.showinfo(
                    "请先启动嗅探，再装证书",
                    "还没有生成 CA 证书文件。\n\n"
                    "这是正常现象：证书要等软件**第一次启动嗅探**时才会自动生成。\n\n"
                    "请按这个顺序操作：\n"
                    "  1️⃣ 点【▶ 启动嗅探】（就在这个按钮上面）\n"
                    "  2️⃣ 等 3~5 秒（此时会自动生成证书）\n"
                    "  3️⃣ 再回来点【安装/检测】\n\n"
                    "本机证书目录：\n"
                    f"{Path.home() / '.mitmproxy'}",
                    parent=self.root)
            except Exception:
                pass
            return                       # ★ 不拉导向导
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
        self.push_log(
            "info", "已打开证书安装向导（3 步）：按窗口提示操作，"
                    "遇到 Windows 安全警告点【是】；完成后回来点【安装/检测】确认")

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
        """全选（v2.1.9：加可见按钮，不再只靠 Ctrl+A）。"""
        try:
            kids = self.tree.get_children()
            if kids:
                self.tree.selection_set(kids)
        except Exception:
            pass

    def _invert_selection(self):
        """反选（v2.1.9）。

        典型用法：先「全选」→ 再「反选」→ 只留下原来没选的
        （比如列表里大部分已下载完，想挑出没下的）。
        """
        try:
            kids = list(self.tree.get_children())
            cur = set(self.tree.selection())
            new = [k for k in kids if k not in cur]
            self.tree.selection_set(new) if new else self.tree.selection_remove(*kids)
        except Exception:
            pass

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
        """把一条记录提交到下载管线（waiting/failed → pending）。

        ★ v2.1.7：这里是**所有下载路径的唯一出口**（手动选中/全部待下载/
          自动下载/重新下载/重试失败），所以授权门禁放在这里最稳 ——
          放上层任何一个按钮里都可能被别的路径绕过。
          未授权 → 直接拒收（不弹窗，避免自动下载时弹窗刷屏；提示由
          `_require_license_for_download()` 在用户主动点按钮时给）。
        """
        rec = self.store.items[rid]
        # ★ 未授权不允许下载（可捕获可预览，只是拿不到文件）
        if not self._has_download_license():
            self.push_log(
                "warn",
                f"🔒 未授权，已跳过下载：{rec.title[:30]}（需视频号授权码）")
            return False
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

    def _has_download_license(self) -> bool:
        """纯判断：当前是否有视频号下载授权（不弹窗、不写日志）。

        ★ 与 `_require_license_for_download()` 的分工：
          · 本函数 —— 给**程序化路径**用（自动下载、统一出口 _submit_record），
            静默返回 True/False，绝不弹窗（否则自动下载会弹窗刷屏）
          · `_require_license_for_download()` —— 给**用户主动点击**的入口用，
            未授权时弹窗引导去激活
        """
        # 绑定版本（LOCKED_MAX_RECORDS 写死）→ 视为已授权
        if self.cfg.locked_max_records is not None:
            return True
        try:
            info = self._license_info(lic.PRODUCT_VIDEO)
            if info and not self._license_expired():
                # 公众号产品码不算视频号授权
                return bool(lic.has_feature(info, lic.PRODUCT_VIDEO))
        except Exception:
            pass
        return False

    def _require_license_for_download(self) -> bool:
        """下载前的授权门禁（v2.1.7）。

        ★ 业务规则（用户要求）：
          未授权时可以**捕获、可以看列表**（让客户看到效果），
          但**不能下载** —— 点下载时提示需要授权。
          这样比"未授权就什么都抓不到"体验好：客户能先看到工具真的有用。

        返回 True = 放行下载；False = 已弹提示，调用方应直接 return。
        """
        if self._has_download_license():
            return True
        # ❌ 未授权
        self.push_log(
            "warn", "🔒 未授权：可以捕获和预览列表，但下载需要视频号授权码")
        if messagebox.askyesno(
                "需要授权才能下载",
                "你还没有激活视频号下载授权。\n\n"
                "现在可以：\n"
                "  ✓ 捕获视频、查看列表（先确认工具确实好用）\n"
                "  ✗ 下载视频文件（需要授权码）\n\n"
                "授权码请向发布方购买。\n\n"
                "现在就打开「🔑 授权管理」粘贴授权码吗？",
                parent=self.root):
            self._on_license_input()
        return False

    def _download_selected(self):
        if not self._require_license_for_download():
            return
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
        if not self._require_license_for_download():
            return
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

    def _stop_all_downloads(self):
        """「停止所有下载」：一键停止所有 排队中/下载中/解密中 的任务。"""
        targets = [r for r in self.store.items.values()
                   if r.status in ("pending", "downloading", "decrypting")]
        if not targets:
            messagebox.showinfo("提示", "当前没有正在下载/排队中的任务")
            return
        if not messagebox.askyesno(
                "停止所有下载",
                f"将停止 {len(targets)} 个正在下载/排队的任务？\n"
                "（已下载完成的文件保留，可稍后『重试失败』再下）"):
            return
        for r in targets:
            try:
                self.pipeline.cancel(r.url)
            except Exception:
                pass
            self.store.update(r.id, status="stopped", error="用户停止")
            self._update_row(r.id)
        self._dirty = True
        self.push_log("ok", f"已停止所有下载：{len(targets)} 个任务已停止")
        self._update_status_bar()

    def _cancel_all_queued(self):
        """「取消所有排队」：把所有排队中的任务恢复为「待下载」状态。"""
        targets = [r for r in self.store.items.values()
                   if r.status == "pending"]
        waiting = [r for r in self.store.items.values()
                   if r.status == "waiting"]
        if not targets:
            if waiting:
                messagebox.showinfo(
                    "提示",
                    f"当前没有排队中的任务。\n"
                    f"列表里有 {len(waiting)} 个『待下载』任务尚未入队，"
                    "无需取消；\n"
                    "如需下载请点『下载全部待下载』或选中后点『下载选中』。")
            else:
                messagebox.showinfo("提示", "当前没有排队中的任务")
            return
        if not messagebox.askyesno(
                "取消所有排队",
                f"将 {len(targets)} 个排队中的任务恢复为『待下载』？\n"
                "正在下载中的不会中断。"):
            return
        for r in targets:
            try:
                # 让管线在轮到这些任务时直接跳过（从队列摘除）
                self.pipeline.cancel(r.url)
            except Exception:
                pass
            self.store.update(r.id, status="waiting", progress=0, error="")
            self._update_row(r.id)
        self._dirty = True
        self.push_log("ok",
            f"已取消所有排队：{len(targets)} 个『排队中』任务恢复为待下载"
            f"（列表现有 {len(waiting) + len(targets)} 个待下载）")
        self._update_status_bar()

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
        """删除选中的 N 条记录（视频文件保留）。"""
        rids = self._selected_rids()
        if not rids:
            messagebox.showinfo("提示",
                                "请先在列表里选中要删除的记录"
                                "（可 Ctrl/Shift 多选）")
            return
        n = len(rids)
        preview = "、".join(self.store.items[r].title[:16] for r in rids[:3])
        if n > 3:
            preview += "…"
        if not messagebox.askyesno("删除记录",
                                   f"删除选中的 {n} 条记录？"
                                   "（视频文件会保留）\n\n" + preview):
            return
        for rid in rids:
            self._remove_row(rid)
        self._dirty = True
        self.push_log("info", f"已删除 {n} 条记录（文件保留）")
        self._update_status_bar()

    def _del_file(self):
        """删除选中的 N 条记录及其已下载文件。"""
        rids = self._selected_rids()
        if not rids:
            messagebox.showinfo("提示",
                                "请先在列表里选中要删除的记录"
                                "（可 Ctrl/Shift 多选）")
            return
        n = len(rids)
        preview = "、".join(self.store.items[r].title[:16] for r in rids[:3])
        if n > 3:
            preview += "…"
        if not messagebox.askyesno("删除文件+记录",
                                   f"将删除选中的 {n} 条记录及其视频文件？\n"
                                   "注意：文件删除后不可恢复！\n\n" + preview):
            return
        removed, failed = 0, []
        for rid in rids:
            rec = self.store.items[rid]
            try:
                if rec.file and os.path.exists(rec.file):
                    os.remove(rec.file)
            except Exception as e:
                failed.append(f"{rec.title[:16]}：{e}")
                continue  # 文件删不掉则保留记录，便于重试
            self._remove_row(rid)
            removed += 1
        self._dirty = True
        if failed:
            self.push_log("error",
                          f"删除文件失败 {len(failed)} 条（记录已保留）: "
                          + "；".join(failed[:5]))
        self.push_log("ok" if not failed else "warn",
                      f"已删除文件+记录 {removed} 条"
                      + (f"，失败 {len(failed)} 条" if failed else ""))
        self._update_status_bar()

    def _del_all_records(self):
        """「删除全部记录」：清空整个视频清单（已下载的文件保留）。"""
        if not self.store.order:
            messagebox.showinfo("提示", "列表已经是空的")
            return
        n = len(self.store.order)
        if not messagebox.askyesno(
                "删除全部记录",
                f"将清空整个视频清单（共 {n} 条记录）？\n"
                "注意：只删记录，已下载的文件保留在磁盘。"):
            return
        for rid in list(self.store.order):
            self._remove_row(rid)
        self._dirty = True
        # ★ v2.2.2：清空全部记录时重置抓取序号，否则客户继续抓会从
        #   "上次的总数+1" 开始（如 101），客户以为软件坏了。
        self._reset_seq_on_clear()
        # ★ 清空 addon.seen（v2.0.18）：删全部记录后重新打开视频号必须能重新捕获。
        # 用 clear_seen 而非逐个 release —— seen 里可能残留列表已无的 key
        # （如候选缓存过的、满档被拒后残留的），逐个释放清不掉，会导致
        # 后续拉取被静默跳过、清单不动（2026-10-16 用户故障的第二个入口）。
        try:
            freed = self.addon.clear_seen()
        except Exception as e:
            freed = 0
            self.push_log("error", f"清空捕获去重集合失败（不影响列表）：{e}")
        self.push_log("ok", f"已清空视频清单：删除全部 {n} 条记录（文件保留）"
                            + (f"；已释放 {freed} 个捕获去重名额"
                               "（这些视频可重新捕获）" if freed else ""))
        self._update_status_bar()

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
        # ★ v2.2.2：如果清完列表变空，序号也重置（否则下次抓从 101 开始）
        if not self.store.order:
            self._reset_seq_on_clear()
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
        # ★ v2.2.2：如果清完列表变空，序号也重置
        if not self.store.order:
            self._reset_seq_on_clear()
        self.push_log("info", f"已清空 {len(failed)} 条失败记录")

    def _col_index(self, name: str) -> int:
        """取列在 Treeview values 里的索引（0-based）。找不到返回 -1。

        ★ 为什么不硬编码数字（v2.2.0 教训）：
          v2.2.0 加了「序号」列后，原来「来源」列从第 3 列变成第 4 列，
          但代码里写死了 `col == "#3"` 和 `vals[2]` →
          **双击来源列筛选直接失效**（而且不报错，静默失效）。
          改为按列名查，以后再加列不会重犯。
        """
        try:
            return list(self.tree["columns"]).index(name)
        except Exception:
            return -1

    def _on_tree_dclick(self, evt):
        # ★ v2.0.19：双击「来源」列的单元格 → 按该来源筛选清单
        #   （不只作用于被双击的那一行，而是筛出该类全部记录，便于批量操作）
        try:
            col_id = self.tree.identify_column(evt.x)
            row = self.tree.identify_row(evt.y)
            pi = self._col_index("platform")
            # identify_column 返回 #1 起的序号 → 换算成 0-based 索引
            try:
                col_0based = int(col_id.lstrip("#")) - 1
            except Exception:
                col_0based = -1
            if row and pi >= 0 and col_0based == pi:
                vals = self.tree.item(row, "values")
                plat_cell = str(vals[pi]) if len(vals) > pi else ""
                plat = "公众号" if "公众号" in plat_cell else "视频号"
                self._filter_by_platform(plat)
                return
        except Exception:
            pass

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

    def _on_tree_motion(self, evt):
        """鼠标移到「来源」列时，把光标变手型并给一行提示（v2.0.19）。

        目的是让用户**发现**"双击来源列可以筛选"这个操作 —— 光靠
        status_bar 的一句文字很多人看不到。这里在鼠标进入该列时
        直接把提示打到状态栏（频率限制，避免抖动）。
        """
        try:
            col_id = self.tree.identify_column(evt.x)
            region = self.tree.identify_region(evt.x, evt.y)
        except Exception:
            return
        # ★ v2.2.0：按列名判断（不硬编码 #3，加「序号」列后会错位）
        pi = self._col_index("platform")
        try:
            col_0based = int(col_id.lstrip("#")) - 1
        except Exception:
            return
        if pi < 0 or col_0based != pi:
            return
        now = time.time()
        if now - getattr(self, "_plat_hint_at", 0) < 3:
            return
        self._plat_hint_at = now
        cur = getattr(self, "_platform_filter", "")
        if cur:
            msg = (f"💡 当前已筛选「{cur}」。双击任意「来源」单元格可切换/取消筛选")
        else:
            msg = "💡 双击「来源」列的任意单元格，可按 视频号 / 公众号 筛选清单"
        try:
            self.push_log("info", msg)
        except Exception:
            pass

    def _filter_by_platform(self, plat: str):
        """按来源筛选清单（v2.0.19）。

        plat: "视频号" / "公众号" / ""（空 = 取消筛选，显示全部）
        筛选只是**视图过滤**，不删除任何记录；下载/删除等操作仍作用于
        当前可见行（选中行必然可见）。
        """
        self._platform_filter = plat or ""
        self._render_filtered_tree()
        if self._platform_filter:
            n = sum(1 for r in self.store.items.values()
                    if (r.platform or "视频号") == self._platform_filter)
            self.push_log(
                "info",
                f"🔍 已筛选来源＝{self._platform_filter}（{n} 条）。"
                "再双击「来源」列的任意行，或点状态栏「显示全部」可取消筛选。")
        else:
            self.push_log("info", "🔍 已取消来源筛选，显示全部记录")
        self._update_status_bar()

    def _render_filtered_tree(self):
        """按当前 platform 筛选重绘清单（仅重绘可见行）。"""
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._row_of.clear()
        flt = getattr(self, "_platform_filter", "")
        for rid in self.store.order:
            r = self.store.items.get(rid)
            if r is None:
                continue
            if flt and (r.platform or "视频号") != flt:
                continue
            iid = self.tree.insert(
                "", "end", values=self._row_values(r),
                tags=(STATUS_TAG.get(r.status, "tag_pending"),))
            self._row_of[r.id] = iid
        self._apply_sort()

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
        # v2.0.19：platform 列区分来源（视频号 / 公众号）
        #   前面加图标，一眼分辨；双击该单元格可按来源筛选（见 _on_tree_dclick）
        plat = r.platform or "视频号"
        plat_cell = ("📄 公众号" if plat == "公众号" else "🎬 视频号")
        # 文件名过长时中间省略（保留扩展名），避免右侧被硬切掉一半看不出是什么
        fname = ""
        if r.file:
            fname = Path(r.file).name
            if len(fname) > 22:
                stem, dot, ext = fname.rpartition(".")
                if dot and len(ext) <= 5:
                    fname = stem[:14] + "…" + ext
                else:
                    fname = fname[:20] + "…"
        # ★ v2.2.0：序号列先留空，插入后由 _renumber_rows() 按显示顺序填。
        #   （不能在 _row_values 里算：那时还不知道会插到第几行）
        return ("", st, title, plat_cell,
                format_size(r.size) if r.size else "-",
                r.source, r.created[5:], fname)

    def _update_row(self, rid):
        r = self.store.items.get(rid)
        if not r:
            return
        iid = self._row_of.get(rid)
        if iid and self.tree.exists(iid):
            self.tree.item(iid, values=self._row_values(r),
                           tags=(STATUS_TAG.get(r.status, "tag_pending"),))
            self._apply_sort()

    def _reset_seq_on_clear(self):
        """清空全部记录时重置抓取序号计数器（v2.2.2）。

        ★ 为什么（客户会困惑）：
          如果只清 store 不重置计数器，客户「删除全部记录」后继续抓，
          序号会从 101 开始（因为计数器还停在 100）——
          客户看到"为什么不是从 1 开始？是不是软件坏了？"

        ★ 为什么删部分记录**不**重置：
          列表里还有老记录，重置会让新记录序号与现存重复，
          排序时就分不清先抓后抓了。删除部分只留空档（如 1,3,5），
          序号列会重编成 1,2,3，客户看不到空档。
        """
        self._seq_counter = 0

    def _remove_row(self, rid):
        # 同步清理 url 映射（可能有多条：切换清晰度后旧/新 url 都映射本记录）。
        # 否则删除记录后同 URL 再捕获会被误判「重复链接」而无法重新入列表。
        rec = self.store.items.get(rid)
        if rec is not None and rec.video_id:
            self._vid_index.discard(rec.video_id)
            # ★ 释放 addon.seen（v2.0.18）：这是**所有单条删除路径的统一出口**
            # （删除记录 / 删除文件+记录 / 清空已完成 / 清空失败 / 删除全部记录）。
            # 不释放的话，删完记录重新打开视频号时，同一批视频全部命中 seen
            # 被判「重复视频」跳过、清单不动（2026-10-16 用户报告的真实故障）。
            try:
                if getattr(self, "addon", None) is not None:
                    self.addon.release_seen([rec.video_id])
            except Exception:
                pass
        for k in [k for k, v in self._url_to_rid.items() if v == rid]:
            self._url_to_rid.pop(k, None)
        self.store.remove(rid)
        iid = self._row_of.pop(rid, None)
        if iid and self.tree.exists(iid):
            self.tree.delete(iid)

    # ---------------- 原始数据诊断（v2.2.1） ----------------
    DIAG_FLAG = "开启原始数据记录.txt"

    def _diag_raw_enabled(self) -> bool:
        """是否开启原始 object_desc 落盘（诊断用）。

        启动方式：在**程序所在目录**新建一个空文件
        `开启原始数据记录.txt`（内容无所谓），重启程序即生效。
        关闭：删掉这个文件。
        """
        if not hasattr(self, "_diag_flag_cache"):
            try:
                self._diag_flag_cache = (BASE_DIR / self.DIAG_FLAG).exists()
            except Exception:
                self._diag_flag_cache = False
        return bool(self._diag_flag_cache)

    def _dump_raw_desc(self, vd):
        """把一条捕获的原始 object_desc 存成 json（供分析顺序字段）。

        ★ 为什么要这个（客户需求 2026-10-20）：
          客户要求「下载顺序、标题与视频号里的顺序一致」。
          但**视频号订阅流的原始数据里是否有位置序号字段，我无法凭空假设** ——
          必须看真实样本才能确定用什么字段排序。
          这个开关就是为了拿样本，不改任何正常逻辑。

        存放：<程序目录>/diag_object_desc/<时间戳>_<video_id>.json
        上限：最多 200 个文件（避免占满磁盘）
        """
        try:
            import json as _json
            raw = getattr(vd, "raw", None)
            if not raw:
                return
            d = Path(BASE_DIR) / "diag_object_desc"
            d.mkdir(parents=True, exist_ok=True)
            # 限流：超过 200 个就不再写
            if len(list(d.glob("*.json"))) >= 200:
                return
            vid = (getattr(vd, "video_id", "") or "novid")[:16]
            ts = time.strftime("%H%M%S")
            fn = d / f"{ts}_{vid}.json"
            fn.write_text(_json.dumps(raw, ensure_ascii=False, indent=1)[:200000],
                          encoding="utf-8")
        except Exception:
            pass

    def _next_seq(self) -> int:
        """分配下一个抓取序号（v2.2.1）。

        ★ 为什么单独维护计数器（不复用 len(store)）：
          删记录后 len 会变小，再新抓的序号会与已有记录**重复**，
          客户按序号排序就会乱。用 max(已有 seq)+1 保证单调递增。

        ★ 启动时（_load 之后）第一次调用要扫描已有记录取最大值，
          这样重启后继续抓，序号接着往下排，不会从头开始。
        """
        if not hasattr(self, "_seq_counter"):
            mx = 0
            try:
                for rr in self.store.items.values():
                    mx = max(mx, int(getattr(rr, "seq", 0) or 0))
            except Exception:
                mx = 0
            self._seq_counter = mx
        self._seq_counter += 1
        return self._seq_counter

    def _add_row(self, r: Record):
        iid = self.tree.insert("", 0, values=self._row_values(r),
                               tags=(STATUS_TAG.get(r.status, "tag_pending"),))
        self._row_of[r.id] = iid
        self._url_to_rid[r.url] = r.id
        if r.video_id:
            self._vid_index.add(r.video_id)
        self._apply_sort()

    def _drop_records_ui(self, rec_list):
        """把「已从 store 删除的记录」从界面与去重索引中清干净。

        注意：这些记录已不在 store.items 里，必须用传进来的 Record 数据清
        _vid_index —— 否则该视频被永久判「列表中已有」、再也回不到列表
        （v2.0.16 的去重黑洞缺陷）。只删列表记录，磁盘文件一律保留。

        删记录后若列表不再满，**重置满档门闩并提示恢复加载**：这是"买小套餐
        的客户勤删记录就能一直拉新视频"的关键路径（v2.0.17 用户需求）。

        v2.0.18：**同时释放 addon 侧的 seen 去重名额**。否则删完记录重新打开
        视频号时，同一批视频会全部命中 seen 被判「重复视频」跳过、清单不动
        （2026-10-16 用户报告的真实故障）。
        """
        for rec in rec_list:
            if rec.video_id:
                self._vid_index.discard(rec.video_id)
            for k in [k for k, v in self._url_to_rid.items() if v == rec.id]:
                self._url_to_rid.pop(k, None)
            iid = self._row_of.pop(rec.id, None)
            if iid and self.tree.exists(iid):
                self.tree.delete(iid)
        if rec_list:
            # ★ 释放 addon.seen：使这些视频可被重新捕获（v2.0.18）
            try:
                self.addon.release_seen(r.video_id for r in rec_list)
            except Exception as e:
                self.push_log("error", f"释放捕获去重名额失败（不影响列表）：{e}")
            self._after_records_removed()

    def _after_records_removed(self):
        """删除记录后的容量状态回调：列表不再满 → 复位门闩 + 提示恢复加载。

        满档门闩（_limit_warned / _full_event_pending）必须随"列表脱离满档"
        复位，否则客户删记录后再次填满时将**静默无提示**（只在换档位时复位
        是不够的——删记录同样会让列表脱离满档）。
        """
        if self.store.is_full():
            self._rejected_count = 0
            self._update_status_bar()
            return
        was_limited = self._limit_warned or self._full_event_pending or self._rejected_count
        self._limit_warned = False
        self._full_event_pending = False
        if was_limited:
            self._rejected_count = 0
            self.push_log(
                "ok",
                f"✅ 列表已降至 {len(self.store.order)}/{self.store.max_records} 条，"
                "已恢复加载新捕获的视频（继续拉取即可，无需重启）")
        # ★ v2.1.6：列表脱离满档 → 恢复被「满档」自动停掉的批量拉取
        self._maybe_resume_batch()
        self._update_status_bar()

    def _maybe_resume_batch(self):
        """若批量拉取是被「列表满档」自动停掉的，且现在已不满 → 自动恢复。

        ★ 为什么需要（2026-10-17 客户实测）：
          用户开着批量拉取，抓到 500 条满档 → `_warn_full_once()` 自动停批量
          → 用户去「🔑 授权管理」升级到 1000 档
          → 档位变了但没有恢复批量 → 用户看到"批量被关掉了，抓取还卡在 500"
          → **误以为是授权动作导致的**，实际是满档先停的。

          修复后：档位升上去 / 删掉记录，只要列表脱离满档，批量自动恢复。
          用户视角变成"升级完就继续拉了"，符合直觉。

        ⚠️ 只恢复**因满档自动停止**的批量（`_batch_stopped_by_full`），
           用户主动点「停止批量」的绝不自动重启。
        """
        if not getattr(self, "_batch_stopped_by_full", False):
            return
        if self.addon.batch_mode:
            self._batch_stopped_by_full = False
            return
        if self.store.is_full():
            return                      # 仍满档，别恢复（会空转）
        self._batch_stopped_by_full = False
        try:
            self._set_batch(True)
            self.push_log(
                "ok",
                f"⚡ 列表上限已提升到 {self._tier_label_of()}，"
                f"批量拉取已自动恢复（当前 {len(self.store.order)}/"
                f"{self.store.max_records} 条）")
        except Exception as e:                            # noqa: BLE001
            self.push_log("warn", f"⚠ 批量拉取自动恢复失败：{e}")

    def _warn_full_once(self):
        """列表满档时给出一次明确提示（并自动停掉批量滚动，避免空转）。"""
        if self._limit_warned:
            return
        self._limit_warned = True
        # ★ v2.1.7：未授权时是"试用上限"语义，不是"档位满"
        if self.store.max_records <= 0:
            self.push_log(
                "warn",
                f"⚠️ 已达试用上限（未授权，最多 {config.UNLICENSED_CAPTURE_LIMIT} 条）："
                "已停止加载后续视频（不会顶掉已有记录）。\n"
                "💡 试用期内可以**捕获、查看列表**，但**下载需要授权码**。\n"
                "如需完整使用：点「🔑 授权管理」粘贴授权码后即可下载、并解除条数限制。")
        else:
            self.push_log(
                "warn",
                f"⚠️ 列表已满（{self._tier_label_of()}，共 {self.store.max_records} 条）："
                "已停止加载后续视频（不会顶掉已有记录）。\n"
                "如需继续加载：点「🔑 授权管理」升级档位；或删除部分记录后继续拉取。\n"
                "（满档期间未加载的视频不会被记成「已捕获」，"
                "升级档位或删记录后重新播放/拉取即可加载）")
        if self.addon.batch_mode:
            self.push_log("warn",
                          "⚡ 批量拉取已自动停止（列表满，继续滚动也不会再加载）\n"
                          "  → 升级档位或删掉部分记录后，批量会**自动恢复**")
            # ★ 标记"是满档停的"，供 _maybe_resume_batch() 判断能不能自动恢复
            self._batch_stopped_by_full = True
            self._set_batch(False, auto="full")

    def _refresh_tree_all(self):
        self.tree.delete(*self.tree.get_children())
        self._row_of.clear()
        self._url_to_rid.clear()
        self._vid_index.clear()
        for rid in self.store.order:
            r = self.store.items.get(rid)
            if r:
                self._add_row(r)
        self._apply_sort()

    # ---------------- 表头排序（v2.0.12） ----------------
    def _on_tree_sort(self, col):
        """点击表头：按该列升降序排列（再点一次切换方向）。"""
        if self._sort_col == col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col = col
            self._sort_desc = False
        for c, base in self._head_text.items():
            self.tree.heading(c, text=base)
        self.tree.heading(col, text=self._head_text[col]
                          + (" ▼" if self._sort_desc else " ▲"))
        self._apply_sort()

    def _sort_key(self, rid):
        r = self.store.items.get(rid)
        if r is None:
            return (0, "", "")
        col = self._sort_col or "time"
        # ★ v2.2.1：序号列排序 = **抓取先后顺序**（客户需求）
        #   用途：客户在视频号里按某个顺序刷，希望下载顺序跟那个顺序一致。
        #   seq 是捕获时递增分配的（1,2,3...），排序即还原"刷到的先后"。
        if col == "no":
            return (int(getattr(r, "seq", 0) or 0), r.created, r.id)
        if col == "status":
            return (STATUS_SORT_ORDER.get(r.status, 99), r.created, r.id)
        if col == "size":
            return (int(r.size or 0), r.created, r.id)
        if col == "time":
            return (r.created, r.id)
        if col == "title":
            return ((r.title or "").casefold(), r.created, r.id)
        if col == "platform":
            return ((r.platform or "视频号"), r.created, r.id)
        if col == "source":
            return ((r.source or "").casefold(), r.created, r.id)
        if col == "file":
            return ((r.file or "").casefold(), r.created, r.id)
        return (0, "", "")

    def _apply_sort(self):
        if self._sort_col:
            items = [(rid, iid) for rid, iid in self._row_of.items()
                     if rid in self.store.items]
            items.sort(key=lambda t: self._sort_key(t[0]), reverse=self._sort_desc)
            for idx, (_rid, iid) in enumerate(items):
                if self.tree.exists(iid):
                    self.tree.move(iid, "", idx)
        # ★ v2.2.0：排序后重新编号（序号必须反映**当前显示顺序**）
        self._renumber_rows()

    def _renumber_rows(self):
        """按当前显示顺序重编「序号」列（1,2,3...）。

        ★ 为什么不用固定序号（客户需求 2026-10-20）：
          客户想"只下前 20 个"，要看的是**当前列表第几个**。
          如果按内部 id 编号，排序后就乱了，客户无法按序号选。
          所以每次排序/增删都要重编。

        性能：Treeview 逐行 set 一个单元格，1000 行约 10ms，可接受。
        """
        try:
            kids = self.tree.get_children()
            for i, iid in enumerate(kids, 1):
                try:
                    self.tree.set(iid, "no", str(i))
                except Exception:
                    continue
        except Exception:
            pass

    # ---------------- 事件处理（主线程） ----------------
    def _handle_event(self, item):
        kind = item[0]
        # ★ v2.0.19：公众号视频窗口的事件（解析完成 / 下载完成）优先分发给
        #   当前打开的窗口处理器；没开窗口就忽略（窗口关了自然收不到）。
        if kind in ("mp_parse_done", "mp_dl_done"):
            h = getattr(self, "_mp_event_handler", None)
            if h is not None:
                try:
                    if h(item):
                        return
                except Exception as e:
                    self.push_log("error", f"公众号视频事件处理失败：{e}")
        if kind == "full":
            # 列表满档（v2.0.17）：给出一次明确提示 + 自动停掉批量滚动
            self._full_event_pending = False
            self._warn_full_once()
            self._update_status_bar()
        elif kind == "engine_state":
            self._apply_engine_state(bool(item[1]))
        elif kind == "ca_state":
            self._apply_ca_state(item[1])
        elif kind == "capture":
            self._pending_slots = max(0, self._pending_slots - 1)
            vd, source = item[1], item[2]
            # 满档 = 不再加载后续视频（v2.0.17 策略：不顶掉已有记录）
            if self.store.is_full():
                self._rejected_count += 1
                self._warn_full_once()
                self._update_status_bar()
                return
            # 跨会话去重：同一视频（video_id=md5sum）或同一 URL 已在列表 → 跳过。
            # （addon 的内存去重只在本次 GUI 运行内有效，重启后靠这里兜底）
            # v2.0.17：video_id 走集合索引（O(1)），不再线性扫全表（不限档位下会 O(n²)）
            if vd.video_id and vd.video_id in self._vid_index:
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
                renditions=vd.renditions or [], quality_note=vd.quality_note or "",
                seq=self._next_seq())
            if not self.store.add(rec):
                # 竞态兜底：投递期间被别的记录填满 → 本条不入库
                self._rejected_count += 1
                self._warn_full_once()
                self._update_status_bar()
                return
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
                        "最高（或保证网络良好）后重新播放，再重新捕获。")
                self.store.update(rid, status="done", progress=100,
                                  file=item[2], error="",
                                  size=actual or rec.size)
            elif kind == "failed":
                self.store.update(rid, status="failed", error=item[2])
            elif kind == "cancelled":
                # 用户停止（管线确认）：以 stopped 收尾；
                # 若已被「取消所有排队」重置为待下载，则保持待下载
                cur = self.store.items[rid].status
                if cur in ("pending", "downloading", "decrypting"):
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
        limit = self._tier_label_of()
        if self.store.is_full():
            limit += f"（已满·未加载 {self._rejected_count} 条）"
        elif self._rejected_count:
            limit += f"（已加载 {self._rejected_count} 条前曾满档）"
        batch_txt = ""
        if getattr(self, "addon", None) is not None and self.addon.batch_mode:
            batch_txt = (f"   |   ⚡批量: "
                         f"{'拉取中' if self.addon._batch_active else '待重进页面'}"
                         f"(+{self.addon.batch_count})")
        # v2.0.19：状态栏显示当前来源筛选状态（有筛选时给出条目数与取消入口）
        flt = getattr(self, "_platform_filter", "")
        filter_txt = ""
        if flt:
            shown = sum(1 for r in self.store.items.values()
                        if (r.platform or "视频号") == flt)
            filter_txt = f"   |   🔍来源={flt}（{shown}/{total} 条）· 双击来源列可取消。"
        self.status_bar.config(
            text=f"  {engine_txt}   |   {proxy_txt}   |   {ca_txt}   |   "
                 f"{hook_txt}   |   列表: 共 {total} 条 / 上限 {limit}"
                 f" · 待下载 {waiting} · 完成 {done} · 失败 {failed}"
                 f"{batch_txt}{filter_txt}")
        # ★ v2.2.3：有筛选时显示醒目横幅（带「显示全部」按钮），否则隐藏
        #
        #   ★ 判据必须用 winfo_manager()，不能用 winfo_ismapped()：
        #     ismapped() 要求控件在屏幕上真正可见 —— 窗口最小化/被遮挡/
        #     或处于 withdraw 状态时它返回 0，即使 pack 过也是 0。
        #     用它做"要不要 pack"的判断会导致横幅永远不隐藏（实测踩到：
        #     点「显示全部」后记录恢复了但横幅还挂着）。
        try:
            shown_now = self.filter_bar.winfo_manager() == "pack"
            if flt:
                shown = sum(1 for r in self.store.items.values()
                            if (r.platform or "视频号") == flt)
                self.lbl_filter.config(
                    text=f"🔍 当前只显示「{flt}」（{shown}/{total} 条）"
                         f" — 其他来源的记录只是被隐藏，没有删除")
                if not shown_now:
                    self.filter_bar.pack(fill="x", side="bottom",
                                         before=self.status_bar)
            else:
                if shown_now:
                    self.filter_bar.pack_forget()
        except Exception:
            pass

    def _diagnose_no_traffic(self, now, a):
        """「零流量」主动诊断（v2.0.19）。

        触发条件：嗅探已启动 ≥60s，但**既无流活动、也无钩子数据、也无捕获**。
        含义：流量根本没经过代理（而不是"注入了但没抓到"）。

        为什么要分情况：这三种原因的解法完全不同，笼统说"抓不到"客户仍不会修。
          A. 微信在启动嗅探前已打开 → 微信进程启动时读过一次代理配置，
             之后不再读 → **必须完全退出微信再重开**
          B. 抓的不是视频号（如公众号文章） → 本工具只抓视频号
          C. 系统代理被锁/被改（企业版组策略、第三方代理软件）→ 需联系 IT

        只提示一次（_no_traffic_warned），避免每 60s 刷屏；一旦有流量就复位，
        下次再出现零流量可再次提示。
        """
        started = getattr(self, "_sniff_started_ts", 0) or 0
        if not started or now - started < 60:
            return
        has_traffic = bool(a.last_stream_ts or a.last_bridge_ts or a.capture_count)
        if has_traffic:
            self._no_traffic_warned = False
            return
        if getattr(self, "_no_traffic_warned", False):
            return
        self._no_traffic_warned = True

        # 补充判定：系统代理是否真的指向我们（读注册表，失败不影响提示）
        proxy_ok, proxy_now = self._check_system_proxy()
        lines = [
            f"⚠️ 已运行 {int(now - started)} 秒，但**没有任何流量经过代理**"
            f"（页面注入 0 次 / 流活动 无 / 捕获 0 个）。",
            "这不是证书问题，而是微信的请求没走代理。按顺序检查：",
            "",
            "① ★最常见：微信是在「启动嗅探」之前就打开的",
            "   → 请【完全退出微信】（托盘图标右键→退出，不是最小化）",
            "   → 再点「启动嗅探」，等看到「代理已监听」",
            "   → 然后重新打开微信、进视频号播放",
            "",
            "② 你打开的是「公众号文章」吗？",
            "   本工具只抓【视频号】（微信→发现→视频号）；",
            "   公众号文章里的视频抓不到（那是另一套域名/加密）。",
            "",
            "③ 公司电脑 / Win10 企业版：系统代理可能被 IT 组策略锁定",
            "   → 请看上面「系统代理」状态；显示异常请联系 IT 放开。",
            "   → 装了 Clash/VPN 等代理软件也会抢走系统代理，先退出它们。",
        ]
        if not proxy_ok:
            lines.append("")
            lines.append(f"★ 检测到系统代理当前为：{proxy_now or '(空)'}")
            lines.append("   应为 127.0.0.1:8899。若不对：点「停止嗅探」再"
                         "「启动嗅探」重设；仍不对多为安全软件/组策略拦截。")
        self.push_log("warn", "\n".join(lines))

    def _check_system_proxy(self):
        """读 Windows 系统代理设置，返回 (是否指向本机嗅探端口, 当前值)。

        只用于诊断提示，任何失败都返回 (True, '') —— 不因读不到就报警。
        """
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
            try:
                enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
                server, _ = winreg.QueryValueEx(key, "ProxyServer")
            finally:
                winreg.CloseKey(key)
            want = f"127.0.0.1:{config.PROXY_PORT}"
            cur = str(server or "")
            ok = bool(enable) and (want in cur.replace(" ", ""))
            return ok, cur if enable else "(未启用)"
        except Exception:
            return True, ""


    def _refresh_mp_button(self):
        """刷新「📄 公众号视频」按钮外观。

        授权了 → 正常；未授权 → 显示 🔒 但**仍可点**（点了给解锁引导，
        比禁用更利于客户理解与成交）。

        ★ v2.0.19 修复：必须**按公众号产品线**查询授权。
          原先用 _license_info()（优先返回视频号），导致客户同时买了
          视频号和公众号时，按钮仍显示 🔒（2026-10-17 实测）。
        """
        if getattr(self, "btn_mp", None) is None:
            return False
        try:
            has_mp = lic.is_mp_product(
                self._license_info(lic.PRODUCT_MP_ARTICLE))
        except Exception:
            has_mp = False
        try:
            self.btn_mp.config(
                text="📄 公众号视频" if has_mp else "📄 公众号视频 🔒",
                style="Mp.TButton" if has_mp else "MpLocked.TButton",
                state="normal")
        except Exception:
            pass
        return has_mp

    def _on_mp_article(self):
        """「📄 公众号视频」入口：未授权给引导，已授权开清单窗口。"""
        if not self._refresh_mp_button():
            if messagebox.askyesno(
                    "公众号视频（未授权）",
                    "「公众号文章视频下载」是**独立产品**，与视频号分开销售。\n\n"
                    "区别：\n"
                    "  · 视频号：微信「发现 → 视频号」里的视频（嗅探捕获）\n"
                    "  · 公众号：公众号文章正文里的视频（粘贴链接解析）\n"
                    "  两者是不同入口，授权码也分开：视频号码不能下公众号，\n"
                    "  公众号码也不能抓视频号。\n\n"
                    "如需使用本功能，请联系发布方购买「公众号视频版」授权码，\n"
                    "拿到后在「🔑 授权管理」粘贴激活即可（立即生效）。\n\n"
                    "要去「🔑 授权管理」查看当前授权吗？"):
                self._on_license_input()
            return
        self._open_mp_window()

    def _open_mp_window(self):
        """公众号文章解析窗口（v2.0.19）。

        ★ 设计变更（用户要求）：**不再有独立清单**。
          解析出来的视频统一进入主清单（与视频号共用一张表），
          靠「来源」列区分（🎬 视频号 / 📄 公众号）；双击来源列可筛选。
          本窗口只负责"粘链接 → 解析 → 加入主清单"。
        """
        from sphdl import mp_article as mpmod

        win = tk.Toplevel(self.root)
        win.title("公众号文章视频下载")
        win.transient(self.root)
        win.geometry("820x440")
        win.minsize(700, 400)

        pad = ttk.Frame(win, padding=14)
        pad.pack(fill="both", expand=True)

        ttk.Label(pad, text="① 粘贴公众号文章链接",
                  font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        ttk.Label(pad,
                  text="在微信里打开文章 → 右上角「…」→ 复制链接 → 粘到下面。\n"
                       "解析出的视频会直接加入主清单（列表里「来源」列显示 📄 公众号），"
                       "和视频号视频一起管理、排序、下载。",
                  foreground="#6b7280", wraplength=760,
                  justify="left").pack(anchor="w", pady=(2, 8))

        row = ttk.Frame(pad)
        row.pack(fill="x")
        url_var = tk.StringVar()
        ent = ttk.Entry(row, textvariable=url_var)
        ent.pack(side="left", fill="x", expand=True)
        btn_paste = ttk.Button(row, text="📋 粘贴", width=9,
                               command=lambda: _do_paste())
        btn_paste.pack(side="left", padx=(6, 0))
        self._tt(btn_paste, "从剪贴板粘贴链接（先在微信里复制文章链接）")
        btn_parse = ttk.Button(row, text="解析并加入清单", width=15,
                               command=lambda: _do_parse())
        btn_parse.pack(side="left", padx=(6, 0))
        self._tt(btn_parse,
                 "抓取文章页、解析出里面所有视频，并加入主清单。\n"
                 "同一文章重复解析不会重复添加（按视频标识去重）。\n"
                 "大文章可能需要 10-30 秒。")

        # ---- 解析结果预览（只读）----
        ttk.Label(pad, text="② 解析结果",
                  font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w",
                                                                pady=(12, 4))
        cols = ("idx", "vid", "quality")
        tv = ttk.Treeview(pad, columns=cols, show="headings", height=9)
        tv.heading("idx", text="序号")
        tv.heading("vid", text="视频标识")
        tv.heading("quality", text="可用清晰度")
        tv.column("idx", width=55, anchor="center")
        tv.column("vid", width=460, anchor="w")
        tv.column("quality", width=220, anchor="w")
        tv.pack(fill="both", expand=True, pady=(0, 6))
        tv.insert("", "end",
                  values=("—", "还没有解析，先粘贴链接并点「解析并加入清单」", "—"),
                  tags=("hint",))
        tv.tag_configure("hint", foreground="#9ca3af")

        # ---- 底部状态 + 跳转按钮 ----
        bot = ttk.Frame(pad)
        bot.pack(fill="x")
        status = ttk.Label(bot, text="等待解析…", foreground="#6b7280")
        status.pack(side="left")
        btn_view = ttk.Button(bot, text="在主清单中查看 ▼", width=16,
                              command=lambda: _goto_main("公众号"))
        btn_view.pack(side="right")
        self._tt(btn_view, "关闭本窗口，回到主清单并筛选出所有「公众号」来源的视频")

        # ★ v2.2.3：解析进度条（默认隐藏，解析时才出现）
        #
        #   客户实测反馈（2026-10-23）：
        #   「点『解析并加入清单』后没有视频出现，以为故障了」
        #   实际是 — 大文章 HTML 有 3.5MB，抓取+解析要 10-30 秒，
        #   而旧版只在窗口底部显示一行小灰字，容易被忽略 → 以为卡死。
        #
        #   三条可见反馈：
        #     ① 按钮文字变「⏳ 解析中…」（眼睛最常看的地方）
        #     ② 数据表出现「正在抓取…」占位行
        #     ③ 底部进度条 + 每秒更新的秒数
        #   ★ 必须放进已显示的 bot 框架：放进 pad 末尾会分不到空间（实测 1x1/mapped=0）
        prog = ttk.Progressbar(bot, mode="indeterminate", length=150)
        _watch = {"id": None, "n": 0}

        def _watch_start():
            """开始解析 → 三条可见反馈全部亮起。"""
            _watch["n"] = 0
            btn_parse.config(text="⏳ 解析中…", state="disabled")
            status.config(text="⏳ 正在抓取文章页…", foreground="#1d4ed8")
            # 数据表里放一行占位，让"有反应"这件事在结果区也看得见
            for i in tv.get_children():
                tv.delete(i)
            tv.insert("", "end", values=("…", "正在抓取文章并解析视频，请稍等…", "—"),
                      tags=("busy",))
            tv.tag_configure("busy", foreground="#2563eb")
            try:
                prog.pack(side="left", fill="x", expand=True, padx=(10, 10))
                prog.start(12)
            except Exception:
                pass

            def _tick():
                _watch["n"] += 1
                n = _watch["n"]
                try:
                    if n <= 30:
                        status.config(
                            text="⏳ 正在抓取文章页…已 %d 秒（大文章约 10-30 秒）" % n,
                            foreground="#1d4ed8")
                    else:
                        status.config(
                            text="⏳ 仍在抓取…已 %d 秒，网络较慢或文章很大，请再等等" % n,
                            foreground="#b45309")
                    _watch["id"] = self.root.after(1000, _tick)
                except Exception:
                    # 窗口已关闭 → 静默停止计时，别在控制台喷异常
                    _watch["id"] = None
            _tick()

        def _watch_stop():
            """解析结束（成功/失败）→ 全部复原。"""
            if _watch["id"]:
                try:
                    self.root.after_cancel(_watch["id"])
                except Exception:
                    pass
                _watch["id"] = None
            try:
                prog.stop()
                prog.pack_forget()
            except Exception:
                pass
            try:
                btn_parse.config(text="解析并加入清单", state="normal")
            except Exception:
                pass


        def _do_paste():
            try:
                s = self.root.clipboard_get()
                if s and s.strip():
                    url_var.set(s.strip())
                    status.config(text="已粘贴链接，点「解析并加入清单」")
            except Exception:
                status.config(text="剪贴板里没有文本")

        def _do_parse():
            u = url_var.get().strip()
            if not u:
                status.config(text="请先粘贴公众号文章链接")
                return
            if not mpmod.is_mp_article_url(u):
                status.config(text="✗ 这不是公众号文章链接（应形如 "
                                    "https://mp.weixin.qq.com/s/xxxxx）")
                return
            # ★ v2.2.3：三条可见反馈（按钮文字+表格占位+进度条秒数）
            _watch_start()

            def _work():
                try:
                    h = mpmod.fetch_article(u)
                    title = mpmod.article_title(h)
                    vids = mpmod.parse_article(h)
                except Exception as e:                   # noqa: BLE001
                    self.push_event(("mp_parse_done", None, None,
                                     f"✗ 解析失败：{type(e).__name__}: {e}", u))
                    return
                self.push_event(("mp_parse_done", vids, title, "", u))

            import threading
            threading.Thread(target=_work, daemon=True).start()

        def _on_parse_done(vids, title, err, article_url=""):
            _watch_stop()          # ★ 复原按钮文字 + 隐藏进度条 + 停计时
            if err:
                status.config(text=err, foreground="#b91c1c")
                for i in tv.get_children():
                    tv.delete(i)
                return
            if not vids:
                status.config(text="✗ 没解析到视频。可能原因：文章里没有视频、"
                                   "需要登录/付费、或链接已失效。",
                              foreground="#b91c1c")
                for i in tv.get_children():
                    tv.delete(i)
                return
            # 清空预览表
            for i in tv.get_children():
                tv.delete(i)
            added = 0
            multi = len(vids) > 1
            for i, v in enumerate(vids, 1):
                qmap = {lbl: u2 for _f, u2, lbl in v.renditions}
                labels = [lbl for _f, _u, lbl in v.renditions]
                best = labels[-1] if labels else ""
                tv.insert("", "end", values=(
                    i, v.vid, "、".join(labels) or "未知"))
                # ★ 写入主清单（platform=公众号）
                rec = Record(
                    id=f"mp{int(time.time()*1000)%10**9}{i}{uuid.uuid4().hex[:4]}",
                    title=(title or "公众号视频") + (f" #{i}" if multi else ""),
                    url=v.url, decode_key="", size=0,
                    source="公众号解析", created=time.strftime("%Y-%m-%d %H:%M:%S"),
                    status="waiting", platform="公众号",
                    video_id=v.vid, seq=self._next_seq(),
                    renditions=[{"url": u2, "label": lbl, "size": 0}
                                for _f, u2, lbl in v.renditions],
                    quality_note=("、".join(labels) or ""))
                # ★ v2.0.19：公众号视频不占用视频号的档位名额 —— 这一点已在
                #   RecordStore.is_full() / _trim_to_limit() 里统一处理
                #   （只统计 platform=="视频号" 的记录），此处直接入库即可。
                if self.store.add(rec):
                    self._add_row(rec)
                    added += 1
            # 若当前在筛选视图，重绘以保持一致
            if getattr(self, "_platform_filter", ""):
                self._render_filtered_tree()
            self._dirty = True
            self._force_save()
            self._update_status_bar()
            status.config(text=f"✅ 解析到 {len(vids)} 个视频，"
                               f"新加入主清单 {added} 条"
                               + ("（已在清单里的自动跳过）" if added < len(vids)
                                  else ""))
            self.push_log("ok", f"📄 公众号：{title[:30]} → 加入 {added} 个视频"
                                f"（来源标记为「公众号」）")

        def _goto_main(plat):
            try:
                win.destroy()
            except Exception:
                pass
            self._filter_by_platform(plat)
            try:
                self.root.deiconify()
                self.root.lift()
            except Exception:
                pass

        def _handle(item):
            kind = item[0] if item else ""
            if kind == "mp_parse_done":
                _on_parse_done(item[1], item[2], item[3],
                               item[4] if len(item) > 4 else "")
                return True
            return False

        self._mp_event_handler = _handle

        def _on_close():
            try:
                self._mp_event_handler = None
            except Exception:
                pass
            try:
                win.destroy()
            except Exception:
                pass
        win.protocol("WM_DELETE_WINDOW", _on_close)

        ent.focus_set()
        self.push_log("ok", "📄 公众号窗口已打开：粘贴文章链接 → 解析 → "
                            "视频会进入主清单（来源列标为「公众号」）")

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
                "——页面里没有捕获钩子（可能是在启动嗅探前就打开的，"
                "或钩子被页面刷新/回收）。\n"
                "处理：「完全关闭视频号页面」（或退出微信视频号后重进），"
                "让页面重新走网络加载、代理自动注入钩子；\n"
                "也可重启嗅探引擎后重进页面。仅刷新可能仍走缓存，"
                "务必先关闭再打开。")
        if not streaming or bridge_age < 90:
            self._hook_warned = False
        # ★ v2.0.19「零流量」主动诊断：启动嗅探 60s 后仍无任何流量经过代理。
        # 依据 2026-10-17 真实日志（用户 4 次重试、页面注入 0 次 / 流活动 无）：
        # 客户只会看到"软件没反应"，却不知道是流量压根没走代理。
        # 这里直接给出可操作的原因清单，减少客服往返。
        self._diagnose_no_traffic(now, a)
        # 周期性心跳日志（60s 一条）：引擎/钩子健康状态可追溯
        if now - self._last_hb_log >= 60:
            self._last_hb_log = now

            def _age(t):
                return "无" if not t else f"{int(now - t)}s前"

            batch_txt = ""
            if a.batch_mode:
                batch_txt = (f" | 批量 {_age(a.last_batch_ts)}"
                             f"·新增{a.batch_count}条")
            # ★ v2.0.18：批量「已停」且久无新捕获 → 明确提示可操作原因。
            # 这是客户最常遇到的"清单不动"场景：视频号窗口失焦 → 页面拿不到
            # 滚动授权 → 不再翻页。日志必须说清怎么办，不能让客户干等。
            stall_txt = ""
            if a.batch_mode and a.last_batch_ts and now - a.last_batch_ts > 60:
                idle = now - (a.last_capture_ts or a.last_batch_ts)
                if idle > 60:
                    stall_txt = (
                        f"\n⚠ 批量已停 {int(now - a.last_batch_ts)}s"
                        f" 且 {int(idle)}s 无新捕获 —— 若视频号列表还在翻但清单不增，"
                        "请【点一下视频号窗口】让它重新获得焦点（自动翻页需要焦点）；"
                        "仍不行则点「停止批量」再「开始批量」。")
            self.push_log(
                "info",
                f"💓 嗅探运行中 | 钩子数据 {_age(a.last_bridge_ts)} "
                f"(心跳×{a.hb_count} / 数据×{a.bridge_count}) | "
                f"流活动 {_age(a.last_stream_ts)} | "
                f"循环延迟 {a.loop_delay_ms}ms | "
                f"页面注入 {a.bridge_hooked_pages} 次 | 捕获 {a.capture_count} 个"
                f"{batch_txt}{stall_txt}")

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
        # 落盘节流（v2.0.17）：至尊 records.json 可达 13MB，每 300ms 全量
        # 重写会造成 I/O 抖动。改为最多每 SAVE_MIN_INTERVAL 秒一次；退出/上限
        # 变更/Done 等关键点仍会强制落盘（见 _force_save）。
        if self._dirty and (time.time() - self._last_save) >= self.SAVE_MIN_INTERVAL:
            self._force_save()
        self._hook_watchdog()
        self._update_status_bar()
        self.root.after(self.POLL_MS, self._poll)

    def _force_save(self):
        """立即落盘（原子写）。失败不抛异常——落盘失败不能影响界面。"""
        self._last_save = time.time()
        self._dirty = False
        self.store.save()

    # ---------------- 退出 ----------------
    def _on_close(self):
        if self.engine.running:
            if not messagebox.askyesno("退出确认",
                                       "嗅探引擎正在运行。\n停止嗅探（并恢复系统代理）并退出？"):
                return
            self.btn_engine.config(state="disabled", text="退出中…")
            self.engine.stop()
        # ★ v2.0.19：先停水印循环，避免窗口销毁后 tick 继续跑
        try:
            if getattr(self, "_wm_after_id", None):
                self.root.after_cancel(self._wm_after_id)
                self._wm_after_id = None
            if getattr(self, "_wm_win", None) is not None:
                self._wm_win.destroy()
                self._wm_win = None
        except Exception:
            pass
        self.cfg.save()
        self.store.save()   # 退出强制落盘（节流窗口内也要写）
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
