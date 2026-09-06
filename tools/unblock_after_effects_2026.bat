@echo off
:: Self-elevation to Administrator
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Requesting Administrator privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo =======================================================
echo   Unblocking Adobe After Effects 2026 in Windows Firewall
echo =======================================================
echo.

set "EXES=AfterFX.exe aerender.exe Adobe Analysis Server.exe CRWindowsClientService.exe dynamiclinkmanager.exe TeamProjectsLocalHub.exe dvaapprelauncher.exe GPUSniffer.exe"

for %%E in (%EXES%) do (
    netsh advfirewall firewall delete rule name="Block AE 2026 Outbound - %%E" >nul 2>&1
    netsh advfirewall firewall delete rule name="Block AE 2026 Inbound - %%E" >nul 2>&1
    echo   [OK] Removed rules for %%E
)

echo.
echo =======================================================
echo   After Effects 2026 firewall rules removed successfully.
echo =======================================================
echo.
pause
