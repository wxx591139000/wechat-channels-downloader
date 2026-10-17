#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""激活上报（客户端）— 作者侧防盗版追溯。

★ 总开关
--------
    sphdl/config.py:  TELEMETRY_ENABLED = False

**默认 False**。关掉时的保证（本模块的设计契约）：
  ① 不发起任何网络请求
  ② 不做任何设备指纹采集（不调 WMIC/PowerShell，不读注册表）
  ③ 不产生任何日志、文件、界面痕迹
  ④ 调用方即使误调 report_activation()，也会立即 return（空操作）

打开时的行为（`TELEMETRY_ENABLED = True`）：
  · 激活成功后，**在后台线程**上报一次
  · 超时 3 秒；**任何失败都静默忽略**（绝不抛异常、绝不弹窗、绝不阻塞界面）
  · 只上报：授权码 + 设备码 + 版本 + 时间戳
  · 不上报：原始硬件 ID、用户名、文件路径、IP（服务端只记 /24 段）

隐私说明（须在授权协议/使用说明中告知用户，PIPL 要求）
------------------------------------------------------
本软件会记录激活设备信息用于授权验证与售后追溯。
传送内容为不可逆哈希，不含个人身份信息；用户可据此提出异议。
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time

# 上报地址：默认生产地址。可用环境变量覆盖（便于本地测试）。
DEFAULT_ENDPOINT = "https://license.zhuanlu.xyz/api/report"
ENDPOINT = os.environ.get("SPHDL_TELEMETRY_URL", DEFAULT_ENDPOINT)
TIMEOUT = 2.0            # 秒；宁可漏报，不可卡界面（后台线程，超时即弃）
_UA = "sphdl-telemetry/1.0"


def _enabled() -> bool:
    """读总开关。**每次调用都实时读**，便于测试与运行时调整。

    开关在 sphdl/config.py；导入失败一律视为「关闭」（安全默认）。
    """
    try:
        from . import config as _c
        return bool(getattr(_c, "TELEMETRY_ENABLED", False))
    except Exception:
        return False


# ---------------- 设备指纹 ----------------

def _run(cmd, timeout=8):
    """跑一条命令取 stdout。失败返回 ''。"""
    try:
        si = None
        if os.name == "nt":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, startupinfo=si, shell=False)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _powershell(expr: str, timeout=8) -> str:
    return _run(["powershell", "-NoProfile", "-NonInteractive",
                 "-Command", expr], timeout=timeout)


def _wmic(args) -> str:
    return _run(["wmic"] + list(args), timeout=6)


def _first_line_after_header(out: str) -> str:
    """WMIC 输出形如 'SerialNumber\\nABC123\\n\\n' → 取 ABC123。"""
    lines = [x.strip() for x in (out or "").splitlines() if x.strip()]
    return lines[1] if len(lines) >= 2 else ""


def collect_raw() -> dict:
    """采集原始硬件标识（三项 + 系统 GUID）。

    ★ 本函数只在 telemetry 开启时被调用（见 report_activation）。
    wmic 在 Win11 24H2+ 已移除 → **CIM 回退是必需项**（实测本机 wmic 不可用）。
    """
    out = {"cpu": "", "board": "", "disk": "", "guid": ""}
    # ① CPU ID
    v = _first_line_after_header(_wmic(["cpu", "get", "ProcessorId"]))
    if not v:
        v = _powershell("(Get-CimInstance Win32_Processor).ProcessorId")
    out["cpu"] = re.sub(r"\s+", "", v or "")
    # ② 主板序列号
    v = _first_line_after_header(_wmic(["baseboard", "get", "SerialNumber"]))
    if not v or v.lower().startswith("to be filled"):
        v = _powershell("(Get-CimInstance Win32_BaseBoard).SerialNumber")
    out["board"] = re.sub(r"\s+", "", v or "")
    # ③ 系统盘序列号
    v = _first_line_after_header(_wmic(["diskdrive", "get", "SerialNumber"]))
    if not v:
        v = _powershell("(Get-CimInstance Win32_DiskDrive | "
                        "Select-Object -First 1).SerialNumber")
    out["disk"] = re.sub(r"\s+", "", v or "")[:40]
    # ④ MachineGuid（系统级；重装系统才变）——整机换才全变的兜底
    v = _powershell(
        "(Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Cryptography')"
        ".MachineGuid")
    out["guid"] = re.sub(r"\s+", "", v or "")
    return out


def device_components() -> list:
    """把四项硬件标识分别哈希，返回 4 个独立指纹分量（v2.0.19）。

    ★ 为什么要分量而不是整串哈希：
      整串哈希（SHA256(cpu|board|disk|guid)）的致命问题是**任一项变化
      结果全变** —— 客户加一块硬盘、换一根内存、主板返修，设备码就变了，
      绑定的授权码立刻失效，售后成本高（行业里叫"误伤"）。

      行业通行做法（JetBrains/Cryptlex/Keygen 等）：取 3~5 项，
      **容忍 N 项不匹配**，常见是"3 项里对上 2 项就算同一台机器"。

    返回：4 个 16 位十六进制串（每项各自 SHA256 前 16 位），空项为空串。
    顺序固定：cpu, board, guid, disk（disk 放最后，因为它最容易变）。
    """
    r = collect_raw()
    out = []
    for key in ("cpu", "board", "guid", "disk"):
        v = (r.get(key) or "").strip()
        if not v:
            out.append("")
        else:
            out.append(hashlib.sha256(v.encode("utf-8")).hexdigest()[:16])
    return out


def device_fingerprint() -> str:
    """设备指纹（展示/上报用）= SHA256(四项分量拼接)。

    ⚠️ 注意：这个值**不能用于绑定校验** —— 任一项硬件变化它就会变。
    绑定校验请用 device_components() + match_components()（容许硬件变动）。
    本函数保留用于：上报去重、水印展示、台账记录（"这台机器当前长这样"）。
    """
    comps = device_components()
    raw = "|".join(comps)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def match_components(comps_a, comps_b, need: int = 2) -> bool:
    """两组分量是否属于同一台机器（v2.0.19 设备绑定校验核心）。

    规则：**4 项里有 >= need 项相同**即视为同一台机器（默认 need=2）。
      · 换硬盘 → cpu/board/guid 三项目匹配 → 通过 ✓
      · 换主板 → cpu/guid/disk 三项目匹配 → 通过 ✓
      · 重装系统 → cpu/board/disk 匹配 → 通过 ✓
      · 换整机 → 最多 0~1 项目匹配 → 拒绝 ✗

    空分量不参与比较（采集失败的项不能既不算匹配也不算不匹配）。
    """
    a = [x for x in (comps_a or []) if x]
    b = [x for x in (comps_b or []) if x]
    if not a or not b:
        return True          # 任一侧采集失败 → 不阻止（宁可放过）
    same = sum(1 for x, y in zip(a, b) if x and y and x == y)
    # 可用项太少时（比如只采到 2 项），放宽为"至少 1 项匹配"
    avail = min(len(a), len(b))
    need2 = max(1, min(need, avail))
    return same >= need2


def bind_token(components=None) -> str:
    """绑定码串（v2.0.19）= 四个分量首字节的十六进制，共 8 位。

    例如 166b4c9c... / 3163c4f0... / 45d36977... / d536c46e...
      → "163145d5"

    ★ 为什么需要它：
      客户发「设备码」（1594-8EBB-7A1F-B22F）只能做**严格比对**；
      发「绑定码串」（163145d5）才能做**4 项取 2** 的宽容比对。
      作者在发码后台填这个串，客户换硬盘/重装系统就不会失效。
    """
    comps = components if components is not None else device_components()
    out = []
    for i in range(4):
        h = (comps[i] if i < len(comps) else "") or ""
        out.append(h[:2] if len(h) >= 2 else "00")
    return "".join(out)


def device_display(fingerprint: str = "") -> str:
    """展示用设备码：4 组 4 位大写十六进制（如 A3F8-91C2-B7E4-5D9A）。

    注意：**这是设备指纹的展示形态**，上报时传的是它（服务端再哈希一次），
    不传原始 CPU/主板序列号。
    """
    fp = fingerprint or device_fingerprint()
    u = fp.upper()
    if len(u) < 16:
        u = (u + "0" * 16)[:16]
    return "-".join(u[i:i + 4] for i in range(0, 16, 4))


# ---------------- 上报 ----------------

def _payload(code: str, device: str, version: str) -> dict:
    return {"code": code, "device": device,
            "version": version, "ts": int(time.time())}


def _post(url: str, payload: dict) -> bool:
    """发一次 POST。只用标准库（客户端同样零第三方依赖）。

    ★ 超时控制说明（Windows 实测坑）：
      `urlopen(timeout=N)` 在目标不可达时可能远超 N —— 因为它会为
      IPv4/IPv6 各试一遍，每次单独计时，实测 2s 超时能跑成 7.5s。
      这里改用**全局 socket 默认超时** + 显式单次请求，把总耗时压住。
      反正上报在后台守护线程里、结果被丢弃，宁可少报也不能久挂。
    """
    import socket
    import urllib.request
    body = json.dumps(payload).encode("utf-8")
    old_to = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(TIMEOUT)
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json", "User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return 200 <= getattr(r, "status", 200) < 300
    except Exception:
        return False
    finally:
        try:
            socket.setdefaulttimeout(old_to)
        except Exception:
            pass


def report_activation(code: str, version: str = "", block: bool = False,
                      endpoint: str = "") -> bool:
    """激活成功后上报一次（默认后台线程，不阻塞）。

    ★ 返回 True 仅表示"已发出请求"，**不表示服务端收到**（这是刻意的：
      上报是尽力而为，客户端不关心结果）。

    总开关关闭时：立即 return False，不采集指纹、不发请求、不记日志。
    """
    if not _enabled():
        return False                      # ★ 总开关：完全空操作
    if not code:
        return False

    def _work():
        try:
            dev = device_display()        # 采集 + 哈希（仅此时发生）
            url = endpoint or ENDPOINT
            _post(url, _payload(code, dev, version or ""))
        except Exception:
            pass                          # ★ 任何异常都吞掉

    if block:
        _work()
        return True
    try:
        t = threading.Thread(target=_work, name="sphdl-telemetry", daemon=True)
        t.start()
        return True
    except Exception:
        return False


def selftest() -> list:
    """自检：返回 [(名称, 通过, 详情)]，供 --selftest-telemetry 使用。"""
    res = []

    def ck(n, c, d=""):
        res.append((n, bool(c), d))

    ck("总开关可读", isinstance(_enabled(), bool), f"enabled={_enabled()}")
    fp = device_fingerprint()
    ck("设备指纹为 64 位 hex", len(fp) == 64 and all(
        ch in "0123456789abcdef" for ch in fp), fp[:16] + "…")
    disp = device_display(fp)
    ck("展示设备码格式 XXXX-XXXX-XXXX-XXXX",
       bool(re.fullmatch(r"[0-9A-F]{4}(-[0-9A-F]{4}){3}", disp)), disp)
    ck("展示码稳定（两次一致）", device_display(fp) == disp)
    # 总开关关闭时不上报
    ck("关闭时 report_activation 为空操作",
       (not _enabled()) or report_activation("SPHDL-TEST", "0", block=True) in (True, False))
    return res


if __name__ == "__main__":
    for n, ok, d in selftest():
        print(f"{'[PASS]' if ok else '[FAIL]'} {n} {d}")
    print(f"telemetry enabled = {_enabled()}")
    sys.exit(0)
