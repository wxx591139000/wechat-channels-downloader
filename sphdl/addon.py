"""
mitmproxy 嗅探插件。四种嗅探策略（并行生效，哪个先抓到用哪个）：

  ① JS 回调模式：向微信 web 视频号的 JS 注入钩子，
     前端播放视频时把 objectDesc POST 到 wxapp.tc.qq.com/res-downloader/wechat，
     我们拦截该请求拿到 url + decode_key。（ThinkerWen 方案，依赖注入成功）
  ② API 直嗅模式：直接扫描所有 *.qq.com 的 JSON 响应，递归查找
     object_desc/objectDesc 节点——无需注入，对微信版本变化更鲁棒。
  ③ JS 注入：钩子注入器（服务于 ①）。
  ④ bridge 钩子（2.7 方案）：往 channels.weixin.qq.com 的 HTML 页面注入
     WeixinJSBridge.invoke 包装器，把每次 bridge 调用的参数+回调结果
     POST 回代理本身（/__sphdl/bridge），request() 钩子截获解析。
     专治 PC 微信内置视频号：页面 HTML 走网络（可注入），但 objectDesc
     经原生 bridge IPC 返回（②看不见），必须靠 JS 钩子在页面内截获。
"""
import json
import queue as _queue
import re
import threading as _threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from mitmproxy import http
from mitmproxy import tls as _tls

from . import config
from . import shared
from .logger import logger
from .parser import (extract_from_object_desc, iter_object_descs,
                     iter_object_desc_entries, extract_comments)
from .util import mask_url, describe_capture

# ④ 注入到 HTML 的 JS：包装 WeixinJSBridge.invoke，
#    每次调用的参数与回调结果 POST 到本机代理（addon request() 截获）。
_BRIDGE_JS_TEMPLATE = """
<script>
(function () {
  if (window.__sphdl_hooked) { return; }
  window.__sphdl_hooked = true;
  function safeStr(v) {
    try { return JSON.stringify(v); } catch (e) { return String(v); }
  }
  function send(payload) {
    try {
      if (payload.length > 5000000) { return; }
      fetch("http://127.0.0.1:__PORT__/__sphdl/bridge", {
        method: "POST",
        mode: "no-cors",
        headers: { "Content-Type": "text/plain;charset=UTF-8" },
        body: payload
      });
    } catch (e) {}
  }
  function wrap() {
    var w = window;
    var bridge = w.WeixinJSBridge;
    if (!bridge || typeof bridge.invoke !== "function" || bridge.__sphdl_wrapped) { return; }
    bridge.__sphdl_wrapped = true;
    var orig = bridge.invoke;
    bridge.invoke = function (name, args, cb) {
      var a = Array.prototype.slice.call(arguments);
      if (typeof a[2] === "function") {
        var origCb = a[2];
        a[2] = function () {
          try {
            var result = arguments.length === 1 ? arguments[0] : Array.prototype.slice.call(arguments);
            var payload = JSON.stringify({
              url: w.location.href,
              cmdName: name,
              arguments: safeStr(args || {}),
              result: safeStr(result)
            });
            send(payload);
          } catch (e) {}
          return origCb.apply(this, arguments);
        };
      }
      return orig.apply(bridge, a);
    };
    send(JSON.stringify({ url: w.location.href, hb: 1 }));  // 握手：代理确认钩子存活
  }
  // 看门狗：微信可能重建 WeixinJSBridge 对象（页面重载/渲染进程回收后
  // 新 JS 上下文没有钩子 → 捕获静默停摆）。每 5s 检查一次并补包；
  // 钩子存活时每 5s 发一次心跳，代理端据此判断钩子页面是否仍存活。
  setInterval(function () {
    try {
      if (window.WeixinJSBridge && !window.WeixinJSBridge.__sphdl_wrapped) { wrap(); }
      else if (window.WeixinJSBridge && window.WeixinJSBridge.__sphdl_wrapped) {
        send(JSON.stringify({ url: window.location.href, hb: 1 }));
      }
    } catch (e) {}
  }, 5000);
  if (window.WeixinJSBridge) { wrap(); }
  else {
    var t = 0;
    var timer = setInterval(function () {
      t += 100;
      if (window.WeixinJSBridge) { clearInterval(timer); wrap(); }
      else if (t > 20000) { clearInterval(timer); }
    }, 100);
  }
})();

// ⚡ 批量拉取驱动（v2.0.11）：常驻页面，轮询代理批量状态。
// 只滚动"当前选中"的视频号窗口（代理按最近获得焦点的页面判定）；
// 即使被 Excel 等窗口遮挡也持续滚动，直到点「停止拉取」或拉到底。
(function () {
  if (window.__sphdl_batch_driver) { return; }
  window.__sphdl_batch_driver = true;
  var pageId = (Math.random() * 1e9).toString(36);
  var rounds = 0, stable = 0, maxH = 0, done = false, doneAt = 0;
  var pageActive = false;   // 本页最近是否获得过焦点（焦点事件跟踪）
  // 复位「滚动进度」但**不清 done**：用于"未授权滚动"时避免误判到底
  // （v2.0.18 修复：原来 resetState 连 done 一起清，配合失焦会永久停摆）
  function resetProgress() { rounds = 0; stable = 0; }
  function resetState() { rounds = 0; stable = 0; maxH = 0; done = false; doneAt = 0; }
  try {
    window.addEventListener("focus", function () { pageActive = true; });
    window.addEventListener("pointerdown", function () { pageActive = true; });
    window.addEventListener("blur", function () { pageActive = false; });
  } catch (e) {}
  function post(o) {
    try {
      fetch("http://127.0.0.1:__PORT__/__sphdl/batch", {
        method: "POST", mode: "no-cors", body: JSON.stringify(o)
      });
    } catch (e) {}
  }
  function scrollable() {
    var d = document.scrollingElement || document.documentElement;
    if (d && d.scrollHeight - d.scrollTop - d.clientHeight > 80) { return d; }
    var els = document.querySelectorAll('div,main,section,ul');
    var best = null, bs = 0;
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (el.scrollHeight > el.clientHeight + 150 && el.scrollHeight > bs) {
        bs = el.scrollHeight; best = el;
      }
    }
    return best || d;
  }
  function doScroll() {
    var el = scrollable();
    if (!el) { return {h:0,t:0,c:0}; }
    var isDoc = (el === document.scrollingElement || el === document.documentElement);
    if (isDoc) { window.scrollTo(0, el.scrollHeight); }
    else { el.scrollTop = el.scrollHeight; }
    try {
      if (el.dispatchEvent) {
        el.dispatchEvent(new WheelEvent("wheel", {deltaY: 800, bubbles: true, cancelable: true}));
      }
    } catch (e) {}
    return {h: el.scrollHeight || 0, t: el.scrollTop || 0, c: el.clientHeight || 0};
  }
  setInterval(function () {
    try {
      fetch("http://127.0.0.1:__PORT__/__sphdl/batch?status=1&id=" + pageId
            + "&f=" + (pageActive ? "1" : "0") + "&h=" + maxH, {
        method: "GET", mode: "cors", cache: "no-store"
      }).then(function (r) { return r.text(); }).then(function (t) {
        // 响应格式：'1' = 授权滚动；'0' = 未授权。
        // '1' 开头带 'r' 表示代理侧捕获数有增长（列表还有内容，要求续跑）。
        var on = !!(t && t.charAt(0) === "1");
        var grew = !!(t && t.indexOf("r") > 0);
        if (!on) {
          // 未授权滚动（本页失焦 / 非被选中页）——v2.0.18 修复：
          // ① 只复位滚动进度，**不清 done**（避免"到底了"被失焦抹掉）
          // ② 绝不把它当成"已到底"的证据（原来会因此永久停摆）
          resetProgress();
          return;
        }
        if (done) {
          // 已判定"到底"。若代理侧又有新捕获（grew），说明没到底 → 自动续跑；
          // 否则等 done 宽限期过后也自动再试一轮，绝不永久停摆。
          var waited = Date.now() - doneAt;
          if (grew || waited > 20000) {
            done = false; doneAt = 0; stable = 0; rounds = 0;
          } else {
            return;
          }
        }
        rounds++;
        var r = doScroll();
        if (r.h > maxH + 10) { maxH = r.h; stable = 0; }
        else if (r.h > 0 && r.t >= r.h - r.c - 60) { stable++; }
        else { stable = 0; }
        // 到底判定放宽：50s 无新增内容（原 30s，虚拟列表懒加载易误判）
        if (rounds > 240 || stable >= 20) {
          done = true; doneAt = Date.now();
          post({batch_done: 1, rounds: rounds, maxHeight: maxH});
        } else {
          post({batch: 1, rounds: rounds});
        }
      }).catch(function () {});
    } catch (e) {}
  }, 2500);
})();
</script>
"""

# 自然捕获（v2.0.7）：列表数据不再整页入库，播放到哪条才捕获哪条。
#  - 列表 payload 的 objectDesc 全部缓存为"候选"（不显示、不下载）
#  - 播放器请求某条视频的流/封面（stodownload URL 含同 encfilekey）→ 命中，
#    捕获"当前播放 + 紧随其后的 LOOKAHEAD 条"
#  - 新列表数据的前 ANCHOR 条作为"当前浏览锚点"先行入列（可见反馈）
_EFK_RE = re.compile(r"encfilekey=([A-Za-z0-9_\-]+)")


def _extract_encfilekey(url: str) -> str:
    m = _EFK_RE.search(url or "")
    return m.group(1) if m else ""


def _scan_stat_hits(obj, limit: int = 15) -> list:
    """扫出 payload 里像统计数的字段路径（诊断用，容错、不抛异常）。

    复用 parser.stat_schema_report 的判据（键名含 like/forward/share/
    fav/comment/count/num/stat/praise/digg/rec/thumb/view/play/collect）。
    """
    try:
        from .parser import stat_schema_report
        return stat_schema_report(obj, limit=limit)
    except Exception:
        return []


class SphAddon:
    # ★ v2.2.4：只拦截微信域名，其余流量**原样透传**（不 MITM）
    #
    #   客户报障（2026-10-23）：
    #     「启动嗅探后，优酷播不了，视频号也提示
    #      『暂无法观看此视频，可尝试切换网络或检查是否配置了代理』」
    #     关闭嗅探后两者都恢复正常 → 确认是代理的问题。
    #
    #   根因两条叠加：
    #     ① mitmproxy 拦截**全系统**流量。视频类大流量走 MITM 后
    #        握手/流式播放容易中断（优酷、视频号都是重视频站点）。
    #     ② 本机 IPv6 不通，但微信域名有 AAAA 记录，
    #        mitmproxy 连 IPv6 卡死（直连有 Happy Eyeballs 会自动回退）。
    #
    #   修法：
    #     · 本类新增 tls_clienthello：非微信域名 → ignore_connection（裸 TCP 隧道，
    #       证书透传，等同没装代理）→ 优酷/浏览器/其他 App 完全不受影响。
    #     · `sphdl/netfix.py`：IPv6 不通时让 getaddrinfo 只回 IPv4。
    #
    #   ★ 白名单只放**本工具真的要抓**的域名，别图省事写 "qq.com" ——
    #     那会把腾讯视频(v.qq.com)也拦进来，同样会播不了。
    INTERCEPT_HOST_PATTERNS = (
        r"(^|\.)weixin\.qq\.com$",        # mp./channels./open.weixin.qq.com
        r"(^|\.)wx\.qq\.com$",            # res.wx.qq.com 等
        r"(^|\.)qpic\.cn$",               # mmbiz.qpic.cn / mpvideo.qpic.cn
        r"(^|\.)weixin\.com$",
        r"(^|\.)weixinbridge\.com$",
        r"(^|\.)tc\.qq\.com$",            # wxapp.tc.qq.com（钩子回传）
        r"^finder\.video\.qq\.com$",      # 视频号视频 CDN
        r"(^|\.)wxqcloud\.qq\.com$",
        r"(^|\.)qlogo\.cn$",              # 头像
        # ★★★ v2.2.15：整个 qq.com 都拦（评论等接口散落在各个子域）
        #
        #   客户反馈（2026-10-23）：「有些视频我看有评论，但工具里显示没拉到」
        #   根因：我们的拦截白名单只有 9 条，而视频号的**评论接口**
        #   散落在 wxa.wxs.qq.com / mmfinder.qq.com / finder.qq.com /
        #   wxis.qq.com / wss.qq.com ... 这些都不在白名单里 →
        #   tls_clienthello 直接把连接**裸透传**，一个字节都看不到。
        #   评论区能显示多少，取决于评论接口恰好走没走白名单 → 时好时坏。
        r"(^|\.)qq\.com$",
        r"(^|\.)gtimg\.cn$",              # 微信图片 CDN（体量小）
        # ★★★ v2.7.6：小程序（短剧）相关域名 —— 实测客户播放「绿泡泡短剧」时
        #   日志出现 `33wozf743525a.vodpreview.wxamedia.com` 但被**透传**，
        #   一个字节都看不到。wxamedia 是小程序视频 CDN（wxa = 微信小程序）。
        r"(^|\.)wxamedia\.com$",         # 小程序视频 CDN（含 vodpreview）
        r"(^|\.)servicewechat\.com$",    # 小程序 API 网关
        r"(^|\.)wxs\.qq\.com$",          # 小程序接口（部分场景）
    )

    # ★ v2.2.15：这些重型媒体域名**不拦**（MITM 大流量视频会卡/断流）
    #   注：放 qq.com 之后判定，优先级更高。
    PASSTHROUGH_HOST_PATTERNS = (
        r"(^|\.)v\.qq\.com$",             # 腾讯视频（整站）
        # ★ 只排除腾讯视频 CDN 的 vv 子域，**不能写 video.qq.com 通配** ——
        #   那会把 finder.video.qq.com（视频号视频 CDN）一起透传掉，
        #   视频号就抓不到了（v2.2.15 自查时抓到的问题）。
        r"(^|\.)vv\.video\.qq\.com$",
        r"(^|\.)live\.qq\.com$",
        r"(^|\.)now\.qq\.com$",
    )

    def __init__(self, pipeline, on_capture=None, auto_download: bool = True,
                 allow_images: bool = False):
        """auto_download=False 时（GUI 模式）：只捕获进列表，
        不自动提交下载队列，由用户点选后再下载。
        allow_images=False 时（v2.0.4「🖼 下载图片」开关关闭；
        **v2.0.19 起为默认值**）：图片不捕获、不显示、不下载——直接跳过
        （不占去重名额，重新开启后同一图片可再次被捕获）。"""
        self._intercept_re = [re.compile(p, re.I)
                              for p in self.INTERCEPT_HOST_PATTERNS]
        self._passthrough_re = [re.compile(p, re.I)
                                for p in self.PASSTHROUGH_HOST_PATTERNS]
        # 透传开关（诊断用：设为 False 可恢复"拦截全部"的旧行为）
        self.passthrough_others = True
        self._passthrough_logged = set()
        self.pipeline = pipeline
        self.on_capture = on_capture  # 可选回调 fn(vd, source)（GUI 用于更新列表）
        self.auto_download = auto_download
        self.allow_images = allow_images
        # 可选 fn(vd)->bool：GUI 跨会话去重（records.json 已有该视频/链接则 True）。
        # 在自动下载提交前拦截，避免"列表去重跳过了、文件却重复下载"（v2.0.5 修复）
        self.duplicate_check = None
        # ★ v2.2.5：自动下载的授权门禁回调（GUI 传 self._has_download_license）。
        #   None = 不做校验（命令行/测试场景）；GUI 模式必须设置。
        self.license_check = None
        # ★ v2.2.11：bridge 全量诊断（定位评论等非视频数据走哪个 cmdName）
        self._bridge_diag = False
        self._bridge_diag_dir = None
        self._bridge_diag_seen = {}
        # ★ v2.2.12：评论抓取
        #   comments_enabled：总开关（GUI 可关，默认开）
        #   on_comments：GUI 回调 fn(video_key, comments, total) 把评论写进清单
        #   _cur_video_key：当前正在播放的视频标识（评论挂它身上）
        self.comments_enabled = True
        self.on_comments = None
        # ★ v2.2.16：由 GUI 注入 —— 给定一组候选 ID，返回该视频的记录键
        self.resolve_video_by_ids = None
        self._cur_video_key = ""
        self._comment_seen = {}       # video_key -> set(cid 或 content)，去重
        # ★ v2.2.16：全局 cid → video_key 归属表
        #   实测事故：72 条评论串到 3 个不同视频上（同一 cid 出现在多条记录）。
        #   根因：评论挂在「最后捕获的视频」，而不是 payload 自己所属的视频。
        #   兜底：一个 cid 只能属于一个视频，已归属的绝不重复挂到别处。
        self._cid_owner = {}
        # ★ v2.2.23：静默失败诊断计数器（"抓不到"看门狗用）
        self.stat_channels_hits = 0      # 微信页面请求数
        self.stat_tls_fail = 0           # 微信域名连接/TLS 失败数
        self.stat_captured = 0           # 本次会话捕获到的视频数
        self.stat_comment_payloads = 0   # 收到的评论 payload 数
        self.stat_last_hit_ts = 0.0      # 最近一次微信页面请求时间
        self.seen = set()          # 去重: (url, decode_key)
        self._dedup_logged = set()  # 已提示过的重复视频 key（同一视频只提示一次，防刷屏）
        self._multi_payload_logged = False  # 多视频 payload 诊断提示只打一次
        # ---- 自然捕获（v2.0.7）----
        self.natural_capture = True   # GUI「🎯 自然捕获」开关：False = 整页列表捕获
        self.ANCHOR = 2               # 新列表数据前几条先入列（当前浏览锚点）
        self.LOOKAHEAD = 2            # 播放命中后，紧随其后的几条一起捕获
        self.PENDING_CAP = 200        # 候选缓存上限
        # ★ v2.6.6：长视频（直播回放）整页全抓
        #   背景：博主的「直播回放」列表一个 payload 带 8 条，每条都有完整
        #   下载地址（file_size 2~3GB、video_play_len 1.8~7 小时）。
        #   但老逻辑只抓前 2 条 → 245 条回放要滑 122 次才抓完。
        #   现在：同一 payload 里**时长 ≥ 10 分钟**的全部入列（最多 60 条/页），
        #   短视频仍只抓 2 条 —— 避免推荐流把无关视频灌进列表。
        self.LONG_VIDEO_SEC = 600     # ≥10 分钟算长视频（回放/讲座）
        self.LONG_PER_PAGE_CAP = 60   # 单页最多补抓多少条长视频
        self.PENDING_EXPIRE = 1800    # 候选缓存过期（秒）
        self._pending = []            # [{efk, key, vd, ts, source}] 按列表顺序
        self._pending_lock = _threading.Lock()
        # ---- ⚡ 批量拉取（v2.0.8）----
        self.batch_mode = False       # GUI「⚡ 批量拉取」开关
        self.scroll_height = 0        # ★ v2.7.4 页面上报的 scrollHeight
        self.batch_done_at = 0.0      # ★ v2.7.5 JS 判定"到底"的时间戳
        self.batch_count = 0          # 批量期间新增捕获数
        self.rejected_count = 0       # 列表满档被 GUI 拒收（未加载）的视频数（v2.0.17）
        self._batch_active = False    # 页面滚动驱动是否在跑
        self._batch_seen_count = 0     # 上次下发续跑信号时的捕获计数（v2.0.18）
        self.last_batch_ts = 0.0      # 最近一次批量心跳
        self._batch_page = None       # 当前选中的视频号页面 id（最近获得焦点者）
        self.injected = False      # JS 是否已注入
        self.bridge_hooked_pages = 0  # ④ 已注入的 HTML 页面数
        # ---- 钩子存活跟踪（防"捕获静默停摆"：页面重载/回收后钩子丢失，
        #      以前无任何日志，用户只能靠重启引擎碰运气恢复）----
        self.last_bridge_ts = 0.0  # 最近一次收到任何 bridge POST（含心跳）
        self.last_capture_ts = 0.0  # 最近一次成功捕获视频
        self.last_stream_ts = 0.0  # 最近一次视频流（206）经过代理
        self.bridge_count = 0      # 非心跳 bridge 数据条数
        self.hb_count = 0          # 心跳条数
        self.capture_count = 0     # 累计捕获数（含去重跳过前的命中）
        # ---- 解析卸载（v1.2.5）：request() 在事件循环上只做 O(1) 的事，
        #      json.loads + 递归扫描 + 字符串二次解码搬进独立工作线程
        #      （sphdl-parse）——无论 payload 多大、循环多忙，捕获链路
        #      都不可能再被本工具自己的代码堵死（24h+ 连续播放结构保证）
        self.loop_delay_ms = 0     # 事件循环延迟（引擎每 5s 自测，供心跳日志）
        self._parse_q = _queue.Queue()
        self._parse_worker = _threading.Thread(target=self._parse_worker_loop,
                                               name="sphdl-parse", daemon=True)
        self._parse_worker.start()
        self._seen_lock = _threading.Lock()  # seen 去重集：request 解析（worker 线程）
        #                                    与 response API 直嗅（事件循环线程）
        #                                    都会调 _handle，加锁防并发竞态
        self.media_regex = re.compile(r"get\s+media\(\)\{", re.MULTILINE)
        self.comment_regex = re.compile(
            r"async\s+finderGetCommentDetail\((\w+)\)\s*\{return(.*?)\s*\}\s*async",
            re.MULTILINE | re.DOTALL,
        )
        self.bridge_js = _BRIDGE_JS_TEMPLATE.replace("__PORT__", str(config.PROXY_PORT))
        self._bridge_port = config.PROXY_PORT  # 注入 JS 实际使用的端口（可被 set_bridge_port 覆盖）
        logger.ok("嗅探插件已就绪")

    def set_bridge_port(self, port: int):
        """引擎以非默认端口启动时重建注入 JS（否则钩子仍回传 8899，
        bridge 捕获会静默失效——2026-08-26 审查发现）。"""
        try:
            port = int(port)
        except (TypeError, ValueError):
            return
        if port != self._bridge_port:
            self.bridge_js = _BRIDGE_JS_TEMPLATE.replace("__PORT__", str(port))
            self._bridge_port = port
            logger.debug(f"bridge 钩子端口已更新为 {port}")

    def _dump_bridge_diag(self, body: bytes):
        """把一条 bridge 调用落盘（仅诊断模式）。

        输出：diag_bridge/bridge_calls.jsonl，每行一个调用，
        含 cmdName / arguments / result 的头部片段。
        同时把 cmdName 种类汇总打到日志（每种只打一次），
        跑一次就能看出「评论」是哪个 cmdName。
        """
        try:
            obj = json.loads(body.decode("utf-8", "replace"))
        except Exception:
            return
        if not isinstance(obj, dict):
            return
        cmd = obj.get("cmdName") or "(无 cmdName)"
        args = obj.get("arguments")
        res = obj.get("result")
        try:
            if self._bridge_diag_dir is None:
                base = Path(config.BASE_DIR) if hasattr(config, "BASE_DIR") else Path(".")
                d = base / "diag_bridge"
                d.mkdir(parents=True, exist_ok=True)
                self._bridge_diag_dir = d
            rec = {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "cmd": cmd,
                "args_len": len(str(args)),
                "result_len": len(str(res)),
                "args_head": str(args)[:400],
                "result_head": str(res)[:1200],
                # ★ v2.5.3：扫描**完整请求体**里所有像统计数的字段。
                #   背景（2026-10-27）：客户报「点赞列全是「—」」，
                #   实测 objectDesc 里根本没有点赞字段（只有 video_play_len
                #   这类技术字段）→ 说明点赞数来自**另一个接口**。
                #   有了这个字段，扫一遍 bridge_calls.jsonl 就能定位是哪个
                #   cmdName 携带点赞数，不用再猜。
                "stat_hits": _scan_stat_hits(obj),
            }
            with (self._bridge_diag_dir / "bridge_calls.jsonl").open(
                    "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass
        # 每种 cmdName 只在日志里报一次，避免刷屏
        n = self._bridge_diag_seen.get(cmd, 0)
        self._bridge_diag_seen[cmd] = n + 1
        if n == 0:
            try:
                logger.info(
                    f"🔬 bridge诊断：新命令 {cmd} "
                    f"(args={len(str(args))}B result={len(str(res))}B)")
            except Exception:
                pass
        # ★ v2.5.3：只要某条命令带疑似统计字段，立刻打一条显眼日志
        #   （客户报「点赞列全是—」，需要定位点赞数到底来自哪个接口）
        try:
            _hits = _scan_stat_hits(obj, limit=6)
            if _hits:
                seen = getattr(self, "_stat_cmd_logged", None)
                if seen is None:
                    self._stat_cmd_logged = set()
                    seen = self._stat_cmd_logged
                if cmd not in seen:
                    seen.add(cmd)
                    logger.info(
                        f"📊 【找到疑似统计字段】cmdName={cmd} → "
                        + " ｜ ".join(_hits))
        except Exception:
            pass

    def _diag_on(self) -> bool:
        """诊断开关是否开启（★动态读取，改文件即时生效，不用重启）。

        v2.6.5：原来 `_bridge_diag` 在 GUI 初始化时读一次，
        客户每次都要重启程序才能开/关诊断 —— 实测太折腾。
        这里按 3 秒 TTL 重新读开关文件，兼顾即时性与开销。
        """
        now = time.time()
        cached = getattr(self, "_diag_cache", None)
        if cached and now - cached[0] < 3.0:
            return cached[1]
        try:
            base = Path(config.BASE_DIR) if hasattr(config, "BASE_DIR") else Path(".")
            on = (base / "开启原始数据记录.txt").exists()
        except Exception:
            on = False
        self._diag_cache = (now, on)
        return on

    def _dump_full_body(self, body: bytes):
        """把含 objectDesc 的完整 bridge body 存一份（仅诊断模式，v2.5.4）。

        目的：看清 objectDesc **之外**的兄弟字段里有没有点赞/转发数。

        落盘：diag_full_body/<时间戳>_<字节数>.json（最多 20 个，超了不写）
        日志：每条打一行「顶层字段 + 疑似统计字段」，一眼定位。
        """
        try:
            if len(body) > 4 * 1024 * 1024:      # 超大 body 跳过
                return
            base = Path(config.BASE_DIR) if hasattr(config, "BASE_DIR") else Path(".")
            d = base / "diag_full_body"
            if not d.exists():
                d.mkdir(parents=True, exist_ok=True)
            if len(list(d.glob("*.json"))) >= 20:
                return
            # 先看结构（诊断要的是字段名，不是内容）
            try:
                obj = json.loads(body.decode("utf-8", "replace"))
            except Exception:
                obj = None
            keys = list(obj.keys())[:12] if isinstance(obj, dict) else []
            hits = _scan_stat_hits(obj, limit=20) if isinstance(obj, dict) else []
            fn = d / f"{time.strftime('%H%M%S')}_{len(body)}.json"
            fn.write_bytes(body)
            if not getattr(self, "_full_body_logged", False):
                self._full_body_logged = True
                logger.info(
                    f"🧾 完整body已存 {fn.name}｜顶层字段={keys}")
                if hits:
                    logger.info("📊 完整body里的疑似统计字段："
                                + " ｜ ".join(hits[:12]))
                else:
                    logger.info("📊 完整body里也没扫到像统计数的字段"
                                "（点赞可能来自别的接口，需再抓）")
        except Exception:
            pass

    # ---------- ⓿ TLS 握手前：非微信域名一律裸透传（v2.2.4）----------
    def tls_clienthello(self, data: _tls.ClientHelloData):
        """在 MITM 接管 TLS 之前决定「拦还是透」。

        ★ 为什么必须有（2026-10-23 客户报障）：
          启动嗅探后优酷播不了、视频号提示「检查是否配置了代理」，
          关掉嗅探就都好了 —— 是 mitmproxy 把**全系统**流量都 MITM 了。
          视频类大流量走 MITM 很容易中断（握手/分片/长连接都可能踩坑）。

        做法：只有白名单里的微信域名才 MITM；
        其余（优酷、B站、浏览器、其他 App）设 `ignore_connection = True`
        → mitmproxy 退化成裸 TCP 隧道，证书不替换、不解析内容，
        对客户端来说「等于没装代理」。

        ★ 这个钩子只在 CONNECT/TLS 阶段触发，O(1) 正则匹配，不阻塞事件循环。
        """
        if not getattr(self, "passthrough_others", True):
            return
        host = (getattr(data, "client_hello", None)
                and getattr(data.client_hello, "sni", None)) or ""
        host = (host or "").strip().lower().rstrip(".")
        if not host:
            # 无 SNI（如直接用 IP 连）→ 不拦，避免把未知流量弄坏
            try:
                data.ignore_connection = True
            except Exception:
                pass
            return
        # ① 重型媒体一律透传（腾讯视频等，MITM 会卡）
        if any(rx.search(host) for rx in self._passthrough_re):
            try:
                data.ignore_connection = True
            except Exception:
                pass
            return
        # ② 微信生态域名 → 正常 MITM，继续嗅探
        if any(rx.search(host) for rx in self._intercept_re):
            return
        try:
            data.ignore_connection = True
        except Exception:
            pass
        # 每个域名只记一次，避免刷屏
        if host not in self._passthrough_logged and len(self._passthrough_logged) < 200:
            self._passthrough_logged.add(host)
            try:
                logger.debug(f"↪ 透传（不拦截）{host}")
            except Exception:
                pass

    # ---------- ① JS 回调通道 + ④ bridge 钩子回传 ----------
    def request(self, flow: http.HTTPFlow):
        """运行在 mitmproxy asyncio 事件循环上 —— 只做 O(1) 的事，绝不阻塞！
        重解析（json.loads + 递归扫描 + 字符串二次解码）卸载到独立工作线程
        sphdl-parse（v1.2.5）：钩子 = 字节预检 + 回 OK + 入队，实测 5MB
        payload 钩子耗时 ~4ms。无论 payload 多大、循环多忙，捕获链路
        都不可能再被本工具自己的代码堵死（24h 连续播放需求）。"""
        req = flow.request
        # ★ v2.2.23：统计微信域名请求（供"抓不到"看门狗判断页面是否在活动）
        try:
            _h = (req.pretty_host or "").lower()
            if ("weixin.qq.com" in _h or "qq.com" in _h
                    or "weixinbridge.com" in _h):
                self.stat_channels_hits += 1
                self.stat_last_hit_ts = time.time()
        except Exception:
            pass
        # 顺带捕获微信 webview 的 UA：CDN 对含 MicroMessenger 的 UA 限速更宽，
        # 下载器复用（shared 同进程共享，无需回调）。
        if "wxhls" in req.host or req.host.endswith("wxapp.tc.qq.com") or "live" in req.host:
            ua = (req.headers.get("User-Agent") or "").strip()
            if "micromessenger" in ua.lower():
                shared.set_wechat_ua(ua)
        logger.debug(f"📡 → {req.method} {req.host}{mask_url(req.path)}")
        is_js_callback = req.host.endswith(config.CALLBACK_HOST) and config.CALLBACK_PATH in req.path
        is_bridge = config.BRIDGE_CALLBACK_PATH in req.path
        is_batch = config.BATCH_CALLBACK_PATH in req.path
        if not (is_js_callback or is_bridge or is_batch):
            return
        # ⚡ 批量拉取心跳/完成回传（页面自动滚动驱动）
        if is_batch:
            if req.method == "OPTIONS":
                flow.response = http.Response.make(
                    204, b"", {
                        "Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                        "Access-Control-Allow-Headers": "*",
                    })
                return
            if req.method == "GET":
                # 状态轮询：页面驱动每 2.5s 请求一次，带 ?id=页ID&f=1|0
                #  f=1（本页有焦点）→ 设为"当前选中页"；只有选中页返回 1
                q = parse_qs(urlparse(req.path).query)
                pid = (q.get("id") or [""])[0]
                focused = (q.get("f") or [""])[0] == "1"
                # ★ v2.7.4：记录页面上报的 scrollHeight（h 参数）。
                #   判「到底」用它比「有没有新捕获」可靠得多：
                #   页面还在变长 = 还在加载；高度不再增长 = 真到底了。
                try:
                    _h = (q.get("h") or ["0"])[0]
                    self.scroll_height = int(_h)
                except Exception:
                    pass
                if self.batch_mode:
                    if focused and pid:
                        self._batch_page = pid
                    chosen = bool(self._batch_page) and pid == self._batch_page
                else:
                    self._batch_page = None
                    chosen = False
                # v2.0.18：把「上次查询之后是否有新捕获」作为续跑信号下发。
                # 页面据此判断"列表还有内容"→ 即使已判到底也自动续跑；
                # 同时把 scrollHeight 上报回来用于诊断（h 参数仅记录，不参与判定）。
                grew = False
                if chosen:
                    grew = self.capture_count > self._batch_seen_count
                    self._batch_seen_count = self.capture_count
                payload = ("1" if chosen else "0") + ("r" if grew else "")
                flow.response = http.Response.make(
                    200, payload.encode(),
                    {"Content-Type": "text/plain",
                     "Access-Control-Allow-Origin": "*",
                     "Cache-Control": "no-store"})
                return
            body = req.content or b""
            self.last_batch_ts = time.time()
            if b"batch_done" in body:
                # ★ v2.7.5：记下 JS 判"到底"的时间。GUI 的滚轮兜底看到它
                #   就立刻停手 —— 否则 JS 已判完，兜底还会白滚 11 次（实测）。
                self.batch_done_at = time.time()
                if self._batch_active:
                    self._batch_active = False
                    logger.ok(f"⚡ 批量拉取完成：已滚动到列表末尾"
                              f"（本次新增 {self.batch_count} 条）")
            flow.response = http.Response.make(
                200, b"OK", {"Content-Type": "text/plain"})
            return
        source = "bridge钩子" if is_bridge else "JS回调"
        body = req.content or b""
        if is_bridge:
            # 任何 bridge POST（含心跳）都证明钩子页面存活 → 更新存活时间戳。
            # 心跳：直接回 OK，不解析、不落日志（每 5s 一条，解析会白白占事件循环）。
            if b'"hb"' in body:
                self._touch_bridge(hb=True)
                flow.response = http.Response.make(
                    200, b"OK", {"Content-Type": "text/plain"})
                return
            self._touch_bridge(hb=False)
            # 字节预检（C 级快扫，v1.2.1）：body 里根本没有 objectDesc 字样时
            # 跳过解析入队。视频号页面每秒会 POST 多条无 objectDesc 的
            # bridge 调用（log/心跳/finderH5ExtTransfer 等），零漏报
            # （解析器只按这两个字面键搜索，凡能捕获的 body 必含字面键）。
            if b"object_desc" not in body and b"objectDesc" not in body:
                # ★ v2.2.12：先试评论提取（客户需求）
                #
                #   评论数据不含 objectDesc，会被下面这条预检丢弃。
                #   所以在丢弃前先跑一次评论提取 —— 提取器是纯结构识别
                #   （不看 cmdName），找到评论就挂到「当前播放的视频」上。
                #
                #   ★ 为什么在事件循环里做：
                #     评论 body 一般几 KB~几十 KB，extract_comments 是
                #     一次线性遍历，比视频 objectDesc 的小得多。
                #     但为稳妥仍先做字节预检（必须有昵称/内容类键之一），
                #     不含才跳过，避免每条 bridge 都全量 json.loads。
                if (b"nick" in body or b"content" in body or b"comment" in body) \
                        and self.comments_enabled:
                    self._parse_q.put((body, "__comment__"))
                # ★ v2.2.11：诊断模式下记录「非视频」的 bridge 调用
                if self._bridge_diag or self._diag_on():
                    self._dump_bridge_diag(body)
                flow.response = http.Response.make(
                    200, b"OK", {"Content-Type": "text/plain"})
                return
        # ★ v2.5.4：诊断模式下把**含 objectDesc 的完整 body** 也存一份。
        #   为什么（2026-10-27 实测结论）：
        #     客户要的点赞数**不在 objectDesc 里**（objectDesc 只有 12 个字段：
        #     description/media/media_type/location/ext_reading/topic/
        #     mentioned_user/feed_location/mentioned_musics/short_title/
        #     finder_newlife_desc/mod_feed_info —— 没有一个统计数）。
        #     所以点赞必然在**同一响应体的兄弟字段**里，
        #     必须存完整 body 才能看到（旧的 bridge 诊断只记录"不含 objectDesc"
        #     的调用，正好把这条跳过了）。
        # ★ v2.6.5：诊断范围扩大
        #   原来只存「含 objectDesc」的 body，但**直播回放列表不含 objectDesc**
        #   （它是 pc_finderinit + scope=finderLive，23KB），正好被跳过。
        #   现在：含 objectDesc 的存，**或者** body > 8KB 的也存（大响应基本都是数据）。
        if (self._bridge_diag or self._diag_on()) and is_bridge:
            try:
                if len(body) > 8000 or b"object_desc" in body or b"objectDesc" in body:
                    self._dump_full_body(body)
            except Exception:
                pass
        # 先回 OK（微信侧不受任何延迟影响），再把 body 交给工作线程解析。
        # 钩子全程 O(1)：预检 + 回包 + 入队，无 json.loads、无递归扫描。
        flow.response = http.Response.make(200, b"OK", {"Content-Type": "text/plain"})
        self._parse_q.put((body, source))

    def _handle_comment_payload(self, body: bytes):
        """处理评论 payload：解析 → 去重 → 回调 GUI 写进清单（v2.2.12）。

        ★ 挂在「当前正在播放的视频」上：
          视频号里翻评论必然是在看某条视频，所以评论 payload 到达时，
          最近一次捕获的视频就是它的宿主（`_cur_video_key`）。

        ★ 去重：同一个视频反复翻评论会重复收到同一批。
          按 (cid 或 content) 去重，只把新评论交给 GUI 追加。
        """
        if not self.comments_enabled:
            return
        try:
            obj = json.loads(body.decode("utf-8", "replace"))
        except Exception:
            return
        try:
            comments, total = extract_comments(obj)
        except Exception:
            return
        if not comments:
            return
        # ★ v2.2.16：先尝试从评论 payload 里认出「这条评论属于哪个视频」
        #
        #   视频号的评论接口会在 payload 里带上对象的 nonce/objectId，
        #   用它匹配记录比「最后捕获的视频」可靠得多。
        #   找不到才退回 _cur_video_key（老行为）。
        key = ""
        try:
            ids = self._collect_video_ids(obj)
            if ids and self.resolve_video_by_ids is not None:
                key = self.resolve_video_by_ids(ids) or ""
        except Exception:
            key = ""
        if not key:
            key = self._cur_video_key
        if not key:
            # 还没捕获过视频（比如先翻评论后播视频）→ 先放着不挂
            return
        seen = self._comment_seen.setdefault(key, set())
        fresh = []
        for c in comments:
            ident = c.get("cid") or (c.get("nick", "") + "\x00" + c.get("content", ""))
            # ★ 兜底防串：这个 cid 已经归属别的视频 → 跳过
            owner = self._cid_owner.get(ident)
            if owner is not None and owner != key:
                continue
            if ident in seen:
                continue
            seen.add(ident)
            self._cid_owner[ident] = key
            fresh.append(c)
        if not fresh:
            return
        try:
            if self.on_comments is not None:
                self.on_comments(key, fresh, total)
        except Exception as e:                      # noqa: BLE001
            try:
                logger.debug(f"评论回调异常: {e}")
            except Exception:
                pass

    # 评论 payload 里可能出现的「视频标识」字段名
    _VIDEO_ID_KEYS = ("objectId", "object_id", "objectNonceId", "object_nonce_id",
                      "nonceId", "nonce_id", "exportId", "export_id",
                      "feedId", "feed_id", "md5sum", "md5_sum", "vid")

    def _collect_video_ids(self, obj, depth: int = 0, out=None):
        """从评论 payload 里收集所有可能的「视频标识」值。

        ★ 不认识 payload 具体结构 → 按字段名递归找，
          找到就交给 GUI 去匹配记录（匹配不上自然退回老行为）。
        """
        if out is None:
            out = set()
        if depth > 8 or obj is None:
            return out
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in self._VIDEO_ID_KEYS and isinstance(v, (str, int)):
                    sv = str(v).strip()
                    if sv and len(sv) <= 128:
                        out.add(sv)
                elif isinstance(v, (dict, list)):
                    self._collect_video_ids(v, depth + 1, out)
                elif isinstance(v, str) and v.strip()[:1] in ("{", "["):
                    try:
                        dv = json.loads(v)
                        self._collect_video_ids(dv, depth + 1, out)
                    except Exception:
                        pass
        elif isinstance(obj, list):
            for v in obj[:200]:
                self._collect_video_ids(v, depth + 1, out)
        return out

    def set_current_video(self, key: str):
        """设置「当前播放的视频」，评论会挂到它身上。

        key 用 video_id（md5sum）优先，没有就用 url。
        """
        if key:
            self._cur_video_key = key

    def _parse_worker_loop(self):
        """独立解析线程（sphdl-parse）：json.loads + 递归扫描 + 字符串
        二次解码都在这里跑，事件循环保持零解析开销。
        _handle 内的 on_capture（GUI 队列）与 pipeline.submit（队列）
        均线程安全；seen 去重集由 _seen_lock 保护（API 直嗅路径在
        事件循环线程也会调 _handle）。"""
        while True:
            body, source = self._parse_q.get()
            try:
                # ★ v2.2.12：评论 payload 走单独分支（不含 objectDesc）
                if source == "__comment__":
                    self._handle_comment_payload(body)
                    continue
                obj = json.loads(body)
                # ★ v2.5.5（真机实测修正）：统计数据在 objectDesc 的**兄弟位置**
                #   （like_count / forward_count / comment_count / fav_count
                #    都在含 object_desc 的那个条目对象上）。
                #   所以必须用「带条目」的迭代器，把 entry 一起传下去。
                _entries = list(iter_object_desc_entries(obj))
                if _entries:
                    odes = [d for d, _e in _entries]
                    _entries_map = {id(d): e for d, e in _entries}
                else:
                    odes = [obj]  # 可能直接就是 object_desc 本体 / media 列表
                    _entries_map = {}
                if len(odes) > 1 and not self._multi_payload_logged:
                    # 诊断（一次）：bridge 回传的列表数据含多个视频 objectDesc。
                    # v2.0.6 逐个处理；v2.0.7 自然捕获模式下只缓存为候选。
                    self._multi_payload_logged = True
                    logger.info(f"📋 {source} payload 含 {len(odes)} 个 objectDesc"
                                "（列表数据）")
                found_any = False
                if len(odes) > 1 and self.natural_capture:
                    # 自然捕获：整页只缓存为候选，置顶锚点先入列，
                    # 其余等播放器请求到对应流/封面时再捕获
                    self._capture_anchor(odes, source, _entries_map)
                    self._buffer_pending(odes, source, _entries_map)
                    found_any = True
                else:
                    handled = set()
                    for od in odes:
                        vd = extract_from_object_desc(
                            od, entry=_entries_map.get(id(od)))
                        if not vd:
                            continue
                        key = vd.video_id or (vd.url, vd.decode_key)
                        if key in handled:
                            continue
                        handled.add(key)
                        self._handle(vd, source=source)
                    found_any = bool(handled)
                if not found_any:
                    cmd = obj.get("cmdName") if isinstance(obj, dict) else None
                    if cmd:
                        logger.info(f"🌉 bridge捕获: cmdName={cmd} | object_desc=✗ 未含")
                    else:
                        logger.debug(f"{source}：未识别到 media 信息")
            except Exception as e:
                logger.error(f"{source} 解析失败: {e}")

    def _touch_bridge(self, hb: bool):
        self.last_bridge_ts = time.time()
        if hb:
            self.hb_count += 1
        else:
            self.bridge_count += 1

    # ---------- ② API 直嗅 + ③④ JS 注入 ----------
    def error(self, flow: http.HTTPFlow):
        """连接/TLS 错误统计（v2.2.23）。

        ★ 用于"抓不到"看门狗判断证书是否生效：
          微信域名出现 TLS 握手失败 → 证书没装、或装了但**微信没重启**
          （微信在进程启动时读取系统证书信任，运行中不会重新读取）。
        """
        try:
            h = (flow.request.pretty_host or "").lower()
            if ("weixin.qq.com" in h or "qq.com" in h
                    or "weixinbridge.com" in h):
                self.stat_tls_fail += 1
                logger.warn(f"微信域名连接失败 {h}: "
                            f"{(flow.error or '')[:120]}")
        except Exception:
            pass

    def sniff_diagnostics(self) -> dict:
        """给 GUI 看门狗用的诊断快照（v2.2.23）。"""
        import time as _t
        age = (_t.time() - self.stat_last_hit_ts) if self.stat_last_hit_ts else -1
        return {
            "channels_hits": self.stat_channels_hits,
            "tls_fail": self.stat_tls_fail,
            "captured": self.stat_captured,
            "comments": self.stat_comment_payloads,
            "last_hit_age": age,
            # 注：addon 本身没有 running 属性（由 engine 管），
            #     这里只暴露统计，是否在嗅探由 GUI 自己判断
            "sniffing": bool(getattr(self, "running", False)),
        }

    # ────────── 小程序短剧「边播边存」（v2.7.7）──────────
    def _save_miniapp_media(self, flow):
        """把小程序视频响应体落盘（支持 Range 分片拼装）。

        ★ 为什么这么做（2026-10-30 实测结论）：
          · 地址形如 `xxx.vodpreview.wxamedia.com/<vod>/<episode_id>/f0.mp4?t=..&us=..`
          · `t=` 是时间戳签名，几分钟后必然 403
          · 用微信 UA + Referer + Range 等 5 种组合外部重下，**全部 403**
            → 说明还依赖小程序会话（cookie / 设备指纹），外部下载走不通
          · 但流经代理的响应是**明文可用**的 → 直接 tee 落盘最可靠

        分片处理：播放器用 Range 请求（206）。按 Content-Range 的 start/total
        定位写入，凑满 total 即完成，然后改名去掉 .part。
        """
        import os as _os
        import re as _re
        from pathlib import Path as _P

        resp = flow.response
        path = flow.request.path or ""
        if ".mp4" not in path.lower():
            return
        if resp.status_code not in (200, 206):
            return
        body = resp.content or b""
        if not body:
            return

        # 集号：路径里 `/<vod>/<episode_id>/f0.mp4`
        m = _re.search(r"/([a-f0-9]{20,})/f0\.mp4", path)
        ep = m.group(1) if m else str(abs(hash(path)) % 10**12)

        try:
            from . import config as _cfg
            base = _P(getattr(_cfg, "DOWNLOAD_DIR", ".")) / "小程序短剧"
        except Exception:
            base = _P("小程序短剧")
        base.mkdir(parents=True, exist_ok=True)

        final = base / f"{ep}.mp4"
        if final.exists() and final.stat().st_size > 1024:
            return                                    # 已存过

        part = base / f"{ep}.mp4.part"
        cr = resp.headers.get("content-range") or ""
        total = 0
        start = 0
        if cr:
            mm = _re.match(r"bytes\s+(\d+)-(\d+)/(\d+)", cr)
            if mm:
                start, _end, total = int(mm.group(1)), int(mm.group(2)), int(mm.group(3))
        elif resp.status_code == 200:
            total = len(body)

        mode = "r+b" if part.exists() else "wb"
        with open(part, mode) as f:
            if start:
                f.seek(start)
            else:
                f.seek(0)
            f.write(body)

        # ★ 完成判定必须按「字节区间覆盖」算，不能看文件大小。
        #   实测教训：分片可能乱序到达（先到 2000 段），seek 写完后文件大小
        #   就已经等于 total，但 0~2000 还是空洞 → 提前改名产生残缺文件。
        ranges = getattr(self, "_mp_ranges", None)
        if ranges is None:
            ranges = self._mp_ranges = {}
        rec = ranges.setdefault(ep, {"total": 0, "got": []})
        if total:
            rec["total"] = total
        # 记录本次写入的区间
        seg_end = start + len(body) - 1
        rec["got"].append((start, seg_end))

        covered = 0
        if rec["total"]:
            # 合并区间，算连续覆盖长度
            merged = []
            for a, b in sorted(rec["got"]):
                if merged and a <= merged[-1][1] + 1:
                    merged[-1][1] = max(merged[-1][1], b)
                else:
                    merged.append([a, b])
            covered = sum(b - a + 1 for a, b in merged)
            complete = (len(merged) == 1 and merged[0][0] == 0
                        and merged[0][1] >= rec["total"] - 1)
        else:
            complete = True        # 没有 total 信息（200 全量）→ 视为完成

        got = part.stat().st_size
        if complete:
            try:
                _os.replace(part, final)
                logger.ok(f"🎬 小程序短剧已保存：{final.name} "
                          f"（{got / 1048576:.1f} MB）→ {final.parent}")
                self._miniapp_saved = getattr(self, "_miniapp_saved", 0) + 1
                ranges.pop(ep, None)
            except Exception:
                pass
        else:
            logger.debug(f"🎬 小程序短剧分片 {ep[:10]}… "
                         f"{covered / 1048576:.1f}/"
                         f"{rec['total'] / 1048576:.1f} MB")

    def response(self, flow: http.HTTPFlow):
        resp = flow.response
        if resp is None:
            return
        logger.debug(
            f"📡 ← {resp.status_code} {flow.request.host}{mask_url(flow.request.path)} | "
            f"{len(resp.content or b'')}B | {(resp.headers.get('content-type') or '')[:40]}"
        )
        if resp.status_code not in (200, 206):
            return
        host = flow.request.host
        # 206 = Range 分片流，视频号 CDN 拉流特征 → 用于看门狗判断"正在播放"
        if resp.status_code == 206:
            self.last_stream_ts = time.time()
        # 自然捕获（v2.0.7）：播放器请求某条视频的流/封面 → 命中候选并捕获
        if "stodownload" in flow.request.path:
            self._maybe_capture_from_stream(flow.request.path)

        # ★★★ v2.7.7：小程序短剧「边播边存」
        #   实测（2026-10-30）：小程序视频在 `*.wxamedia.com/.../f0.mp4`，
        #   是**完整 MP4**（单集 3~7MB），但 URL 带 `t=` 时间戳签名，
        #   几分钟就 403；而且外部重下（换任何 headers）也都 403。
        #   → 唯一可靠办法：**把流经代理的响应体直接落盘**，
        #     不需要 cookie、不需要签名、不担心过期。
        if host.endswith("wxamedia.com"):
            try:
                self._save_miniapp_media(flow)
            except Exception as e:                    # noqa: BLE001
                logger.warn(f"小程序短剧保存失败：{e}")

        # ③ 注入点：web-finder 页面的 svg-icons-register JS
        if host.endswith("res.wx.qq.com") and config.INJECT_JS_MARKER in flow.request.path:
            self._inject_js(flow)
            return

        # ④ bridge 钩子：视频号网页 HTML 页面（PC 微信 webview 走网络加载）
        ctype = (resp.headers.get("content-type") or "").lower()
        if any(host.endswith(h) for h in config.BRIDGE_HOOK_HOSTS) and "text/html" in ctype:
            self._inject_bridge_hook(flow)
            return

        # ② 只嗅探 qq.com 域名的 JSON 响应
        if not host.endswith(config.SNIFF_HOST_SUFFIXES):
            return
        if "json" not in ctype:
            return
        body = resp.content or b""
        if not body:
            return
        try:
            obj = json.loads(body)
        except Exception:
            return
        odes = list(iter_object_descs(obj))
        logger.info(f"🔍 API直嗅: {host} {flow.request.path[:60]} | {len(body)}B"
                    f" | object_desc={'✓ 命中×' + str(len(odes)) if odes else '✗ 未含'}")
        if not odes:
            return
        source = f"API直嗅({host})"
        if len(odes) > 1 and self.natural_capture:
            self._capture_anchor(odes, source, _entries_map)
            self._buffer_pending(odes, source, _entries_map)
        else:
            handled = set()
            for od in odes:
                vd = extract_from_object_desc(
                    od, entry=_entries_map.get(id(od)))
                if not vd:
                    continue
                key = vd.video_id or (vd.url, vd.decode_key)
                if key in handled:
                    continue
                handled.add(key)
                self._handle(vd, source=source)

    # ---------- 自然捕获：候选缓存 / 置顶锚点 / 播放命中 ----------
    def _buffer_pending(self, odes, source: str, entries_map=None):
        """列表数据缓存为候选（自然捕获）。不显示、不下载，
        等播放器请求到对应流/封面时由 _maybe_capture_from_stream 捕获。"""
        now = time.time()
        added = 0
        with self._pending_lock:
            self._pending = [p for p in self._pending
                             if now - p["ts"] < self.PENDING_EXPIRE]
            seen = {p["key"] for p in self._pending}
            for od in odes:
                vd = extract_from_object_desc(
                    od, entry=_entries_map.get(id(od)))
                if not vd:
                    continue
                key = vd.video_id or (vd.url, vd.decode_key)
                if key in seen or key in self.seen:
                    continue
                efk = _extract_encfilekey(vd.url)
                if not efk:
                    continue  # 无法用流/封面 URL 匹配 → 不缓存（详情/锚点路径仍可捕获）
                seen.add(key)
                self._pending.append({"efk": efk, "key": key,
                                      "vd": vd, "ts": now, "source": source})
                added += 1
            if len(self._pending) > self.PENDING_CAP:
                self._pending = self._pending[-self.PENDING_CAP:]
        if added:
            logger.info(f"🧲 自然捕获：已缓存 {added} 条候选"
                        "（播放到哪条才捕获哪条）")

    def _capture_anchor(self, odes, source: str, entries_map=None):
        """置顶锚点：新列表数据的前 ANCHOR 条先入列表
        （对应「当前浏览 + 随后的几个缓存」，保证有可见反馈）。

        ★ v2.6.6：长视频（直播回放）**整页全抓**。
        博主的「直播回放」列表一个 payload 带 8 条回放（每条 1.8~7 小时、
        2~3GB），老逻辑只抓前 2 条 → 245 条要滑 122 次。
        现在长视频全部入列（单页上限 LONG_PER_PAGE_CAP）。
        """
        if entries_map is None:
            entries_map = {}

        def _dur(od) -> int:
            try:
                m = (od or {}).get("media") or []
                return int((m[0] or {}).get("video_play_len") or 0) if m else 0
            except Exception:
                return 0

        grabbed = 0
        long_added = 0
        # ① 先按老逻辑抓前 ANCHOR 条（保证「当前浏览」立刻可见）
        for od in odes:
            if grabbed >= self.ANCHOR:
                break
            vd = extract_from_object_desc(od, entry=entries_map.get(id(od)))
            if not vd:
                continue
            key = vd.video_id or (vd.url, vd.decode_key)
            if key in self.seen:
                continue
            grabbed += 1
            self._handle(vd, source=source)
        if grabbed:
            logger.ok(f"🎯 自然捕获：新列表前 {grabbed} 条已入列表"
                      "（当前浏览+随后的缓存）")

        # ② 再补抓本页所有长视频（直播回放场景）
        for od in odes:
            if long_added >= self.LONG_PER_PAGE_CAP:
                break
            if _dur(od) < self.LONG_VIDEO_SEC:
                continue
            vd = extract_from_object_desc(od, entry=entries_map.get(id(od)))
            if not vd:
                continue
            key = vd.video_id or (vd.url, vd.decode_key)
            if key in self.seen:
                continue
            long_added += 1
            self._handle(vd, source=source)
        if long_added:
            logger.ok(f"📺 长视频整页捕获：本页另入 {long_added} 条"
                      f"（时长 ≥{self.LONG_VIDEO_SEC // 60} 分钟，多为直播回放）")

    def _maybe_capture_from_stream(self, url: str):
        """播放器请求某条视频的流/封面 → 捕获命中的候选 + 后续 LOOKAHEAD 条。"""
        if not self.natural_capture:
            return
        efk = _extract_encfilekey(url)
        if not efk:
            return
        with self._pending_lock:
            if not self._pending:
                return
            idx = next((i for i, p in enumerate(self._pending)
                        if p["efk"] == efk), -1)
            if idx < 0:
                return
            end = min(len(self._pending), idx + 1 + self.LOOKAHEAD)
            batch = self._pending[idx:end]
            del self._pending[idx:end]
        if batch:
            logger.info(f"🎯 播放命中：捕获当前播放 + 后续 {len(batch) - 1} 条缓存")
            for p in batch:
                self._handle(p["vd"], source=p["source"])

    def _handle(self, vd, source: str):
        # v2.0.4「🖼 下载图片」关闭时：图片直接跳过（不显示、不下载）。
        # 放在去重之前：不占 seen 名额，重新开启开关后同一图片可再捕获。
        if vd.is_image and not self.allow_images:
            logger.info(f"🖼 跳过图片（下载图片已关闭）: {vd.description or '(无描述)'}")
            return
        # 去重优先用稳定视频 ID（md5sum/encfilekey）：同一视频反复播放
        # 会带新 token 产生新 URL，按 URL 去重会把同一视频重复下载多份
        key = vd.video_id or (vd.url, vd.decode_key)
        with self._seen_lock:
            if key in self.seen:
                if key not in self._dedup_logged:
                    self._dedup_logged.add(key)
                    logger.info(f"⏭ 重复视频（已捕获过），跳过: "
                                f"{vd.description or '(无描述)'}")
                else:
                    logger.debug("重复视频，跳过")
                return
            self.seen.add(key)
        self.capture_count += 1
        self.last_capture_ts = time.time()
        if self.batch_mode:
            self.batch_count += 1
        logger.info(f"🎬 捕获到视频（{source}）: {vd.description or '(无描述)'}")
        logger.debug(f"🧬 捕获结构: {describe_capture(vd)}")
        accepted = True
        if self.on_capture:
            try:
                accepted = self.on_capture(vd, source)
                # ★ v2.2.23：捕获成功计数（供"抓不到"看门狗对比）
                self.stat_captured += 1
            except Exception:
                accepted = True
        if accepted is False:
            # 列表已满被 GUI 拒收（v2.0.17：满档 = 不再加载后续视频）。
            # 关键：撤回「已见」标记 —— 否则用户调高档位后重新拉取时，
            # 这条会被内存去重永远挡掉，等于白拉。
            with self._seen_lock:
                self.seen.discard(key)
                self._dedup_logged.discard(key)
            self.rejected_count += 1
            if self.rejected_count in (1, 10, 50) or self.rejected_count % 100 == 0:
                logger.warn(f"📵 列表已满，本条未加载"
                            f"（累计未加载 {self.rejected_count} 条；"
                            "调高「列表上限」后重新拉取即可加载）")
            return
        if self.auto_download:
            # ★ v2.2.5：自动下载也必须过授权门禁
            #
            #   客户报障（2026-10-23）：「当前 2.2.4 显示未授权，为何还能下载视频」
            #   根因：这里直接 self.pipeline.submit(vd)，
            #   **绕过了 GUI 的 _submit_record() 授权门禁** ——
            #   未授权时只要 auto_download 为真就能下载。
            #   现在加一道 license_check 回调（GUI 传 _has_download_license），
            #   未授权一律不提交，并只提示一次（自动下载不能刷屏）。
            if self.license_check is not None:
                try:
                    _ok = bool(self.license_check())
                except Exception:
                    _ok = False
                if not _ok:
                    if not getattr(self, "_unlic_skip_logged", False):
                        self._unlic_skip_logged = True
                        logger.warn("🔒 未授权：可捕获但不会下载（自动下载已跳过）。"
                                    "激活授权码后自动恢复。")
                    return
            if self.duplicate_check and self.duplicate_check(vd):
                if key not in self._dedup_logged:
                    self._dedup_logged.add(key)
                    logger.info(f"⏭ 重复视频（列表中已有），跳过自动下载: "
                                f"{vd.description or '(无描述)'}")
            else:
                self.pipeline.submit(vd)
        else:
            logger.info("已加入待下载列表（GUI 模式不自动下载，请在界面点选后下载）")

    def release_seen(self, video_ids):
        """删记录后释放 seen 名额，使这些视频可被**重新捕获**（v2.0.18 修复）。

        背景（2026-10-16 用户报告的真实故障）：
          客户在旗舰版下抓到 3000 条 → 删掉所有记录 → 重新打开视频号
          → 清单不动。根因是 `seen` 是内存集合、只增不减，而删记录只清了
          GUI 侧索引，没通知 addon；于是同一批视频全部命中 seen 被跳过。

        与既有的「满档拒收撤回已见标记」（见 _handle 里 accepted is False 分支）
        是同一根因的另一个入口：**凡是把记录从列表移除，都要释放 seen**。
        """
        ids = [v for v in (video_ids or []) if v]
        if not ids:
            return 0
        released = 0
        with self._seen_lock:
            for vid in ids:
                if vid in self.seen:
                    self.seen.discard(vid)
                    released += 1
                self._dedup_logged.discard(vid)
        return released

    def clear_seen(self):
        """清空全部 seen（「删除全部记录」时调用）——返回清掉的条目数。

        比逐个 release_seen 更彻底：`seen` 里可能残留**列表里已无**的 key
        （例如满档被拒后又被其他路径留在 seen 的、或候选缓存过的），
        只按记录逐个释放清不掉它们，会导致后续拉取被静默跳过。
        """
        with self._seen_lock:
            n = len(self.seen)
            self.seen.clear()
            self._dedup_logged.clear()
        return n

    # ---------- ③ JS 注入 ----------
    def _inject_js(self, flow: http.HTTPFlow):
        if self.injected:
            return
        try:
            content = flow.response.content.decode("utf-8", errors="ignore")

            media_hook = (
                "get media(){\n"
                "    if(this.objectDesc){\n"
                '        fetch("https://wxapp.tc.qq.com/res-downloader/wechat?type=1", {\n'
                '          method: "POST",\n'
                '          mode: "no-cors",\n'
                "          body: JSON.stringify(this.objectDesc)\n"
                "        });\n"
                "    };\n"
            )
            comment_hook = (
                "\nasync finderGetCommentDetail(\\1) {\n"
                "    var res = await\\2;\n"
                "    if (res?.data?.object?.objectDesc) {\n"
                '        fetch("https://wxapp.tc.qq.com/res-downloader/wechat?type=2", {\n'
                '          method: "POST",\n'
                '          mode: "no-cors",\n'
                "          body: JSON.stringify(res.data.object.objectDesc)\n"
                "        });\n"
                "    }\n"
                "    return res;\n"
                "}async\n"
            )
            new_content, n_media = self.media_regex.subn(media_hook, content)
            new_content, n_comment = self.comment_regex.subn(comment_hook, new_content)

            if n_media or n_comment:
                flow.response.content = new_content.encode("utf-8")
                flow.response.headers["Content-Length"] = str(len(flow.response.content))
                self.injected = True
                logger.ok(f"✅ JS 注入成功（media 钩子 {n_media} 处 / 评论钩子 {n_comment} 处），JS 回调嗅探已激活")
            else:
                logger.warn("未找到 JS 注入点（微信版本可能已变化）——自动回退到 API 直嗅模式")
        except Exception as e:
            logger.error(f"JS 注入失败: {e}")

    # ---------- ④ bridge 钩子注入（2.7 方案） ----------
    def _inject_bridge_hook(self, flow: http.HTTPFlow):
        """往 HTML 页面的 </head> 前注入 WeixinJSBridge.invoke 包装器。"""
        try:
            content = flow.response.content.decode("utf-8", errors="ignore")
            if "__sphdl_hooked" in content:
                return  # 已注入（理论上不会重复）
            if "</head>" in content:
                new_content = content.replace("</head>", self.bridge_js + "\n</head>", 1)
            else:
                new_content = content + self.bridge_js
            if new_content != content:
                flow.response.content = new_content.encode("utf-8")
                flow.response.headers["Content-Length"] = str(len(flow.response.content))
                self.bridge_hooked_pages += 1
                logger.ok(
                    f"🌉 bridge 钩子已注入 #{self.bridge_hooked_pages}: "
                    f"{flow.request.host}{flow.request.path[:60]}"
                )
        except Exception as e:
            logger.error(f"bridge 钩子注入失败: {e}")
