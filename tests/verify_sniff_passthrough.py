"""验证「只拦微信、其余透传」+ IPv4 修正（v2.2.4）。

═══ 客户报障（2026-10-23）═══
「启动嗅探后，优酷播不了，视频号也提示『暂无法观看此视频，
  可尝试切换网络或检查是否配置了代理』」
「关闭嗅探后，优酷能播放，视频号也能播放了」

→ 确认代理是根因。两个叠加原因：
  ① mitmproxy 拦截**全系统**流量，视频类大流量走 MITM 容易断
  ② 本机 IPv6 不通，微信域名有 AAAA，mitmproxy 连 IPv6 卡死

═══ 修法 ═══
  · addon.tls_clienthello：非微信域名 → ignore_connection（裸透传）
  · netfix.install_prefer_ipv4()：IPv6 不通时只解析 IPv4

═══ 覆盖 ═══
  P1 白名单判定：微信域名要拦
  P2 白名单判定：优酷/百度/其他 不拦（透传）
  P3 ★透传后非微信站点走代理正常（优酷首页能打开）
  P4 ★微信域名走代理正常（IPv4 修正生效）
  P5 透传不影响嗅探（微信域名仍被 MITM）
"""
import socket
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


print("=" * 76)
print("P1/P2 白名单判定")
print("=" * 76)
from sphdl.addon import SphAddon

a = SphAddon(pipeline=None)
cases = [
    ("mp.weixin.qq.com", True),
    ("channels.weixin.qq.com", True),
    ("res.wx.qq.com", True),
    ("mmbiz.qpic.cn", True),
    ("mpvideo.qpic.cn", True),
    ("finder.video.qq.com", True),
    ("wxapp.tc.qq.com", True),
    ("www.youku.com", False),
    ("v.youku.com", False),
    ("www.baidu.com", False),
    ("v.qq.com", False),          # 腾讯视频，不能拦
    ("www.bilibili.com", False),
    ("github.com", False),
]
def _will_intercept(host):
    """★ v2.2.15：判定顺序改为「先排除、后拦截」。
    只看 _intercept_re 会把 v.qq.com（腾讯视频）误判为拦，
    因为 qq.com 通配会命中它 —— 实际运行时被 PASSTHROUGH 排除了。"""
    if any(rx.search(host) for rx in getattr(a, "_passthrough_re", [])):
        return False
    return any(rx.search(host) for rx in a._intercept_re)


for host, want in cases:
    hit = _will_intercept(host)
    mark = "拦" if hit else "透传"
    print(f"  {host:26s} → {mark}")
    check(f"P1/P2 {host}", hit == want, f"期望{'拦' if want else '透传'}，实际{mark}")

print()
print("=" * 76)
print("启动嗅探引擎（真进程内）")
print("=" * 76)
import tkinter as tk
import sphdl.gui as G

app = G.App()
app.root.withdraw()
app.root.update()
eng = app.engine
# ★ 必须用独立端口：客户可能正在运行客户端（占 8899），
#   否则测试请求会走到那个实例上，导致 P5b 之类的判据失真
TEST_PORT = 18898
eng.start(TEST_PORT, auto_proxy=False)     # 不自改系统代理，只测代理本身
for _ in range(40):
    time.sleep(0.3)
    app.root.update()
    if eng.running and eng._port_open(TEST_PORT):
        break
port_ok = eng._port_open(TEST_PORT)
print(f"  端口 {TEST_PORT} 就绪: {port_ok}")
check("引擎启动", port_ok)

if port_ok:
    proxy = f"http://127.0.0.1:{TEST_PORT}"

    def via_proxy(url, timeout=15):
        op = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        t0 = time.time()
        try:
            r = op.open(url, timeout=timeout)
            b = r.read(300)
            return True, r.status, len(b), time.time() - t0
        except Exception as e:
            return False, type(e).__name__, str(e)[:60], time.time() - t0

    print()
    print("=" * 76)
    print("P3 非微信站点（应透传，正常打开）")
    print("=" * 76)
    ok3 = True
    for url in ("https://www.youku.com", "https://www.baidu.com",
                "https://www.bilibili.com"):
        good, a1, a2, dt = via_proxy(url)
        # ★ B站对脚本请求回 412 反爬 —— 能收到 HTTP 响应就说明透传通了
        reached = good or (isinstance(a1, str) and a1 == "HTTPError")
        print(f"  {url:32s} → {'✅' if reached else '❌'} {a1} {dt:.2f}s")
        if not reached:
            ok3 = False
    check("P3 非微信站点走代理正常（透传生效）", ok3,
          "失败说明还在被 MITM")

    print()
    print("=" * 76)
    print("P4 微信域名（应 MITM，且 IPv4 修正生效）")
    print("=" * 76)
    ok4 = True
    for url in ("https://mp.weixin.qq.com",
                "https://channels.weixin.qq.com"):
        good, a1, a2, dt = via_proxy(url)
        print(f"  {url:36s} → {'✅' if good else '❌'} {a1} {dt:.2f}s")
        if not good:
            ok4 = False
    check("P4 微信域名走代理正常（IPv6 修正生效）", ok4,
          "超时说明 IPv6 还在卡")

    print()
    print("=" * 76)
    print("P5 微信域名仍被拦截（嗅探能力没丢）")
    print("=" * 76)
    # ★ 必须看引擎实际挂载的 addon（app.addon），测试里 new 的那个没被调用过
    live_addon = getattr(app, "addon", None) or a
    passthrough_hosts = getattr(live_addon, "_passthrough_logged", set())
    print(f"  已记录透传的域名数: {len(passthrough_hosts)}")
    print(f"  样例: {list(passthrough_hosts)[:5]}")
    hostile_intercepted = not any(
        "weixin" in h or "qpic" in h for h in passthrough_hosts)
    check("P5a 微信域名没被透传（仍会 MITM）", hostile_intercepted,
          str([h for h in passthrough_hosts if "weixin" in h]))
    check("P5b 确实记录了非微信域名透传", len(passthrough_hosts) > 0,
          f"{len(passthrough_hosts)} 个")

    try:
        eng.stop()
    except Exception:
        pass
else:
    for nm in ("P3 非微信站点走代理正常（透传生效）",
               "P4 微信域名走代理正常（IPv6 修正生效）"):
        check(nm, False, "引擎未就绪")

print()
try:
    app.root.destroy()
except Exception:
    pass
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
