"""Put a launcher for this app on the desktop.

The shortcut runs ``pythonw.exe run.py``, which starts the server, waits for it, and opens
the browser. ``pythonw`` rather than ``python`` so no console window is left behind, and
``run.py`` already redirects its output to ``_logs\\server.log`` for exactly that reason.

    tools\\run.bat tools\\make_shortcut.py

Creating the shortcut needs a Windows COM object, so that one step is handed to PowerShell.
Everything else -- finding an interpreter that can actually run the app, building the icon,
checking the result -- happens here where it can be reported properly.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatcanvas import config  # noqa: E402

ICON = config.ROOT / "_assets" / "beatcanvas.ico"
REPORT = config.ROOT / "_logs" / "shortcut.txt"
SHORTCUT_NAME = "BeatCanvas.lnk"

#: Interpreters to try, best first. The app needs numpy, OpenCV, FastAPI and uvicorn, so an
#: interpreter without them would produce a shortcut that fails silently -- worse than no
#: shortcut at all. Each candidate is therefore tested by importing what the app imports.
CANDIDATES = [
    Path(r"C:\Users\prash\Kiro Projects\GoldForge\.venv\Scripts\pythonw.exe"),
    Path(sys.executable).with_name("pythonw.exe"),
    Path(sys.executable),
]

lines: list[str] = []


def say(text: str) -> None:
    print(text)
    lines.append(text)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def can_run(interpreter: Path) -> tuple[bool, str]:
    """Does this interpreter have what the app needs?

    ``pythonw`` cannot report back, so the check runs against its console twin and the
    result is taken to apply to both: they share an installation and its packages.
    """
    console = interpreter.with_name(interpreter.name.replace("pythonw", "python"))
    if not console.exists():
        return False, "no matching python.exe to test with"
    probe = ("import numpy, cv2, fastapi, uvicorn; "
             "print('ok')")
    try:
        result = subprocess.run([str(console), "-c", probe],
                                capture_output=True, text=True, timeout=90)
    except Exception as exc:
        return False, f"{type(exc).__name__}"
    if result.returncode == 0 and "ok" in result.stdout:
        return True, "has numpy, cv2, fastapi and uvicorn"
    missing = (result.stderr or "").strip().splitlines()
    return False, missing[-1][:90] if missing else "import failed"


def desktop() -> Path:
    """Where the desktop actually is, which is not always under the home folder."""
    for candidate in (Path(os.environ.get("USERPROFILE", "")) / "Desktop",
                      Path(os.environ.get("OneDrive", "_")) / "Desktop",
                      Path.home() / "Desktop"):
        if candidate.is_dir():
            return candidate
    return Path.home()


def build_icon() -> bool:
    console = Path(sys.executable)
    script = config.ROOT / "tools" / "make_icon.py"
    try:
        result = subprocess.run([str(console), str(script)],
                                capture_output=True, text=True, timeout=180)
    except Exception as exc:
        say(f"  could not draw the icon: {type(exc).__name__}")
        return False
    if result.returncode != 0:
        say(f"  could not draw the icon: "
            f"{(result.stderr or '').strip().splitlines()[-1][:120]}")
        return False
    return ICON.exists()


#: Electron's own executable, once ``npm install`` has fetched it. Pointing the shortcut
#: straight at it avoids going through npm, which would flash a console window and add a
#: process between the icon and the app for no benefit.
ELECTRON = (config.ROOT / "desktop" / "node_modules" / "electron" / "dist"
            / "electron.exe")


def create_electron(target: Path) -> tuple[bool, str]:
    """A shortcut that opens the real desktop window."""
    return _write_shortcut(
        target,
        program=ELECTRON,
        arguments=f'"{config.ROOT / "desktop"}"',
        working=config.ROOT / "desktop")


def create(interpreter: Path, target: Path) -> tuple[bool, str]:
    """A shortcut that starts the engine and opens the browser instead."""
    return _write_shortcut(
        target,
        program=interpreter,
        arguments=f'"{config.ROOT / "run.py"}"',
        working=config.ROOT)


def _write_shortcut(target: Path, program: Path, arguments: str,
                    working: Path) -> tuple[bool, str]:
    icon = str(ICON) if ICON.exists() else str(program)
    # Single-quoted PowerShell strings, with any quote doubled, so a path containing a
    # space or an apostrophe cannot break out of the string or inject a command.
    def literal(value: str) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    command = (
        "$shell = New-Object -ComObject WScript.Shell; "
        f"$link = $shell.CreateShortcut({literal(target)}); "
        f"$link.TargetPath = {literal(program)}; "
        # Paths here contain spaces, so each must arrive as a single argument. Unquoted, the
        # program is handed only the part before the first space, fails to find it, and exits
        # before it can log anything -- and with no console attached that looks exactly like
        # the shortcut doing nothing at all.
        f"$link.Arguments = {literal(arguments)}; "
        f"$link.WorkingDirectory = {literal(working)}; "
        f"$link.IconLocation = {literal(icon)}; "
        f"$link.Description = 'BeatCanvas - beat-synced slideshows from your own music'; "
        "$link.WindowStyle = 7; "
        "$link.Save(); "
        "Write-Output 'saved'"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=120)
    except Exception as exc:
        return False, f"{type(exc).__name__}"
    if result.returncode == 0 and "saved" in result.stdout:
        return True, "saved"
    return False, ((result.stderr or result.stdout).strip().splitlines() or ["failed"])[-1]


def main() -> int:
    say("choosing an interpreter that can actually run the app")
    chosen: Path | None = None
    for candidate in CANDIDATES:
        if not candidate.exists():
            say(f"  [skip] {candidate}  (not present)")
            continue
        ok, why = can_run(candidate)
        say(f"  [{'use ' if ok else 'skip'}] {candidate}  ({why})")
        if ok and chosen is None:
            chosen = candidate
    if chosen is None:
        say("\nNo interpreter on this machine has the packages the app needs, so a "
            "shortcut would fail silently. Install them first:")
        say("  pip install -r requirements.txt")
        return 1

    say("\ndrawing the icon")
    say(f"  {'drawn' if build_icon() else 'falling back to the python icon'}: {ICON}")

    target = desktop() / SHORTCUT_NAME
    say(f"\ncreating the shortcut\n  {target}")

    # The real desktop window is preferred when it is installed. Without it the shortcut
    # still works, by starting the engine and opening the browser, so the app is never
    # unreachable just because Electron has not been fetched yet.
    if ELECTRON.exists():
        say("  Electron is installed, so this opens the desktop window")
        ok, detail = create_electron(target)
        kind = "the desktop window"
    else:
        say("  Electron is not installed yet, so this starts the engine and opens your "
            "browser instead")
        say(f"  (run 'npm install' in {config.ROOT / 'desktop'} then rerun this tool)")
        ok, detail = create(chosen, target)
        kind = "your browser"
    if not ok:
        say(f"  failed: {detail}")
        return 1

    if not target.exists():
        say("  PowerShell reported success but the file is not there")
        return 1

    say(f"  created, {target.stat().st_size} bytes")
    say("")
    say(f"Double-click it and the app opens in {kind}. There is no console window; if it "
        f"ever fails to appear, the reason is in:")
    say(f"  {config.ROOT / '_logs' / 'server.log'}")
    if ELECTRON.exists():
        say(f"  {config.ROOT / '_logs' / 'desktop.log'}")
    say("")
    say(f"The engine will run on: {chosen}")
    say("")
    say("Note: that interpreter belongs to the GoldForge virtual environment, which this "
        "project borrows. Moving or deleting GoldForge would break the app; rerun this "
        "tool afterwards to point it somewhere else.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
