@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo  mitmproxy CA certificate install wizard
echo  This CA belongs to the local video downloader, sphdl.
echo  It only affects this PC. Safe.
echo ============================================================
echo.

where certutil >nul 2>&1
if errorlevel 1 (
    echo [ERROR] certutil not found on this system.
    goto end
)

rem Locate the CA file: prefer the pure-cert files (the combined
rem mitmproxy-ca.pem contains the private key and certutil rejects it).
set "CA=%USERPROFILE%\.mitmproxy\mitmproxy-ca-cert.pem"
if not exist "%CA%" set "CA=%USERPROFILE%\.mitmproxy\mitmproxy-ca-cert.cer"
if not exist "%CA%" set "CA=%~dp0ca.pem"
if not exist "%CA%" (
    echo [ERROR] CA certificate file not found.
    echo Start the downloader once first so mitmproxy can
    echo generate its CA, then run this script again.
    goto end
)
echo Using CA file: %CA%
echo.

set FOUND=
for /f "tokens=*" %%A in ('certutil -store -user root ^| findstr /i "mitmproxy" 2^>nul') do set FOUND=1

if defined FOUND (
    echo [OK] mitmproxy CA is already installed. Nothing to do.
    goto end
)

echo [1/3] Importing into user Personal store ...
certutil -addstore -user "My" "%CA%" >nul
if errorlevel 1 (
    echo [ERROR] Step 1 failed.
    goto end
)

echo [2/3] Importing into Trusted Root Certification Authorities ...
echo     If a Windows security UAC prompt appears, click Yes.
certutil -addstore -user "Root" "%CA%" >nul
if errorlevel 1 (
    echo [ERROR] Step 2 failed.
    echo If a UAC prompt appeared and you declined it, run
    echo this script again and click Yes.
    goto end
)

echo [3/3] Verifying ...
set FOUND=
for /f "tokens=*" %%A in ('certutil -store -user root ^| findstr /i "mitmproxy" 2^>nul') do set FOUND=1
if defined FOUND (
    echo.
    echo [SUCCESS] Certificate installed. You can now run the downloader.
) else (
    echo.
    echo [WARN] Import ran but verification did not confirm.
    echo Open certmgr.msc and check Trusted Root Certification
    echo Authorities under Certificates - Current User.
)

:end
echo.
pause
