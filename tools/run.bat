@echo off
rem Small launcher so nested-quote paths do not have to survive the shell.
set PY=C:\Users\prash\Kiro Projects\GoldForge\.venv\Scripts\python.exe
set ROOT=C:\Users\prash\Kiro Projects\BeatCanvas
cd /d "%ROOT%"
if not exist _logs mkdir _logs
"%PY%" %* 
