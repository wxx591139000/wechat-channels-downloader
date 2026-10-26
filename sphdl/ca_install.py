"""CA 证书安装（不经批处理，直接在程序内完成）。

═══ 为什么要有（2026-10-25 客户事故）═══

3 个客户报障：「装不上 CA 证书，点安装时命令窗口闪退」。
其中一位自己摸到办法：手动进 certmgr 装上了。

根因：`install-cert.bat` 被存成 **LF 换行**（不是 CRLF），
cmd 无法解析 → `cd /d "%~dp0"` 报「系统找不到指定的路径」
→ 脚本 0.03 秒退出 → 窗口一闪就没。
影响 v2.2.2 ~ v2.2.16 全部已发版本。

**本模块的意义：让装证书不再依赖那个 .bat。**
· 用户根证书库（`-user Root`）**不需要管理员权限**
· 直接在进程内调 certutil，拿到真实错误码，能回显给客户
· 装完立刻校验，结果写日志

批处理保留作为兜底（有些人习惯双击）。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def find_ca_file() -> Path | None:
    """找 mitmproxy 的 CA 证书文件（公共部分，不含私钥）。

    注意：必须用 `mitmproxy-ca-cert.pem` / `.cer`，
    **不能用 `mitmproxy-ca.pem`**（含私钥，certutil 会拒绝导入）。
    """
    home = Path.home() / ".mitmproxy"
    for name in ("mitmproxy-ca-cert.pem", "mitmproxy-ca-cert.cer",
                 "mitmproxy-ca-cert.crt"):
        p = home / name
        if p.exists():
            return p
    return None


def _run(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    """跑命令，返回 (返回码, 输出文本)。不弹窗。"""
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           creationflags=flags)
        out = b""
        out += p.stdout or b""
        out += p.stderr or b""
        for enc in ("gbk", "utf-8"):
            try:
                return p.returncode, out.decode(enc)
            except Exception:
                continue
        return p.returncode, out.decode("utf-8", "replace")
    except FileNotFoundError:
        return -1, "找不到 certutil 命令（系统组件缺失）"
    except subprocess.TimeoutExpired:
        return -2, "certutil 执行超时"
    except Exception as e:                      # noqa: BLE001
        return -3, f"{type(e).__name__}: {e}"


def is_installed() -> bool:
    """用户根证书库里有 mitmproxy 证书吗。"""
    rc, out = _run(["certutil", "-store", "-user", "Root"])
    if rc != 0:
        return False
    return "mitmproxy" in out.lower()


def install() -> tuple[bool, str]:
    """把 CA 装进「当前用户 → 受信任的根证书颁发机构」。

    返回 (成功?, 给用户看的说明)。**不需要管理员权限**。
    """
    ca = find_ca_file()
    if ca is None:
        return False, ("还没生成证书文件。请先点【启动嗅探】，等 3~5 秒"
                       "（程序会自动生成证书），再回来点【安装/检测】。")

    if is_installed():
        return True, "证书已经装好了，无需重复安装。"

    # ① 先导入「个人」存储（有些环境只认这步走到位）
    _run(["certutil", "-addstore", "-user", "My", str(ca)])

    # ② 关键：导入「受信任的根证书颁发机构」
    rc, out = _run(["certutil", "-addstore", "-user", "Root", str(ca)])
    ok = is_installed()
    if ok:
        return True, "证书安装成功，现在可以正常嗅探了。"

    # 失败 → 把真实原因回给客户（而不是让他看闪退窗口）
    hint = ""
    low = (out or "").lower()
    if "拒绝" in out or "denied" in low or "0x80070005" in low:
        hint = "（系统拒绝了写入，可能被杀毒软件拦截）"
    elif rc != 0:
        hint = f"（certutil 返回码 {rc}）"
    detail = (out or "").strip().splitlines()
    tail = detail[-1][:120] if detail else "无输出"
    return False, f"自动安装没成功{hint}：{tail}"


def open_wizard(bat_path: Path) -> tuple[bool, str]:
    """兜底：打开批处理向导（可见窗口，客户能看到过程）。"""
    try:
        # os.startfile 会正常关联 .bat 并给一个**可见的控制台窗口**；
        # 之前用 Popen(shell=True) 在某些环境会闪一下就没了。
        os.startfile(str(bat_path))   # noqa: S606
        return True, ""
    except Exception as e:                       # noqa: BLE001
        try:
            subprocess.Popen(["cmd", "/c", "start", "",
                              str(bat_path)],
                             cwd=str(bat_path.parent))
            return True, ""
        except Exception as e2:                  # noqa: BLE001
            return False, f"{type(e).__name__}: {e} / {type(e2).__name__}: {e2}"
