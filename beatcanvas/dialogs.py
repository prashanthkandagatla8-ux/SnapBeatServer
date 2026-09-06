"""Ask Windows to show a real file or folder picker.

The app runs on your own machine, so there is no reason to make you paste paths into a text
box. These put the operating system's own dialog in front of you, which knows about your
recent folders, your drives and your libraries in a way a web page never can.

Each dialog is a short-lived subprocess. See :mod:`beatcanvas._pick` for why.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

#: The kinds of dialog on offer.
KINDS = ("folder", "output", "music", "photos")

#: A dialog is a person deciding, so it can be open for a while. Long enough to be
#: unhurried, short enough that a dialog nobody is looking at cannot wedge a request
#: for good.
TIMEOUT = 300


class Cancelled(Exception):
    """Raised when the dialog was closed without choosing anything."""


def _interpreter() -> str:
    """The windowed interpreter, so no console flashes up behind the dialog."""
    current = Path(sys.executable)
    windowed = current.with_name(current.name.replace("python.exe", "pythonw.exe"))
    return str(windowed if windowed.exists() else current)


def pick(kind: str = "folder", start: str = "") -> str:
    """Show a dialog and return what was chosen.

    For ``photos`` the result is several paths separated by newlines. Raises
    :class:`Cancelled` if the dialog was dismissed, which is a normal outcome and not an
    error worth reporting as one.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown dialog: {kind}")

    command = [_interpreter(), "-m", "beatcanvas._pick", kind]
    if start:
        # Only offer a starting point that exists, or the dialog opens somewhere arbitrary.
        candidate = Path(start)
        if candidate.is_file():
            candidate = candidate.parent
        if candidate.is_dir():
            command.append(str(candidate))

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=TIMEOUT,
        cwd=str(Path(__file__).resolve().parent.parent), creationflags=flags)

    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        raise RuntimeError(detail[-1][:200] if detail else "the dialog could not open")

    chosen = (result.stdout or "").strip()
    if not chosen:
        raise Cancelled()
    return chosen
