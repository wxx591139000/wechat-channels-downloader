@echo off
chcp 936 >nul 2>&1
title sphdl 诊断工具 - 请把本窗口截图发给发布方
cd /d "%~dp0"

echo ============================================================
echo   sphdl 启动诊断
echo   这个窗口会显示程序内部的报错，请勿关闭
echo ============================================================
echo.

echo [1/6] 系统信息
echo     电脑名: %COMPUTERNAME%
echo     用户名: %USERNAME%
echo     用户目录: %USERPROFILE%
ver
echo.

echo [2/6] 证书目录检查
if exist "%USERPROFILE%\.mitmproxy" (
    echo     [有] %USERPROFILE%\.mitmproxy
    dir /b "%USERPROFILE%\.mitmproxy"
) else (
    echo     [无] %USERPROFILE%\.mitmproxy  —— 说明嗅探从未成功启动过
)
echo.

echo [3/6] 检查 8899 端口是否被占用
netstat -ano | findstr ":8899" | findstr "LISTENING"
if errorlevel 1 (
    echo     [空闲] 8899 端口没人占用 —— 正常
) else (
    echo     [警告] 8899 端口已被占用！可能是上次没退出干净，或被别的软件占了
)
echo.

echo [4/6] 检查是否有残留进程
tasklist | findstr /i "微信视频号下载器"
if errorlevel 1 (
    echo     [无] 没有残留进程
) else (
    echo     [警告] 有残留进程，建议先结束它们
)
echo.

echo [5/6] 检查关键文件是否齐全
set MISSING=
for %%F in ("微信视频号下载器.exe" "install-cert.bat" "_internal") do (
    if not exist "%%~F" set MISSING=1& echo     [缺失] %%~F
)
if not defined MISSING echo     [齐全] 关键文件都在
echo.

echo [6/6] 现在启动程序（会显示真实报错）
echo     ------------------------------------------------------------
echo     下面这个窗口是程序的输出。如果它一闪就关，
echo     或者有红色/英文报错，请立刻截图！
echo     ------------------------------------------------------------
echo.
echo     按任意键开始启动...
pause >nul

echo.
echo     === 程序输出开始 ===
微信视频号下载器.exe
echo     === 程序输出结束（退出码: %ERRORLEVEL%）===
echo.

echo ============================================================
echo   如果上面没有任何输出就结束了，说明程序在启动早期就崩了
echo   请把本窗口完整截图发给发布方
echo ============================================================
echo.
echo 另外请附上这个文件的内容（如果存在）：
echo   日志目录: %CD%\logs\sphdl.log
if exist "logs\sphdl.log" (
    echo.
    echo ------------------------------------------------------------
    echo   日志最后 60 行：
    echo ------------------------------------------------------------
    powershell -NoProfile -Command "Get-Content 'logs\sphdl.log' -Tail 60"
)
echo.
echo ============================================================
echo   按任意键关闭
echo ============================================================
pause >nul
