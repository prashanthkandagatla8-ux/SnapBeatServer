# BeatCanvas desktop shell

A real window around the app, rather than a browser tab pointed at a local server.

## Setting it up

Two commands, once:

```
cd "C:\Users\prash\Kiro Projects\BeatCanvas\desktop"
cmd /c npm.cmd install
```

That fetches Electron, about 150 MB.

**Use `cmd /c npm.cmd`, not plain `npm`.** PowerShell on this machine has script execution
disabled, so `npm.ps1` and `npx.ps1` are refused. The failure is quiet in the worst way: the
command appears to return immediately having done nothing, which cost several attempts to
diagnose. Calling the `.cmd` through `cmd` skips PowerShell's policy entirely.

Then point the desktop icon at it:

```
cd "C:\Users\prash\Kiro Projects\BeatCanvas"
tools\run.bat tools\make_shortcut.py
```

The icon now opens the desktop window. Until Electron is installed the same icon starts the
engine and opens your browser instead, so the app is never unreachable because a download
has not happened yet.

To run it without the icon, double-click `desktop\launch.bat`, or:

```
desktop\launch.bat
```

which calls the Electron binary directly for the same reason as above. `npm start` works
only from a shell that allows scripts.

Verified on 30 Aug 2026: Electron 38.4.0, the window opens, and `_logs\desktop.log` records
the engine answering on a freshly chosen port about two and a half seconds after launch.

## What the shell does, and why

**It owns the engine's life.** Python is started when the window opens and stopped when it
closes, killing the whole process tree so uvicorn and any ffmpeg beneath it go too. Started
separately, the two drift apart: an old server keeps holding the port and the window ends up
talking to code that is no longer on disk. That happened repeatedly while this was being
built, and it is confusing every time.

**It owns the port.** A free one is found here and handed to Python through
`BEATCANVAS_PORT`, so the window and the engine cannot disagree about where the app is, and
a second project cannot collide with it. `BEATCANVAS_NO_BROWSER=1` stops Python opening a
browser as well, which would be a second copy of the same thing.

**It provides the file dialogs.** `preload.js` exposes a deliberately narrow bridge: ask for
a dialog, ask for one of the app's own folders to be opened, nothing else. The page is
served over HTTP and gets no access to Node, so a bug in the page cannot reach the machine.

The page checks for that bridge and falls back to the server's own dialogs when it is
absent, which is what lets the same page work in the window and in a browser.

## If the window does not appear

Two logs, both under `_logs`:

* `desktop.log` — the shell: which interpreter it chose, the port, whether Python answered
* `server.log` — the engine itself

The shell waits up to 90 seconds for the engine to answer `/api/health` before giving up,
and says so in a dialog rather than showing an empty window.

## Known limitation

The engine runs on GoldForge's virtual environment, which this project borrows; there is no
separate one yet. Moving or deleting GoldForge breaks it. `main.js` tries, in order:
GoldForge's `.venv`, a `.venv` in this project, then plain `python` on PATH — so creating a
local `.venv` with `requirements.txt` installed is enough to cut the dependency.
