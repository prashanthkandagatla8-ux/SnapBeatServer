@echo off
rem Starts the BeatCanvas server in the foreground. The desktop shortcut uses
rem pythonw.exe via run.py instead, which leaves no console window behind.
set PY=C:\Users\prash\Kiro Projects\GoldForge\.venv\Scripts\python.exe
cd /d "C:\Users\prash\Kiro Projects\BeatCanvas"
"%PY%" run.py
