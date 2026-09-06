"""Check the browse and open-folder features are wired up and guarded.

The dialogs themselves need a person to click them, so what is checked here is everything
around them: the page offers the buttons, the endpoint validates what it is asked for, the
folder opener refuses paths outside the app, and the picker subprocess can actually start
on this machine.
"""
from __future__ import annotations

import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatcanvas import config, dialogs  # noqa: E402

BASE = "http://127.0.0.1:8772"
REPORT = config.ROOT / "_logs" / "browse_report.txt"

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


def post(path: str, fields: dict) -> tuple[int, str]:
    body = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(
        BASE + path, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=20) as reply:
            return reply.status, reply.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except OSError as exc:
        return 0, str(exc)


def main() -> int:
    say("1. the picker can start on this machine")
    interpreter = dialogs._interpreter()
    say(f"        using {interpreter}")
    probe = subprocess.run(
        [interpreter.replace("pythonw.exe", "python.exe"), "-c",
         "import tkinter; print('ok')"],
        capture_output=True, text=True, timeout=90)
    check("tkinter is available, so a dialog can be shown",
          probe.returncode == 0 and "ok" in probe.stdout,
          (probe.stderr or "").strip().splitlines()[-1][:90]
          if probe.returncode else "present")
    check("every offered dialog kind is known",
          set(dialogs.KINDS) == {"folder", "output", "music", "photos"},
          ", ".join(dialogs.KINDS))

    try:
        with urllib.request.urlopen(BASE + "/", timeout=20) as reply:
            page = reply.read().decode("utf-8", "replace")
    except Exception as exc:
        say(f"\nserver not answering on {BASE} ({exc}); start it and run this again")
        say("\n" + "=" * 70)
        say(f"{failures} check(s) FAILED" if failures else "checked what was reachable")
        return 2

    say("\n2. the page offers the buttons")
    for label, marker in (
            ("browse for a photo folder", "browseFor('folder')"),
            ("pick photos one by one", "browseFor('photos')"),
            ("browse for music", "browseFor('music')"),
            ("browse for the output folder", "browseFor('output')"),
            ("open the output folder", "openOutput()"),
            ("the output folder is shown, not hidden", 'id="output_dir"')):
        check(label, marker in page, marker)

    say("\n3. the endpoint checks what it is asked for")
    status, detail = post("/api/browse", {"kind": "nonsense"})
    check("an unknown dialog kind is refused", status == 400, detail[:110])

    say("\n4. opening a folder is guarded")
    status, detail = post("/api/open-folder", {"path": str(config.OUTPUT_DIR)})
    check("the app's own output folder opens", status == 200, detail[:110])
    status, detail = post("/api/open-folder", {"path": r"C:\Windows\System32"})
    check("a folder outside the app is refused", status == 403, detail[:110])
    status, detail = post("/api/open-folder", {"path": str(config.ROOT / "nope")})
    check("a folder that does not exist is refused", status == 400, detail[:110])

    say("\n" + "=" * 70)
    if failures:
        say(f"{failures} check(s) FAILED")
        return 1
    say("browsing is wired up and guarded; the dialogs themselves need a click to test")
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
