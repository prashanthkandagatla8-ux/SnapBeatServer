@echo off
:: Self-elevation to Administrator
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Requesting Administrator privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo =======================================================
echo   Blocking Adobe After Effects 2026 in Windows Firewall
echo =======================================================
echo.

set "AE_DIR=C:\Program Files\Adobe\Adobe After Effects 2026\Support Files"

if not exist "%AE_DIR%\AfterFX.exe" (
    echo [ERROR] AfterFX.exe not found at:
    echo "%AE_DIR%"
    echo.
    pause
    exit /b 1
)

set "EXES=AfterFX.exe aerender.exe Adobe Analysis Server.exe CRWindowsClientService.exe dynamiclinkmanager.exe TeamProjectsLocalHub.exe dvaapprelauncher.exe GPUSniffer.exe"

for %%E in (%EXES%) do (
    if exist "%AE_DIR%\%%E" (
        echo [1/2] Adding Outbound Block for %%E...
        netsh advfirewall firewall delete rule name="Block AE 2026 Outbound - %%E" >nul 2>&1
        netsh advfirewall firewall add rule name="Block AE 2026 Outbound - %%E" dir=out action=block program="%AE_DIR%\%%E" enable=yes profile=any >nul

        echo [2/2] Adding Inbound Block for %%E...
        netsh advfirewall firewall delete rule name="Block AE 2026 Inbound - %%E" >nul 2>&1
        netsh advfirewall firewall add rule name="Block AE 2026 Inbound - %%E" dir=in action=block program="%AE_DIR%\%%E" enable=yes profile=any >nul
        echo   [OK] %%E blocked.
    )
)

echo.
echo =======================================================
echo   Successfully blocked all After Effects 2026 internet access!
echo   Both Inbound and Outbound connections are blocked.
echo   Your Wi-Fi and other apps remain completely unaffected.
echo =======================================================
echo.
pause
