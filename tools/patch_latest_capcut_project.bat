@echo off
title CapCut Pendulum Vertical Patcher
cd /d "%~dp0\.."
echo ========================================================
echo   CapCut Pendulum Vertical Patcher (Auto-Detect Latest)
echo ========================================================
echo.
python tools\apply_pendulum_vertical.py
echo.
echo Press any key to exit...
pause >nul
