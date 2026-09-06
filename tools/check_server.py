"""Verify the running server serves its pages, not merely that it bound a port."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8772"
failures = 0


def fetch(path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(BASE + path, timeout=25) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:
        return 0, str(exc)


def expect(label: str, ok: bool, detail: str = "") -> None:
    global failures
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures += 1


def main() -> int:
    status, body = fetch("/api/health")
    expect("GET /api/health", status == 200, f"status {status}")
    if status == 200:
        data = json.loads(body)
        print(f"       templates={data['templates']} drafts={data['drafts']}")
        print(f"       animations={data['animations']}")
        print(f"       ffmpeg={data['ffmpeg']}")

    status, body = fetch("/")
    expect("GET / renders", status == 200 and "New video" in body,
           f"status {status}, {len(body)} bytes")
    for marker in ("Templates", "Import a CapCut draft", "Placeholder cards", "Jobs",
                   "repeat", "trim"):
        expect(f"home page shows '{marker}'", marker in body)

    status, body = fetch("/api/jobs")
    expect("GET /api/jobs", status == 200, f"status {status}")

    status, body = fetch("/api/photos?folder=" +
                         urllib.parse.quote(r"C:\Users\prash\Kiro Projects\BeatCanvas\_placeholders"))
    expect("GET /api/photos lists the placeholders", status == 200, f"status {status}")
    if status == 200:
        print(f"       {json.loads(body)['count']} photos")

    status, _ = fetch("/api/photos?folder=" + urllib.parse.quote(r"C:\nope\missing"))
    expect("GET /api/photos rejects a missing folder", status == 400, f"status {status}")

    # Path guard: a file outside BeatCanvas must not be served.
    status, _ = fetch("/file?path=" + urllib.parse.quote(r"C:\Windows\win.ini"))
    expect("GET /file refuses paths outside BeatCanvas", status in (403, 404),
           f"status {status}")

    status, body = fetch("/template?path=" + urllib.parse.quote(
        r"C:\Users\prash\Kiro Projects\BeatCanvas\templates\0817-1_rebuild.json"))
    expect("GET /template renders", status == 200 and "Retime" in body,
           f"status {status}")

    print()
    print("all server checks passed" if not failures else f"{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
