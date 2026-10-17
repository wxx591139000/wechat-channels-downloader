@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
cd /d "%~dp0"

title sphdl - CA 证书安装向导

echo ============================================================
echo   sphdl 证书安装向导
echo   这个证书只用于本机嗅探视频号，不会影响其它网站。
echo ============================================================
echo.

rem ---------- 检查 certutil ----------
where certutil >nul 2>&1
if errorlevel 1 (
    echo [错误] 找不到 certutil 命令。
    echo        这通常意味着系统组件损坏，请联系发布方。
    goto end
)

rem ---------- 定位 CA 文件 ----------
rem mitmproxy 的 CA 在「第一次启动嗅探」时才生成。
rem 纯证书文件才有：mitmproxy-ca.pem 里含私钥，certutil 会拒绝导入。
set "CA=%USERPROFILE%\.mitmproxy\mitmproxy-ca-cert.pem"
if not exist "%CA%" set "CA=%USERPROFILE%\.mitmproxy\mitmproxy-ca-cert.cer"
if not exist "%CA%" set "CA=%~dp0ca.pem"

if not exist "%CA%" (
    echo [原因] 还没有生成 CA 证书文件。
    echo.
    echo        这是**正常现象** —— 证书要等软件第一次启动嗅探时才生成。
    echo.
    echo   请按这个顺序操作：
    echo     1. 回到软件窗口
    echo     2. 点【▶ 启动嗅探】按钮
    echo     3. 等 3~5 秒（这时会自动生成证书）
    echo     4. 再回来点【安装/检测】按钮
    echo.
    echo   如果已经启动过嗅探还是不行，请把下面这个文件夹的
    echo   内容截图发给发布方：
    echo     %USERPROFILE%\.mitmproxy
    goto end
)

echo 找到证书文件：
echo   %CA%
echo.

rem ---------- 已装过就跳过 ----------
set FOUND=
for /f "tokens=*" %%A in ('certutil -store -user root ^| findstr /i "mitmproxy" 2^>nul') do set FOUND=1
if defined FOUND (
    echo [已安装] 检测到本机已经装过这个证书，无需重复安装。
    echo.
    echo 如果软件里仍显示「未安装」，请点软件上的【安装/检测】按钮刷新状态。
    goto end
)

rem ---------- 安装 ----------
echo [1/3] 正在导入到「个人」证书存储 ...
certutil -addstore -user "My" "%CA%" >nul 2>&1
if errorlevel 1 (
    echo [失败] 第 1 步导入失败。
    goto fail
)

echo [2/3] 正在导入到「受信任的根证书颁发机构」...
echo       如果弹出 Windows 安全警告，请点【是】。
certutil -addstore -user "Root" "%CA%" >nul 2>&1
if errorlevel 1 (
    echo [失败] 第 2 步导入失败。
    echo        很可能是 UAC 提示被拒绝了，或者被安全软件拦截。
    echo        请重新运行本脚本，并在提示时点【是】。
    goto fail
)

echo [3/3] 正在验证 ...
set FOUND=
for /f "tokens=*" %%A in ('certutil -store -user root ^| findstr /i "mitmproxy" 2^>nul') do set FOUND=1

if defined FOUND (
    echo.
    echo ============================================================
    echo   [成功] 证书已安装，可以正常使用了。
    echo ============================================================
    echo.
    echo   请回到软件窗口，点【安装/检测】按钮确认状态变成「已安装」。
    goto end
) else (
    echo.
    echo [警告] 导入命令执行了，但验证没查到证书。
    echo        请手动检查：
    echo          1. 按 Win+R，输入 certmgr.msc 回车
    echo          2. 展开「受信任的根证书颁发机构」-「证书」
    echo          3. 找找有没有 mitmproxy 开头的
    echo        如果没有，请把本窗口截图发给发布方。
    goto fail
)

:fail
echo.
echo 安装未成功。请把本窗口的截图发给发布方，说明卡在哪一步。

:end
echo.
echo ============================================================
echo   按任意键关闭本窗口
echo ============================================================
pause >nul
