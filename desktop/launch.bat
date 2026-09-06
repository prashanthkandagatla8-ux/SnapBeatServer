@echo off
rem Launch the desktop app.
rem
rem This calls the electron binary directly rather than going through npx or npm. On this
rem machine PowerShell refuses to run npx.ps1 because script execution is disabled, which is
rem what made every earlier "npm install" and "npx electron" appear to do nothing at all.
rem Calling the executable avoids the shell entirely, so the launcher cannot be defeated by
rem a policy setting.
cd /d "%~dp0"
if not exist "node_modules\electron\dist\electron.exe" (
  echo Electron is not installed. Run:  cmd /c npm.cmd install
  echo in %~dp0 and try again.
  pause
  exit /b 1
)
start "" "node_modules\electron\dist\electron.exe" "%~dp0."
