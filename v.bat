@echo off
rem Run a check, or the whole guard suite, and put the output in _logs\v.txt.
rem
rem   v.bat                              the full suite: presets, page, layers
rem   v.bat tools\check_presets.py       one script
rem   v.bat tools\make_sample.py "<track>" 20 one-groove
rem
rem This exists because the IDE terminal on this machine truncates long command lines and
rem sometimes swallows them entirely, and because PowerShell refuses to run .ps1 shims. A
rem short call into a batch file is the one form that has proved reliable, and redirecting
rem to a file means the result can be read even when the terminal shows nothing.
cd /d "C:\Users\prash\Kiro Projects\BeatCanvas"
if not exist "_logs" mkdir "_logs"
set PY="C:\Users\prash\Kiro Projects\GoldForge\.venv\Scripts\python.exe"
if not "%~1"=="" goto :one
> "_logs\v.txt" echo ==== presets ====
%PY% "tools\check_presets.py" >> "_logs\v.txt" 2>&1
echo EXITCODE %ERRORLEVEL%>> "_logs\v.txt"
echo ==== page ====>> "_logs\v.txt"
%PY% "tools\check_page.py" >> "_logs\v.txt" 2>&1
echo EXITCODE %ERRORLEVEL%>> "_logs\v.txt"
echo ==== layers ====>> "_logs\v.txt"
%PY% "tools\check_layers.py" >> "_logs\v.txt" 2>&1
echo EXITCODE %ERRORLEVEL%>> "_logs\v.txt"
goto :eof
:one
%PY% %* > "_logs\v.txt" 2>&1
echo EXITCODE %ERRORLEVEL%>> "_logs\v.txt"
