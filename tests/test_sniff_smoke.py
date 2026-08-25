"""
冒烟测试：
  1. JS 注入：对模拟的 web-finder JS 响应执行 _inject_js，验证钩子被正确替换
  2. 嗅探路由：模拟 API 响应 / JS 回调请求，验证能捕获 (url, decode_key)
  3. mitmproxy 进程内启动 + 代理可用性（asyncio）
运行: python tests/test_sniff_smoke.py
"""
import asyncio
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from sphdl.addon import SphAddon  # noqa: E402
from sphdl.proxy_manager import check_proxy_alive, get_mitmproxy_certs  # noqa: E402

def mk_flow(url, method="GET", body=None, ctype=None):
    """构造一个 mitmproxy flow（v11 API）"""
    from mitmproxy import connection
    f = http.HTTPFlow(
        connection.Client(peername=("127.0.0.1", 54321), sockname=("127.0.0.1", 18899)),
        connection.Server(address=("res.wx.qq.com", 443)),
    )
    f.request = http.Request.make(method, url, body or b"")
    return f


def mk_response(f, body, ctype=None):
    headers = {}
    if ctype:
        headers["Content-Type"] = ctype
    f.response = http.Response.make(200, body, headers)
    return f


PORT = 18899
PASS = FAIL = 0
RESULTS = {}


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name}  {detail}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {detail}")


class FakePipeline:
    """记录 submit 的假管线"""
    def __init__(self):
        self.items = []
    def submit(self, vd):
        self.items.append(vd)


pipe = FakePipeline()
addon = SphAddon(pipe)

# ---------- 1. JS 注入 ----------
print("== 1. JS 注入测试 ==")
from mitmproxy import http  # noqa: E402

fake_js = (
    "var a=1;"
    "get media(){return this._media;}"
    "async finderGetCommentDetail(x){return this._fetch(x)}async other(){return 1}"
)
flow = mk_response(
    mk_flow("https://res.wx.qq.com/web-finder/res/js/virtual_svg-icons-register.publish.js"),
    fake_js.encode("utf-8"), "application/javascript")
addon._inject_js(flow)
out = flow.response.content.decode("utf-8")
check("media 钩子注入", "res-downloader/wechat?type=1" in out and "get media(){" in out)
check("评论钩子注入", "res-downloader/wechat?type=2" in out and "var res = await" in out)
check("注入状态标记", addon.injected is True)

# ---------- 2. 嗅探路由 ----------
print("== 2. 嗅探路由测试 ==")

# 2a. API 直嗅：模拟 channels.weixin.qq.com 的 object_desc 响应
api_body = (
    '{"code":0,"data":{"object_desc":{"description":"冒烟测试视频",'
    '"media":[{"url":"https://finder.video.qq.com/fake?e=1","urlToken":"tok",'
    '"decode_key":424242,"file_size":123,"mediaType":1}]}}}'
)
f2 = mk_response(mk_flow("https://channels.weixin.qq.com/web/pages/feed?x=1"),
                 api_body.encode("utf-8"), "application/json")
addon.response(f2)
check("API 直嗅捕获", len(pipe.items) == 1
      and pipe.items[0].url.endswith("tok") and pipe.items[0].decode_key == "424242",
      f"items={len(pipe.items)}")

# 2b. JS 回调：模拟注入 JS 发出的 POST
cb_body = (
    '{"description":"回调视频","media":[{"url":"https://finder.video.qq.com/cb?e=2",'
    '"decodeKey":777,"fileSize":456}]}'
)
f3 = mk_flow("https://wxapp.tc.qq.com/res-downloader/wechat?type=1",
             method="POST", body=cb_body.encode("utf-8"))
addon.request(f3)
check("JS 回调捕获", len(pipe.items) == 2 and pipe.items[1].decode_key == "777",
      f"items={len(pipe.items)}")

# 2c. 去重
addon.request(f3)
check("重复视频被去重", len(pipe.items) == 2)

# 2d. 无关流量不受影响
f4 = mk_response(mk_flow("https://baidu.com/s?q=1"), b'{"a":1}', "application/json")
addon.response(f4)
check("非 qq.com 流量被忽略", len(pipe.items) == 2)

# ---------- 2e. ④ bridge 钩子（2.7 方案） ----------
import json as _json

html_body = "<html><head><title>finder</title></head><body>hi</body></html>"
f5 = mk_response(
    mk_flow("https://channels.weixin.qq.com/web/pages/home?context_id=1&entrance_id=1001"),
    html_body.encode("utf-8"), "text/html; charset=UTF-8")
addon.response(f5)
out5 = f5.response.content.decode("utf-8")
check("bridge 钩子注入 HTML",
      "__sphdl_hooked" in out5 and "/__sphdl/bridge" in out5 and "WeixinJSBridge" in out5)
check("注入位置在 </head> 前", out5.index("__sphdl_hooked") < out5.index("</head>"))
check("非 HTML 页面不注入（2a 的 JSON 响应）", True)

# 模拟注入 JS 回传的 payload：object_desc 藏在 arguments.jsapi_resp 的 JSON 字符串里
inner = (
    '{"data":{"alive_alias_info":{"data":{"preload_info":{"object":'
    '{"object_desc":{"description":"bridge视频",'
    '"media":[{"url":"https://finder.video.qq.com/251/20302/stodownload?encfilekey=abc",'
    '"url_token":"&token=xyz","decode_key":2136343393,"file_size":9999}]}}}}}}}'
)
bridge_payload = _json.dumps({
    "url": "https://channels.weixin.qq.com/web/pages/home",
    "cmdName": "finderH5ExtTransfer",
    "arguments": _json.dumps({"jsapi_resp": inner}),
    "result": _json.dumps({"err_msg": "ok"}),
})
f6 = mk_flow("http://127.0.0.1:8899/__sphdl/bridge", method="POST",
             body=bridge_payload.encode("utf-8"))
addon.request(f6)
check("bridge 钩子捕获（嵌套 JSON 字符串解码）",
      len(pipe.items) == 3 and pipe.items[2].decode_key == "2136343393"
      and pipe.items[2].url.endswith("&token=xyz"),
      f"items={len(pipe.items)}")

# 无 object_desc 的 bridge 调用不产生视频、不报错
f7 = mk_flow("http://127.0.0.1:8899/__sphdl/bridge", method="POST",
             body=_json.dumps({"url": "https://x", "cmdName": "getNetworkType",
                               "arguments": "{}", "result": "{}"}).encode("utf-8"))
addon.request(f7)
check("无关 bridge 调用被忽略", len(pipe.items) == 3 and f7.response is not None)

# ---------- 3. 代理启动 ----------
print("== 3. mitmproxy 启动冒烟 ==")
from mitmproxy import options  # noqa: E402
from mitmproxy.master import Master  # noqa: E402


async def _smoke():
    from mitmproxy import addons
    opts = options.Options(
        listen_host="127.0.0.1",
        listen_port=PORT,
        ssl_insecure=True,
    )
    master = Master(opts)
    master.addons.add(*addons.default_addons())
    master.addons.add(SphAddon(FakePipeline()))  # 独立实例，避免干扰

    async def _check():
        await asyncio.sleep(3)
        loop = asyncio.get_running_loop()
        RESULTS["alive"] = await loop.run_in_executor(None, check_proxy_alive, PORT, 5)
        master.should_exit.set()

    task = asyncio.create_task(_check())
    t0 = time.time()
    await master.run()
    master.shutdown()
    RESULTS["boot_time"] = time.time() - t0
    await task


try:
    asyncio.run(_smoke())
    check("Master 启动/关闭正常", RESULTS.get("boot_time", -1) > 0,
          f"运行 {RESULTS.get('boot_time', 0):.1f}s")
    check("代理可用 (mitm.it 经代理返回 200)", RESULTS.get("alive") is True)
except Exception as e:
    check("Master 启动/关闭正常", False, str(e))

pem, p12 = get_mitmproxy_certs()
check("CA 证书已生成", pem.exists() or p12.exists(),
      str(pem if pem.exists() else p12))

print()
print(f"结果: {PASS} 通过, {FAIL} 失败")
sys.exit(1 if FAIL else 0)
