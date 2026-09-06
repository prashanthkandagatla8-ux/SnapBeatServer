"""Report whether Node, npm and Electron are usable on this machine.

An Electron shell is only worth building if it can be run, and that depends on things
outside this project: Node, npm, a writable cache and enough network access to fetch the
Electron binaries. Establishing that first avoids writing a wrapper nobody can start.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatcanvas import config  # noqa: E402

OUT = config.ROOT / "_logs" / "node_report.txt"
lines: list[str] = []


def say(text: str = "") -> None:
    print(text)
    lines.append(text)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(command: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=timeout, shell=False)
        return result.returncode, (result.stdout or result.stderr or "").strip()
    except FileNotFoundError:
        return 127, "not found"
    except Exception as exc:
        return 1, f"{type(exc).__name__}: {exc}"


def which(name: str) -> str:
    found = shutil.which(name)
    return found or ""


say("looking for Node and npm")
for tool in ("node", "npm", "npx"):
    path = which(tool)
    if not path:
        say(f"  [NO ] {tool}  (not on PATH)")
        continue
    code, output = run([path, "--version"])
    say(f"  [yes] {tool:4s} {output.splitlines()[0] if output else '?'}   {path}")

node = which("node")
npm = which("npm")

if node:
    code, output = run([node, "-e", "console.log(process.arch, process.platform)"])
    say(f"\nnode reports: {output}")

if npm:
    say("\nnpm configuration that matters for installing Electron")
    for key in ("registry", "cache", "proxy", "https-proxy"):
        code, output = run([npm, "config", "get", key], timeout=90)
        say(f"  {key:12s} {output.splitlines()[0] if output else '(unset)'}")

    say("\ncan npm reach the registry?")
    code, output = run([npm, "view", "electron", "version"], timeout=180)
    if code == 0:
        say(f"  [yes] latest electron is {output.splitlines()[-1]}")
    else:
        say(f"  [NO ] {output.splitlines()[-1][:160] if output else 'no answer'}")

say("\nis anything already installed here?")
for folder in (config.ROOT / "desktop" / "node_modules",
               config.ROOT / "node_modules"):
    say(f"  {'yes' if folder.is_dir() else 'no '}  {folder}")

cache = Path(os.environ.get("LOCALAPPDATA", "")) / "electron" / "Cache"
say(f"  {'yes' if cache.is_dir() else 'no '}  electron binary cache at {cache}")

say("")
if node and npm:
    say("Node and npm are present, so an Electron shell can be built and run.")
else:
    say("Node is not installed, so Electron cannot run at all. Install it from "
        "https://nodejs.org (the LTS build) and run this again.")
