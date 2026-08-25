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
import re

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
      if (payload.length > 2000000) { return; }
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
  }
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
        self.media_regex = re.compile(r"get\s+media\(\)\{", re.MULTILINE)
        self.comment_regex = re.compile(
            r"async\s+finderGetCommentDetail\((\w+)\)\s*\{return(.*?)\s*\}\s*async",
            re.MULTILINE | re.DOTALL,
        )
        self.bridge_js = _BRIDGE_JS_TEMPLATE.replace("__PORT__", str(config.PROXY_PORT))
        logger.ok("嗅探插件已就绪")

    # ---------- ① JS 回调通道 + ④ bridge 钩子回传 ----------
    def request(self, flow: http.HTTPFlow):
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
        try:
            obj = json.loads(req.content)
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
        # 无论成功与否都回 OK，避免影响微信自身
        flow.response = http.Response.make(200, b"OK", {"Content-Type": "text/plain"})

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
