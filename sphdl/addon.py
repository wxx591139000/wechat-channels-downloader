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

from mitmproxy import http

from . import config
from . import shared
from .logger import logger
from .parser import extract_from_object_desc, iter_object_descs
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
</script>
"""

# ⚡ 批量拉取自动滚动驱动（v2.0.8）：开启批量模式后注入视频号页面。
# 每 3s 滚动到列表底部触发页面"加载更多"，直到：
#  - 滚动高度连续 10 轮（~30s）不再增长且已在底部 → 完成
#  - 或超过 200 轮（~10 分钟）兜底停止
# 心跳/完成都 POST 回代理 /__sphdl/batch。
_BATCH_JS_TEMPLATE = """
<script>
(function () {
  if (window.__sphdl_batch_driver) { return; }
  window.__sphdl_batch_driver = true;
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
    if (el === document.scrollingElement || el === document.documentElement) {
      window.scrollTo(0, el.scrollHeight);
    } else {
      el.scrollTop = el.scrollHeight;
    }
    return {h: el.scrollHeight || 0, t: el.scrollTop || 0, c: el.clientHeight || 0};
  }
  function post(o) {
    try {
      fetch("http://127.0.0.1:__PORT__/__sphdl/batch", {
        method: "POST", mode: "no-cors", body: JSON.stringify(o)
      });
    } catch (e) {}
  }
  var maxH = 0, stable = 0, rounds = 0;
  var timer = setInterval(function () {
    try {
      rounds++;
      var r = doScroll();
      if (r.h > maxH + 10) { maxH = r.h; stable = 0; }
      else if (r.h > 0 && r.t >= r.h - r.c - 60) { stable++; }
      else { stable = 0; }
      if (rounds > 200 || stable >= 10) {
        clearInterval(timer);
        post({batch_done: 1, rounds: rounds, maxHeight: maxH});
        return;
      }
      post({batch: 1, rounds: rounds});
    } catch (e) {}
  }, 3000);
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


class SphAddon:
    def __init__(self, pipeline, on_capture=None, auto_download: bool = True,
                 allow_images: bool = True):
        """auto_download=False 时（GUI 模式）：只捕获进列表，
        不自动提交下载队列，由用户点选后再下载。
        allow_images=False 时（v2.0.4「🖼 下载图片」开关关闭）：
        图片不捕获、不显示、不下载——直接跳过（不占去重名额，
        重新开启后同一图片可再次被捕获）。"""
        self.pipeline = pipeline
        self.on_capture = on_capture  # 可选回调 fn(vd, source)（GUI 用于更新列表）
        self.auto_download = auto_download
        self.allow_images = allow_images
        # 可选 fn(vd)->bool：GUI 跨会话去重（records.json 已有该视频/链接则 True）。
        # 在自动下载提交前拦截，避免"列表去重跳过了、文件却重复下载"（v2.0.5 修复）
        self.duplicate_check = None
        self.seen = set()          # 去重: (url, decode_key)
        self._dedup_logged = set()  # 已提示过的重复视频 key（同一视频只提示一次，防刷屏）
        self._multi_payload_logged = False  # 多视频 payload 诊断提示只打一次
        # ---- 自然捕获（v2.0.7）----
        self.natural_capture = True   # GUI「🎯 自然捕获」开关：False = 整页列表捕获
        self.ANCHOR = 2               # 新列表数据前几条先入列（当前浏览锚点）
        self.LOOKAHEAD = 2            # 播放命中后，紧随其后的几条一起捕获
        self.PENDING_CAP = 200        # 候选缓存上限
        self.PENDING_EXPIRE = 1800    # 候选缓存过期（秒）
        self._pending = []            # [{efk, key, vd, ts, source}] 按列表顺序
        self._pending_lock = _threading.Lock()
        # ---- ⚡ 批量拉取（v2.0.8）----
        self.batch_mode = False       # GUI「⚡ 批量拉取」开关
        self.batch_count = 0          # 批量期间新增捕获数
        self._batch_active = False    # 页面滚动驱动是否在跑
        self.last_batch_ts = 0.0      # 最近一次批量心跳
        self._batch_js = _BATCH_JS_TEMPLATE.replace("__PORT__", str(config.PROXY_PORT))
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
            self._batch_js = _BATCH_JS_TEMPLATE.replace("__PORT__", str(port))
            self._bridge_port = port
            logger.debug(f"bridge 钩子端口已更新为 {port}")

    # ---------- ① JS 回调通道 + ④ bridge 钩子回传 ----------
    def request(self, flow: http.HTTPFlow):
        """运行在 mitmproxy asyncio 事件循环上 —— 只做 O(1) 的事，绝不阻塞！
        重解析（json.loads + 递归扫描 + 字符串二次解码）卸载到独立工作线程
        sphdl-parse（v1.2.5）：钩子 = 字节预检 + 回 OK + 入队，实测 5MB
        payload 钩子耗时 ~4ms。无论 payload 多大、循环多忙，捕获链路
        都不可能再被本工具自己的代码堵死（24h 连续播放需求）。"""
        req = flow.request
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
            body = req.content or b""
            self.last_batch_ts = time.time()
            if b"batch_done" in body:
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
                flow.response = http.Response.make(
                    200, b"OK", {"Content-Type": "text/plain"})
                return
        # 先回 OK（微信侧不受任何延迟影响），再把 body 交给工作线程解析。
        # 钩子全程 O(1)：预检 + 回包 + 入队，无 json.loads、无递归扫描。
        flow.response = http.Response.make(200, b"OK", {"Content-Type": "text/plain"})
        self._parse_q.put((body, source))

    def _parse_worker_loop(self):
        """独立解析线程（sphdl-parse）：json.loads + 递归扫描 + 字符串
        二次解码都在这里跑，事件循环保持零解析开销。
        _handle 内的 on_capture（GUI 队列）与 pipeline.submit（队列）
        均线程安全；seen 去重集由 _seen_lock 保护（API 直嗅路径在
        事件循环线程也会调 _handle）。"""
        while True:
            body, source = self._parse_q.get()
            try:
                obj = json.loads(body)
                odes = list(iter_object_descs(obj))
                if not odes:
                    odes = [obj]  # 可能直接就是 object_desc 本体 / media 列表
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
                    self._capture_anchor(odes, source)
                    self._buffer_pending(odes, source)
                    found_any = True
                else:
                    handled = set()
                    for od in odes:
                        vd = extract_from_object_desc(od)
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

        # ③ 注入点：web-finder 页面的 svg-icons-register JS
        if host.endswith("res.wx.qq.com") and config.INJECT_JS_MARKER in flow.request.path:
            self._inject_js(flow)
            return

        # ④ bridge 钩子：视频号网页 HTML 页面（PC 微信 webview 走网络加载）
        ctype = (resp.headers.get("content-type") or "").lower()
        if any(host.endswith(h) for h in config.BRIDGE_HOOK_HOSTS) and "text/html" in ctype:
            self._inject_bridge_hook(flow)
            if self.batch_mode:
                self._inject_batch_driver(flow)
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
            self._capture_anchor(odes, source)
            self._buffer_pending(odes, source)
        else:
            handled = set()
            for od in odes:
                vd = extract_from_object_desc(od)
                if not vd:
                    continue
                key = vd.video_id or (vd.url, vd.decode_key)
                if key in handled:
                    continue
                handled.add(key)
                self._handle(vd, source=source)

    # ---------- 自然捕获：候选缓存 / 置顶锚点 / 播放命中 ----------
    def _buffer_pending(self, odes, source: str):
        """列表数据缓存为候选（自然捕获）。不显示、不下载，
        等播放器请求到对应流/封面时由 _maybe_capture_from_stream 捕获。"""
        now = time.time()
        added = 0
        with self._pending_lock:
            self._pending = [p for p in self._pending
                             if now - p["ts"] < self.PENDING_EXPIRE]
            seen = {p["key"] for p in self._pending}
            for od in odes:
                vd = extract_from_object_desc(od)
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

    def _capture_anchor(self, odes, source: str):
        """置顶锚点：新列表数据的前 ANCHOR 条先入列表
        （对应「当前浏览 + 随后的几个缓存」，保证有可见反馈）。"""
        grabbed = 0
        for od in odes:
            if grabbed >= self.ANCHOR:
                break
            vd = extract_from_object_desc(od)
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
        if self.on_capture:
            try:
                self.on_capture(vd, source)
            except Exception:
                pass
        if self.auto_download:
            if self.duplicate_check and self.duplicate_check(vd):
                if key not in self._dedup_logged:
                    self._dedup_logged.add(key)
                    logger.info(f"⏭ 重复视频（列表中已有），跳过自动下载: "
                                f"{vd.description or '(无描述)'}")
            else:
                self.pipeline.submit(vd)
        else:
            logger.info("已加入待下载列表（GUI 模式不自动下载，请在界面点选后下载）")

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

    def _inject_batch_driver(self, flow: http.HTTPFlow):
        """⚡ 批量拉取：往视频号页面注入自动滚动驱动（仅 batch_mode 时调用）。"""
        try:
            content = flow.response.content.decode("utf-8", errors="ignore")
            if "__sphdl_batch_driver" in content:
                return
            if "</head>" in content:
                new_content = content.replace("</head>",
                                              self._batch_js + "\n</head>", 1)
            else:
                new_content = content + self._batch_js
            if new_content != content:
                flow.response.content = new_content.encode("utf-8")
                flow.response.headers["Content-Length"] = str(len(flow.response.content))
                self._batch_active = True
                logger.ok(f"⚡ 批量拉取驱动已注入: "
                          f"{flow.request.host}{flow.request.path[:60]}")
        except Exception as e:
            logger.error(f"批量拉取驱动注入失败: {e}")
