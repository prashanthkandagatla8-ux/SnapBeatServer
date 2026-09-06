"""Launcher: start the local server and open a browser.

    .venv\\Scripts\\pythonw.exe run.py     (what the desktop shortcut uses)
"""
from __future__ import annotations

import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import os

HOST = "0.0.0.0"

#: The Electron shell picks a free port itself and passes it in, so that the window it
#: opens and the server it started cannot disagree about where the app is. Run on its own,
#: the launcher falls back to its usual port.
PREFERRED_PORT = int(os.environ.get("BEATCANVAS_PORT", "8772"))

#: Electron shows the app in its own window, so opening a browser as well would be a
#: second, confusing copy of the same thing.
OPEN_BROWSER = os.environ.get("BEATCANVAS_NO_BROWSER", "") != "1"

LOG_DIR = ROOT / "_logs"


def find_port(start: int, attempts: int = 20) -> int:
    for offset in range(attempts):
        candidate = start + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((HOST, candidate))
                return candidate
            except OSError:
                continue
    raise RuntimeError(f"no free port in {start}..{start + attempts}")


def already_running(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        return probe.connect_ex((HOST, port)) == 0


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # A second launch should surface the existing window, not fail on a busy port.
    if already_running(PREFERRED_PORT):
        if OPEN_BROWSER:
            webbrowser.open(f"http://{HOST}:{PREFERRED_PORT}/")
        return 0

    # When a port was handed to us, use exactly that one: the shell is already waiting on
    # it, and quietly moving to the next would leave it waiting for ever.
    port = (PREFERRED_PORT if os.environ.get("BEATCANVAS_PORT")
            else find_port(PREFERRED_PORT))
    url = f"http://{HOST}:{port}/"

    # pythonw has no console, so output needs somewhere to land.
    if not sys.stdout or not sys.stdout.isatty():
        handle = open(LOG_DIR / "server.log", "a", buffering=1, encoding="utf-8")
        sys.stdout = handle
        sys.stderr = handle
        print(f"\n=== BeatCanvas starting {time.strftime('%Y-%m-%d %H:%M:%S')} -> {url}")

    if OPEN_BROWSER:
        threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(url)),
                         daemon=True).start()

    import uvicorn

    from beatcanvas.app import app

    print(f"BeatCanvas listening on {url}")
    uvicorn.run(app, host=HOST, port=port, log_level="info", access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())

