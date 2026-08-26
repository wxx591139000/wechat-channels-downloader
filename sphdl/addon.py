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
from .parser import extract_from_object_desc, find_object_desc_in_json

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


class SphAddon:
    def __init__(self, pipeline, on_capture=None, auto_download: bool = True):
        """auto_download=False 时（GUI 模式）：只捕获进列表，
        不自动提交下载队列，由用户点选后再下载。"""
        self.pipeline = pipeline
        self.on_capture = on_capture  # 可选回调 fn(vd, source)（GUI 用于更新列表）
        self.auto_download = auto_download
        self.seen = set()          # 去重: (url, decode_key)
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
        # ---- 解析卸载（v1.2.2）：request() 在事件循环上只做 O(1) 的事，
        #      json.loads + 递归扫描搬进独立线程 —— 无论 payload 多大、
        #      循环多忙，捕获链路都不可能被本工具的代码堵死（24h 连续播放）
        self.loop_delay_ms = 0     # 事件循环延迟（引擎每 5s 自测，供状态栏/心跳）
        self._parse_q = _queue.Queue()
        self._parse_worker = _threading.Thread(target=self._parse_worker_loop,
                                               name="sphdl-parse", daemon=True)
        self._parse_worker.start()
        self.media_regex = re.compile(r"get\s+media\(\)\{", re.MULTILINE)
        self.comment_regex = re.compile(
            r"async\s+finderGetCommentDetail\((\w+)\)\s*\{return(.*?)\s*\}\s*async",
            re.MULTILINE | re.DOTALL,
        )
        self.bridge_js = _BRIDGE_JS_TEMPLATE.replace("__PORT__", str(config.PROXY_PORT))
        logger.ok("嗅探插件已就绪")

    # ---------- ① JS 回调通道 + ④ bridge 钩子回传 ----------
    def request(self, flow: http.HTTPFlow):
        """运行在 asyncio 事件循环上 —— 只做 O(1) 的事，绝不阻塞！
        重解析（json.loads + 递归扫描 + 二次解码）卸载到独立工作线程，
        保证无论 payload 多大、循环多忙，捕获链路不会被本工具自己的
        代码堵死（24h 连续播放需求，v1.2.2）。"""
        req = flow.request
        # 顺带捕获微信 webview 的 UA：CDN 对含 MicroMessenger 的 UA 限速更宽，
        # 下载器复用（shared 同进程共享，无需回调）。
        if "wxhls" in req.host or req.host.endswith("wxapp.tc.qq.com") or "live" in req.host:
            ua = (req.headers.get("User-Agent") or "").strip()
            if "micromessenger" in ua.lower():
                shared.set_wechat_ua(ua)
        logger.debug(f"📡 → {req.method} {req.host}{req.path[:100]}")
        is_js_callback = req.host.endswith(config.CALLBACK_HOST) and config.CALLBACK_PATH in req.path
        is_bridge = config.BRIDGE_CALLBACK_PATH in req.path
        if not (is_js_callback or is_bridge):
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
            # 字节预检（C 级快扫）：body 里根本没有 objectDesc 字样时，
            # 跳过 json.loads + 递归扫描（v1.2.1）。
            if b"object_desc" not in body and b"objectDesc" not in body:
                flow.response = http.Response.make(
                    200, b"OK", {"Content-Type": "text/plain"})
                return
        # 先回 OK（微信侧不受任何延迟影响），再把 body 交给工作线程解析
        flow.response = http.Response.make(200, b"OK", {"Content-Type": "text/plain"})
        self._parse_q.put((body, source))

    def _parse_worker_loop(self):
        """独立解析线程：json.loads + 递归扫描 + 字符串二次解码都在这里跑，
        事件循环保持零解析开销。_handle 内的 on_capture（GUI 队列）与
        pipeline.submit（队列）均线程安全；seen 去重集合仅本线程访问。"""
        while True:
            body, source = self._parse_q.get()
            try:
                obj = json.loads(body)
                od = find_object_desc_in_json(obj)
                if od is None:
                    od = obj  # 可能直接就是 object_desc 本体
                vd = extract_from_object_desc(od)
                if vd:
                    self._handle(vd, source=source)
                else:
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
            f"📡 ← {resp.status_code} {flow.request.host}{flow.request.path[:80]} | "
            f"{len(resp.content or b'')}B | {(resp.headers.get('content-type') or '')[:40]}"
        )
        if resp.status_code not in (200, 206):
            return
        host = flow.request.host
        # 206 = Range 分片流，视频号 CDN 拉流特征 → 用于看门狗判断"正在播放"
        if resp.status_code == 206:
            self.last_stream_ts = time.time()

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
        od = find_object_desc_in_json(obj)
        logger.info(f"🔍 API直嗅: {host} {flow.request.path[:60]} | {len(body)}B | object_desc={'✓ 命中' if od else '✗ 未含'}")
        if od is None:
            return
        vd = extract_from_object_desc(od)
        if vd:
            self._handle(vd, source=f"API直嗅({host})")

    def _handle(self, vd, source: str):
        # 去重优先用稳定视频 ID（md5sum/encfilekey）：同一视频反复播放
        # 会带新 token 产生新 URL，按 URL 去重会把同一视频重复下载多份
        key = vd.video_id or (vd.url, vd.decode_key)
        if key in self.seen:
            logger.debug("重复视频，跳过")
            return
        self.seen.add(key)
        self.capture_count += 1
        self.last_capture_ts = time.time()
        logger.info(f"🎬 捕获到视频（{source}）: {vd.description or '(无描述)'}")
        if self.on_capture:
            try:
                self.on_capture(vd, source)
            except Exception:
                pass
        if self.auto_download:
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
