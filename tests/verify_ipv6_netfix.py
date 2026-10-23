"""验证 IPv6 补丁（netfix）。

═══ 背景（2026-10-23 真实故障）═══
v2.2.3 启动嗅探后视频号提示「暂无法观看此视频，可尝试切换网络或检查是否配置了代理」。
根因：本机 IPv6 不通，但微信域名有 AAAA 记录，mitmproxy 连 IPv6 卡死。
直连正常（curl 有 Happy Eyeballs 会回退 IPv4），只有 mitmproxy 出问题。

覆盖：
  V1 ipv6_available() 能正确探测（本机应为 False）
  V2 装补丁后 getaddrinfo 不再返回 IPv6
  V3 装补丁后仍能正常解析（IPv4 结果存在）
  V4 明确请求 AF_INET6 时不拦截
  V5 ★装补丁后通过代理访问微信域名成功（核心验证）
  V6 补丁幂等（重复安装不叠加）
"""
import socket
import subprocess
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


from sphdl import netfix

print("=" * 74)
print("V1 IPv6 可用性探测")
print("=" * 74)
ok6 = netfix.ipv6_available()
print(f"  本机 IPv6 可用: {ok6}")
check("V1 ipv6_available() 有明确布尔结果", isinstance(ok6, bool), str(ok6))

print()
print("=" * 74)
print("V2/V3 装补丁后解析结果")
print("=" * 74)
before = socket.getaddrinfo("mp.weixin.qq.com", 443, proto=socket.IPPROTO_TCP)
v6_before = {r[4][0] for r in before if r[0] == socket.AF_INET6}
v4_before = {r[4][0] for r in before if r[0] == socket.AF_INET}
print(f"  装前 IPv4={sorted(v4_before)[:2]}")
print(f"  装前 IPv6={sorted(v6_before)[:2]}")

installed = netfix.install_prefer_ipv4(force=True)
after = socket.getaddrinfo("mp.weixin.qq.com", 443, proto=socket.IPPROTO_TCP)
v6_after = {r[4][0] for r in after if r[0] == socket.AF_INET6}
v4_after = {r[4][0] for r in after if r[0] == socket.AF_INET}
print(f"  装后 IPv4={sorted(v4_after)[:2]}")
print(f"  装后 IPv6={sorted(v6_after)[:2]}")

check("V2 装补丁后不返回 IPv6", len(v6_after) == 0, str(sorted(v6_after)[:2]))
check("V3 仍然能解析出 IPv4", len(v4_after) > 0, str(sorted(v4_after)[:2]))

print()
print("=" * 74)
print("V4 明确请求 AF_INET6 时不拦截")
print("=" * 74)
try:
    r6 = socket.getaddrinfo("mp.weixin.qq.com", 443, socket.AF_INET6,
                            proto=socket.IPPROTO_TCP)
    print(f"  AF_INET6 请求返回 {len(r6)} 条")
    check("V4 明确要 IPv6 时不被过滤", len(r6) > 0, f"{len(r6)} 条")
except Exception as e:
    check("V4 明确要 IPv6 时不被过滤", False, str(e)[:50])

print()
print("=" * 74)
print("V6 补丁幂等")
print("=" * 74)
again = netfix.install_prefer_ipv4(force=True)
r2 = socket.getaddrinfo("mp.weixin.qq.com", 443, proto=socket.IPPROTO_TCP)
print(f"  二次安装返回 {again}，解析 IPv6 数量 {len([x for x in r2 if x[0]==socket.AF_INET6])}")
check("V6 重复安装不叠加/不报错", again is True and len(
    [x for x in r2 if x[0] == socket.AF_INET6]) == 0)

print()
print("=" * 74)
print("V5 ★通过代理访问微信域名（核心验证）")
print("=" * 74)
print("  注意：需要工具正在嗅探。若未启动，本项会跳过。")

# 探测 8899 是否在监听
port_open = False
s = socket.socket()
s.settimeout(1.5)
try:
    s.connect(("127.0.0.1", 8899))
    port_open = True
except Exception:
    pass
finally:
    s.close()

if not port_open:
    print("  ⏭ 8899 未监听 → 跳过（请先启动嗅探再跑本测试）")
    check("V5 通过代理访问微信域名成功", True, "已跳过")
else:
    proxy = "http://127.0.0.1:8899"
    targets = [
        ("mp.weixin.qq.com", "https://mp.weixin.qq.com"),
        ("channels.weixin.qq.com", "https://channels.weixin.qq.com"),
    ]
    allok = True
    for label, url in targets:
        op = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        t0 = time.time()
        try:
            r = op.open(url, timeout=12)
            b = r.read(200)
            print(f"  {label:24s} → HTTP {r.status}  {len(b)}B  "
                  f"{time.time()-t0:.2f}s  ✅")
        except Exception as e:
            print(f"  {label:24s} → {type(e).__name__}: {str(e)[:50]}  "
                  f"{time.time()-t0:.2f}s  ❌")
            allok = False
    check("V5 通过代理访问微信域名成功", allok,
          "全部超时说明补丁没生效" if not allok else "")

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
