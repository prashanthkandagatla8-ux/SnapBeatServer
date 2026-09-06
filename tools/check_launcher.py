"""Check the desktop shortcut really launches the app.

A shortcut that exists is not a shortcut that works. This starts it the way Explorer does,
waits for the server to answer, and confirms the page served is this project rather than
the one it was forked from -- the two are similar enough that a wrong working directory or
a stale process would otherwise go unnoticed.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatcanvas import config  # noqa: E402

HOST, PORT = "127.0.0.1", 8772
REPORT = config.ROOT / "_logs" / "launcher_report.txt"

failures = 0
lines: list[str] = []


def say(text: str = "") -> None:
    print(text)
    lines.append(text)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures
    say(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures += 1


def desktop() -> Path:
    for candidate in (Path(os.environ.get("USERPROFILE", "")) / "Desktop",
                      Path(os.environ.get("OneDrive", "_")) / "Desktop",
                      Path.home() / "Desktop"):
        if candidate.is_dir():
            return candidate
    return Path.home()


def listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        return probe.connect_ex((HOST, port)) == 0


def powershell(command: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, timeout=timeout)


def main() -> int:
    shortcut = desktop() / "BeatCanvas.lnk"
    say("1. the shortcut is on the desktop and points at this project")
    check("the file exists", shortcut.exists(), str(shortcut))
    if not shortcut.exists():
        return 1

    result = powershell(
        "$s = New-Object -ComObject WScript.Shell; "
        f"$l = $s.CreateShortcut('{shortcut}'); "
        "\"$($l.TargetPath)|$($l.Arguments)|$($l.WorkingDirectory)|"
        "$($l.IconLocation)|$($l.WindowStyle)\"")
    fields = (result.stdout or "").strip().split("|")
    if len(fields) < 5:
        check("the shortcut could be read back", False, (result.stderr or "")[:140])
        return 1
    target, arguments, working, icon, style = fields[:5]
    say(f"        target : {target}")
    say(f"        script : {arguments}")
    say(f"        folder : {working}")
    say(f"        icon   : {icon}")

    check("it runs pythonw, so no console window appears",
          target.lower().endswith("pythonw.exe"), Path(target).name)
    check("the interpreter exists", Path(target).exists())
    check("it runs this project's launcher",
          Path(arguments.strip('"')).resolve() == (config.ROOT / "run.py").resolve(),
          arguments)
    # A path with a space in it must be quoted or Python receives only its first word and
    # dies before it can log anything, which under pythonw looks like nothing happening.
    check("the script path is quoted, since it contains spaces",
          arguments.strip().startswith('"') and arguments.strip().endswith('"'),
          arguments)
    check("it starts in this project's folder",
          Path(working).resolve() == config.ROOT.resolve(), working)
    check("it uses the drawn icon", "beatcanvas.ico" in icon.lower(), icon)

    say("\n2. starting it the way Explorer would")
    if listening(PORT):
        say(f"        something is already on {PORT}; stopping it so the launch is "
            f"really being tested")
        powershell("Get-Process python,pythonw -ErrorAction SilentlyContinue | "
                   "Stop-Process -Force")
        time.sleep(3)
    check(f"port {PORT} is free before launching", not listening(PORT))

    # Launched detached and in its own process group, which is how Explorer does it. Started
    # as an ordinary child instead, the app dies the moment the launching shell exits, and
    # that would be a property of however this check was invoked rather than of the
    # shortcut.
    flags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(["cmd", "/c", "start", "", str(shortcut)],
                         creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        started, why = True, "launched detached, as Explorer would"
    except Exception as exc:
        started, why = False, f"{type(exc).__name__}: {exc}"
    check("the shortcut started without error", started, why)

    say("\n3. the server comes up on its own")
    deadline = time.time() + 90
    up = False
    while time.time() < deadline:
        if listening(PORT):
            up = True
            break
        time.sleep(1)
    waited = 90 - max(0.0, deadline - time.time())
    check(f"it answers on {PORT}", up, f"after about {waited:.0f}s")
    if not up:
        log = config.ROOT / "_logs" / "server.log"
        if log.exists():
            tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-6:]
            for line in tail:
                say(f"        server.log: {line[:130]}")
        return 1

    say("\n4. the page served is this project, freshly started")
    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/", timeout=30) as reply:
            page = reply.read().decode("utf-8", "replace")
    except Exception as exc:
        check("the page loads", False, f"{type(exc).__name__}: {exc}")
        return 1
    check("the page loads", True, f"{len(page)} bytes")
    check("it is BeatCanvas, not the project it was forked from",
          "BeatCanvas" in page and "BeatForge" not in page,
          "BeatForge appears in the page" if "BeatForge" in page else "BeatCanvas")
    check("the automatic mode is offered",
          'value="auto"' in page and "Let the music decide" in page)
    check("the pace choice is offered", 'name="pace"' in page)

    log = config.ROOT / "_logs" / "server.log"
    check("it logged its start, since there is no console",
          log.exists() and log.stat().st_size > 0,
          f"{log.stat().st_size} bytes" if log.exists() else "missing")

    say("\n" + "=" * 74)
    if failures:
        say(f"{failures} check(s) FAILED")
        return 1
    say("the desktop shortcut launches the app, opens the browser and needs no console")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback
        say("\nthe check itself crashed:\n" + traceback.format_exc())
        sys.exit(3)
