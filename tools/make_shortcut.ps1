# Creates a "BeatForge" shortcut on the Desktop.
# Targets pythonw.exe so launching leaves no console window behind; anything the server
# prints goes to _logs\server.log instead.

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$runner = Join-Path $root "run.py"
if (-not (Test-Path $runner)) { throw "run.py not found at $runner" }

# BeatForge reuses the GoldForge virtual environment, which already has numpy,
# opencv, fastapi and uvicorn installed. Prefer a local .venv if one appears later.
$candidates = @(
    (Join-Path $root ".venv\Scripts\pythonw.exe"),
    (Join-Path $root ".venv\Scripts\python.exe"),
    (Join-Path (Split-Path -Parent $root) "GoldForge\.venv\Scripts\pythonw.exe"),
    (Join-Path (Split-Path -Parent $root) "GoldForge\.venv\Scripts\python.exe")
)
$target = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $target) { throw "no python interpreter found; looked in $($candidates -join '; ')" }

$desktop = [Environment]::GetFolderPath("Desktop")
$linkPath = Join-Path $desktop "BeatForge.lnk"

$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($linkPath)
$link.TargetPath = $target
$link.Arguments = '"' + $runner + '"'
$link.WorkingDirectory = $root
$link.Description = "BeatForge - CapCut templates rendered with your own photos"
$link.WindowStyle = 7
$icon = Join-Path $root "beatforge.ico"
if (Test-Path $icon) { $link.IconLocation = $icon }
else { $link.IconLocation = "$env:SystemRoot\System32\imageres.dll,108" }
$link.Save()

Write-Output "created: $linkPath"
Write-Output "  target : $target"
Write-Output "  args   : $runner"
Write-Output "  workdir: $root"
