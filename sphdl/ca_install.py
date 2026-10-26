"""CA 证书安装（静默，无窗口，无确认框）。

═══ 两次踩坑记录 ═══

【坑 1｜2026-10-25 客户事故】`install-cert.bat` 是 LF 换行，
cmd 解析失败 → 0.03 秒退出 → 客户看到「命令窗口闪退」。
3 个客户报障，2 个退款。

【坑 2｜同日自查发现】改成进程内 `certutil -addstore -user Root` 后，
**未安装状态**下实测返回：

    返回码 2147943623（0x800704B7 ERROR_CANCELLED）
    「CertUtil: 操作已被用户取消。」

根因：把证书写进**根证书库**会触发 Windows 的安全确认框
（"你即将安装来自以下位置的证书…是否要安装？"）。
我们用 `CREATE_NO_WINDOW` 起进程 → 确认框弹不出来 → 系统自动取消。
耗时 8.24 秒（在等一个永远不会出现的对话框）。

**为什么之前没发现**：本机证书早就装好了，`install()` 走的是
「已安装 → 直接返回成功」的短路分支，**从来没真正验证过首装路径**。
这正是 skill 里写的「自证循环」陷阱。

═══ 现在的做法 ═══

用 .NET `X509Store` 直接写库（不走 certutil，不触发确认框）：

```powershell
$c = New-Object X509Certificate2(<file>)
$s = New-Object X509Store("Root","CurrentUser")
$s.Open("ReadWrite"); $s.Add($c); $s.Close()
```

它是进程内 API 调用，**不弹任何窗口**。

策略顺序：
  1. 已安装 → 直接成功（幂等）
  2. X509Store 写 Root（静默、快）
  3. 校验真的生效
  4. 失败才退回 certutil（可能弹框，但至少给客户明确原因）
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_ca_file() -> Path | None:
    """找 mitmproxy 的 CA 证书文件（公共部分，不含私钥）。"""
    home = Path.home() / ".mitmproxy"
    for name in ("mitmproxy-ca-cert.cer", "mitmproxy-ca-cert.pem",
                 "mitmproxy-ca-cert.crt"):
        p = home / name
        if p.exists():
            return p
    return None


def _run(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    """跑命令，返回 (返回码, 输出)。始终不弹窗口。"""
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           creationflags=_NO_WINDOW)
        raw = (p.stdout or b"") + (p.stderr or b"")
        for enc in ("gbk", "utf-8"):
            try:
                return p.returncode, raw.decode(enc)
            except Exception:
                continue
        return p.returncode, raw.decode("utf-8", "replace")
    except FileNotFoundError:
        return -1, "找不到命令"
    except subprocess.TimeoutExpired:
        return -2, "执行超时"
    except Exception as e:                      # noqa: BLE001
        return -3, f"{type(e).__name__}: {e}"


def _hidden_powershell(script: str, timeout: int = 45) -> tuple[int, str]:
    """静默跑一段 PowerShell（无窗口）。"""
    return _run(["powershell", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-Command", script],
                timeout=timeout)


def _ps_quote(s: str) -> str:
    """单引号包裹并转义（PowerShell 单引号内 '' 表示一个 '）。"""
    return "'" + str(s).replace("'", "''") + "'"


def _add_to_store(ca: Path, store: str = "Root") -> tuple[bool, str]:
    """用 .NET X509Store 静默把证书写进「当前用户」的指定存储。

    ★ 不弹确认框 —— 这是相对 certutil 的关键优势。
    """
    script = (
        "$ErrorActionPreference='Stop';"
        f"$c=New-Object System.Security.Cryptography.X509Certificates.X509Certificate2({_ps_quote(str(ca))});"
        f"$s=New-Object System.Security.Cryptography.X509Certificates.X509Store({_ps_quote(store)},'CurrentUser');"
        "$s.Open('ReadWrite');"
        "$s.Add($c);"
        "$s.Close();"
        "Write-Output ('OK ' + $c.Thumbprint)"
    )
    rc, out = _hidden_powershell(script)
    ok = rc == 0 and "OK " in out
    return ok, out.strip()


def _remove_from_store(ca: Path, store: str = "Root") -> tuple[bool, str]:
    """从「当前用户」指定存储里删掉这张证书（卸载/重装用）。"""
    script = (
        "$ErrorActionPreference='Stop';"
        f"$c=New-Object System.Security.Cryptography.X509Certificates.X509Certificate2({_ps_quote(str(ca))});"
        f"$s=New-Object System.Security.Cryptography.X509Certificates.X509Store({_ps_quote(store)},'CurrentUser');"
        "$s.Open('ReadWrite');"
        "$s.Remove($c);"
        "$s.Close();"
        "Write-Output 'REMOVED'"
    )
    rc, out = _hidden_powershell(script)
    return rc == 0 and "REMOVED" in out, out.strip()


def is_installed() -> bool:
    """用户根证书库里有没有 mitmproxy 证书。

    ★ 用 PowerShell 读 .NET X509Store（不启 certutil，更快更稳），
      失败时退回 certutil。
    """
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$s=New-Object System.Security.Cryptography.X509Certificates.X509Store('Root','CurrentUser');"
        "$s.Open('ReadOnly');"
        "$n=($s.Certificates | Where-Object { $_.Subject -like '*mitmproxy*' }).Count;"
        "$s.Close();"
        "Write-Output ('COUNT ' + $n)"
    )
    rc, out = _hidden_powershell(script, timeout=30)
    if rc == 0 and "COUNT " in out:
        try:
            return int(out.split("COUNT ", 1)[1].strip().split()[0]) > 0
        except Exception:
            pass
    # 兜底：certutil
    rc2, out2 = _run(["certutil", "-store", "-user", "Root"], timeout=30)
    return rc2 == 0 and "mitmproxy" in (out2 or "").lower()


def _bring_confirm_dialog_to_front():
    """把 Windows「安全警告」确认框调到最前（不是伪造点击，只是帮客户看见）。

    ★ 实测（2026-10-25）：把证书写进 Root 库时，Windows 会在 0.5 秒内
      弹出类名 #32770、标题「安全警告」的对话框，等用户点【是】。
      如果它被别的窗口挡住，客户会以为"没反应" → 又变成闪退式体验。
    """
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    found = []

    def cb(hwnd, lparam):
        if not u.IsWindowVisible(hwnd):
            return True
        n = u.GetWindowTextLengthW(hwnd)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, b, n + 1)
            t = b.value
            cls = ctypes.create_unicode_buffer(64)
            u.GetClassNameW(hwnd, cls, 64)
            if cls.value == "#32770" and ("安全" in t or "Security" in t):
                found.append(hwnd)
        return True

    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    u.EnumWindows(EnumProc(cb), 0)
    for h in found[:1]:
        try:
            u.ShowWindow(h, 5)          # SW_SHOW
            u.SetForegroundWindow(h)
            u.BringWindowToTop(h)
        except Exception:
            pass
    return bool(found)


def install_interactive(timeout: int = 90) -> tuple[bool, str, bool]:
    """装证书（交互式）：后台写 Root，等用户点系统确认框的【是】。

    返回 (成功?, 说明, 是否出现过确认框)。

    ★ 为什么这样设计（2026-10-25 实测）：
      · `certutil -addstore -user Root` 和 .NET X509Store 写 Root
        **都会**触发 Windows「安全警告」确认框
      · 客户端要求「不能有闪退感」→ 我们用后台进程，**不显示任何命令行窗口**
      · Windows 自己的确认框是**系统 UI**，客户点【是】是正常且可理解的流程
      · 我们会主动把那个框调到最前，避免它被挡住显得"没反应"
      · 一旦写入生效立即返回（不等满 timeout）

    失败时返回明确原因，GUI 再给出「手动导入」步骤（SOP PDF 也在包里）。
    """
    ca = find_ca_file()
    if ca is None:
        return (False,
                "还没生成证书文件。请先点【启动嗅探】，等 3~5 秒"
                "（程序会自动生成证书），再回来点【安装/检测】。",
                False)

    if is_installed():
        return True, "证书已经装好了，无需重复安装。", False

    script = (
        "$ErrorActionPreference='Stop';"
        f"$c=New-Object System.Security.Cryptography.X509Certificates.X509Certificate2({_ps_quote(str(ca))});"
        "$s=New-Object System.Security.Cryptography.X509Certificates.X509Store('Root','CurrentUser');"
        "$s.Open('ReadWrite');"
        "$s.Add($c);"
        "$s.Close();"
        "Write-Output 'DONE'"
    )
    try:
        proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", script],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=_NO_WINDOW)
    except Exception as e:                       # noqa: BLE001
        return False, f"无法启动安装程序：{type(e).__name__}: {e}", False

    saw_dialog = False
    t0 = time.time()
    try:
        while time.time() - t0 < timeout:
            if _bring_confirm_dialog_to_front():
                saw_dialog = True
            # 写入生效就立刻收工（客户点了【是】）
            if is_installed():
                try:
                    _add_to_store(ca, "My")      # 便于在 certmgr 里看到/删除
                except Exception:
                    pass
                return True, "证书安装成功，现在可以正常嗅探了。", saw_dialog
            if proc.poll() is not None:
                break
            time.sleep(0.4)
    finally:
        if proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass

    if is_installed():
        return True, "证书安装成功，现在可以正常嗅探了。", saw_dialog
    if saw_dialog:
        return (False,
                "Windows 的「安全警告」没被确认（可能是被关掉或点了否）。"
                "可以再点一次【安装/检测】，并在弹框时点【是】。",
                True)
    return (False, "系统没有弹出确认框，自动安装没能完成。", False)


def install() -> tuple[bool, str]:
    """把 CA 装进「当前用户 → 受信任的根证书颁发机构」。

    返回 (成功?, 给用户看的说明)。**不需要管理员权限、不弹窗口**。
    """
    ca = find_ca_file()
    if ca is None:
        return False, ("还没生成证书文件。请先点【启动嗅探】，等 3~5 秒"
                       "（程序会自动生成证书），再回来点【安装/检测】。")

    if is_installed():
        return True, "证书已经装好了，无需重复安装。"

    # ① 主路径：.NET X509Store 静默写入（无确认框）
    ok, out = _add_to_store(ca, "Root")
    if not ok:
        # 有些机器 PowerShell 受限，退回 certutil（可能弹框，尽力而为）
        _run(["certutil", "-addstore", "-user", "Root", str(ca)], timeout=40)
        ok = bool(out)

    # ② 以「真的装上了」为准，不以命令返回值为准
    if is_installed():
        # 顺便放进「个人」库，方便客户在 certmgr 里看到并删除
        try:
            _add_to_store(ca, "My")
        except Exception:
            pass
        return True, "证书安装成功，现在可以正常嗅探了。"

    # ③ 失败：给出真实原因 + 让客户能自己兜底
    low = (out or "").lower()
    hint = ""
    if "取消" in out or "cancel" in low or "0x800704b7" in low:
        hint = "（系统弹出了安全确认框但没等到确认）"
    elif "denied" in low or "拒绝" in out:
        hint = "（系统或杀毒软件拒绝了写入）"
    tail = (out or "").strip().splitlines()
    tail = tail[-1][:120] if tail else "无输出"
    return False, f"自动安装没成功{hint}：{tail}"


def uninstall() -> tuple[bool, str]:
    """删掉已安装的证书（客户要重装 / 我们做首装验证时用）。"""
    ca = find_ca_file()
    if ca is None:
        return False, "找不到证书文件"
    ok1, _ = _remove_from_store(ca, "Root")
    ok2, _ = _remove_from_store(ca, "My")
    still = is_installed()
    if still:
        return False, "删除失败，证书仍在根证书库中"
    return True, "已删除证书，下次点【安装/检测】会重新安装"


def open_wizard(bat_path: Path) -> tuple[bool, str]:
    """兜底：打开批处理向导（可见窗口，客户能看到过程）。

    ⚠️ v2.2.18 起 GUI **不再调用**这个函数（窗口本身就是风险源），
    保留仅供高级用户/排障使用。
    """
    try:
        os.startfile(str(bat_path))   # noqa: S606
        return True, ""
    except Exception as e:                       # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
