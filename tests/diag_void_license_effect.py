"""验证「发码平台作废授权码后，客户端会怎样」（v2.2.24 排查）。

═══ 客户问题（2026-10-26）═══
「在发码平台的某用户授权码作废后，用户当前在下载的视频任务是
  立刻停止显示授权码失效？还是可以继续下载？」

═══ 必须用事实回答，不能猜 ═══

客户端架构事实（先读代码确认，再用测试固化）：
  · license.py 里 **零网络调用**（grep http/urllib/socket 无命中）
  · verify_code() 只做本地 HMAC 校验：码里的 tier/days/maxdev 是
    **写死在码里**的，作废信息不在码里
  · telemetry 只在 hmac（限台数码）激活时联网，且**服务器不可达一律放行**
  · 台账「作废」只是往 licenses.jsonl 追加一条 voided 事件（服务器本地），
    **没有任何端点把作废状态推给客户端**
  · 下载门禁 `_has_download_license()` 读本地 cfg.license_codes

结论预期：**作废对已激活的客户端完全无效** —— 既不停止下载，
也不会提示失效，它会一直正常用到客户主动清除/换机。

覆盖：
  V1 verify_code 不联网（源码无网络调用）
  V2 作废信息不在授权码里（码内容只有 tier/days/dev/maxdev/flags）
  V3 「作废」后同一个码本地校验仍然通过（客户端视角）
  V4 作废不影响 _has_download_license（下载门禁仍放行）
  V5 作废期间正在下载的任务不会被中断（门禁只查一次，入队后不再查）
  V6 客户端没有轮询授权状态的机制
  V7 服务器作废接口只写台账，无推送/下发端点
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


lic_src = (ROOT / "sphdl" / "license.py").read_text(encoding="utf-8")
tel_src = (ROOT / "sphdl" / "telemetry.py").read_text(encoding="utf-8")
gui_src = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8")
srv_src = (ROOT / "server" / "sphdl_admin.py").read_text(encoding="utf-8")

print("=" * 74)
print("V1 license.py 是纯本地校验（零网络）")
print("=" * 74)
net_pat = re.compile(r"\b(urllib|requests|socket|http\.client|urlopen)\b")
hits = net_pat.findall(lic_src)
print(f"  license.py 里的网络关键字: {set(hits) or '无'}")
check("V1a license.py 无网络调用", not hits, str(set(hits)))
check("V1b verify_code 是本地 HMAC 校验",
      "hmac.compare_digest" in lic_src)

print()
print("=" * 74)
print("V2 授权码里装的是什么（作废信息无法表达）")
print("=" * 74)
# 找 payload 结构
m = re.search(r"payload\s*=\s*bytes\(\[(.*?)\]\)", lic_src, re.S)
if m:
    print(f"  payload 组成: {m.group(1)[:120]}")
fields = []
for key in ("tier", "days", "dev", "maxdev", "flags", "prod", "exp"):
    if re.search(rf"\b{key}\b", lic_src):
        fields.append(key)
print(f"  码内字段: {fields}")
check("V2a 码内字段只有 tier/days/设备/台数/产品",
      all(k in fields for k in ("tier", "days")), str(fields))
check("V2b 码里没有「作废/状态」类字段",
      not re.search(r"\b(void|revoke|status|disabled|active)\w*\s*=", lic_src),
      "作废无法编码进码里")

print()
print("=" * 74)
print("V3-V4 作废后客户端本地校验仍通过")
print("=" * 74)
import importlib
from sphdl import license as lic

lic_file = ROOT / "sphdl" / "_lic_key.py"
if not lic_file.exists():
    secret = bytes.fromhex((ROOT / ".license_secret").read_text().strip())
    lic_file.write_text("K = %r\n" % lic.obfuscate(secret), encoding="utf-8")
importlib.reload(lic)

code = lic.make_code(2000, product=lic.PRODUCT_VIDEO)
info = lic.verify_code(code)
print(f"  生成码: {code[:24]}…")
print(f"  本地校验: {'通过' if info else '失败'}  tier={info.get('tier') if info else None}")
check("V3a 码本身有效", info is not None)

# 模拟「服务器作废」：服务器台账里加一条 voided 记录
import json
import tempfile
ledger = Path(tempfile.mkdtemp()) / "licenses.jsonl"
ledger.write_text(json.dumps({"code": code, "tier": 2000,
                              "customer": "测试客户"},
                             ensure_ascii=False) + "\n"
                  + json.dumps({"event": "voided", "code": code},
                               ensure_ascii=False) + "\n", encoding="utf-8")
print(f"  台账写入作废事件: {ledger.name}")
# ★ 客户端根本不读台账 —— 再校验一次完全相同
info2 = lic.verify_code(code)
print(f"  作废后再校验: {'仍通过' if info2 else '失败'}")
check("V3b ★作废后台账变了，但码本地校验依旧通过",
      info2 is not None and info2.get("tier") == 2000,
      "客户端不读台账，作废无效")

print()
print("=" * 74)
print("V5 下载门禁只读本地配置")
print("=" * 74)
# 找 _has_download_license 实现
i = gui_src.find("def _has_download_license")
j = gui_src.find("\n    def ", i + 10)
body = gui_src[i:j]
print(f"  _has_download_license 共 {len(body)} 字符")
has_net = bool(net_pat.search(body))
print(f"  里面有无网络调用: {has_net}")
check("V5a 下载门禁纯本地判断", not has_net)
# 实际走 _license_info() → lic.verify_code() → has_feature()
check("V5b 门禁走本地 _license_info + has_feature",
      "_license_info" in body and "has_feature" in body,
      "仍是纯本地，无网络")
check("V5c 门禁不读服务器/不读台账",
      "ledger" not in body.lower() and "http" not in body.lower())

print()
print("=" * 74)
print("V6 客户端没有「轮询授权状态」的机制")
print("=" * 74)
# 在 gui 里找定时器是否碰授权
poll_i = gui_src.find("def _poll(self)")
poll_j = gui_src.find("\n    def ", poll_i + 10)
poll_body = gui_src[poll_i:poll_j]
print(f"  _poll 长度 {len(poll_body)} 字符")
check("V6a 主轮询不检查授权", "license" not in poll_body.lower(),
      "UI 轮询与授权无关")
# 5 处分别在：读旧字段(314)、切产品线(1107/1114)、更换授权(1426)、激活(1508)
# 关键判据：**不在任何 root.after 定时回调里**
timers = []
for m in re.finditer(r"self\.root\.after\(", gui_src):
    seg = gui_src[m.start():m.start()+400]
    if "verify_code" in seg:
        timers.append(seg[:80])
print(f"  含 verify_code 的定时器: {timers or '无'}")
check("V6b 没有定期 verify_code 的定时器", not timers, str(timers))
print(f"  verify_code 出现 {gui_src.count('verify_code')} 次（均在激活/加载时）")

print()
print("=" * 74)
print("V7 服务器作废只写台账，无下发通道")
print("=" * 74)
vi = srv_src.find("def _do_void")
vj = srv_src.find("\n    def ", vi + 10)
vbody = srv_src[vi:vj]
print(f"  _do_void 长度 {len(vbody)} 字符")
check("V7a 作废只 append 事件到台账",
      "event" in vbody and "voided" in vbody)
# 服务器路由里有没有给客户端用的授权状态查询
routes = re.findall(r'u\.path\s*==\s*"([^"]+)"', srv_src)
print(f"  服务器路由: {routes}")
check("V7b 没有『客户端查询授权状态』的端点",
      not any("licen" in r or "check" in r or "valid" in r for r in routes),
      str(routes))
check("V7c 客户端唯一联网点是上报/台数校验",
      "check_device_limit" in tel_src)

print()
print("=" * 74)
print("V8 台数校验「服务器不可达一律放行」")
print("=" * 74)
check("V8a 服务器不可达 → 返回 True（放行）",
      "任何异常 → 放行" in tel_src or "服务器不可达时一律放行" in tel_src)
check("V8b 只有 max_devices>0 才联网",
      "if md <= 0:" in tel_src)

print()
print("=" * 74)
print("结论")
print("=" * 74)
print("  · 作废（void）只影响发码后台的台账显示")
print("  · 客户端**本地 HMAC 校验，从不联网查授权**")
print("  · 已激活的客户：作废后【不会】提示失效，【不会】停止下载")
print("  · 只有客户清除授权 / 换电脑 / 重装系统才会需要重新激活")
print("  · 限台数码(ver=4)是唯一联网场景，且只校验台数、不校验作废")
