"""Copy this project to a new one, renaming the package as it goes.

BeatCanvas stays exactly as it is. The copy becomes an independent project with its own
package name and its own port, so both can be run at the same time without one shadowing
the other's imports or stealing its listener.

    tools\\run.bat tools\\fork_project.py "C:\\Users\\prash\\Kiro Projects\\BeatCanvas"

Only source and templates are copied. Renders, job history, logs and caches belong to the
old project and would be misleading in the new one.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent
OLD_PACKAGE, NEW_PACKAGE = "beatcanvas", "beatcanvas"
OLD_NAME, NEW_NAME = "BeatCanvas", "BeatCanvas"
OLD_PORT, NEW_PORT = "8772", "8772"

#: Directories that hold output or state rather than source.
SKIP_DIRS = {
    ".cache", "__pycache__", ".git", "output", "jobs", "_logs", "_dropped",
    "_analysis", "_reference", "uploads", "webapp", ".venv",
}
#: Files that are scratch or belong to the old project only.
SKIP_FILES = {"temp_analysis.py"}
#: Extensions whose contents get the rename applied.
TEXT = {".py", ".md", ".txt", ".html", ".json", ".bat", ".cfg", ".toml", ".gitignore"}

REPLACEMENTS = [
    (OLD_PACKAGE.upper(), NEW_PACKAGE.upper()),      # BEATCANVAS_ env vars
    (OLD_PACKAGE, NEW_PACKAGE),                       # imports, thread names
    (OLD_NAME, NEW_NAME),                             # anything user-visible
    (OLD_NAME.lower(), NEW_NAME.lower()),
    (OLD_PORT, NEW_PORT),
]


def rewrite(text: str) -> str:
    for old, new in REPLACEMENTS:
        text = text.replace(old, new)
    return text


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    target = Path(sys.argv[1])
    if target.exists() and any(target.iterdir()):
        print(f"refusing to write into a folder that already has contents: {target}")
        return 2

    copied = renamed = 0
    skipped: list[str] = []

    for path in sorted(SOURCE.rglob("*")):
        relative = path.relative_to(SOURCE)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        if path.name in SKIP_FILES:
            skipped.append(str(relative))
            continue
        if path.is_dir():
            continue

        # The package folder itself is renamed, and so is any path mentioning it.
        parts = [NEW_PACKAGE if part == OLD_PACKAGE else part for part in relative.parts]
        destination = target.joinpath(*parts)
        destination.parent.mkdir(parents=True, exist_ok=True)

        if path.suffix.lower() in TEXT or path.name == ".gitignore":
            try:
                original = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                shutil.copy2(path, destination)
                copied += 1
                continue
            updated = rewrite(original)
            destination.write_text(updated, encoding="utf-8")
            renamed += 1 if updated != original else 0
            copied += 1
        else:
            shutil.copy2(path, destination)
            copied += 1

    # The new project needs its own empty working folders.
    for folder in ("output", "jobs", "_logs", "_dropped"):
        (target / folder).mkdir(parents=True, exist_ok=True)

    print(f"copied {copied} files into {target}")
    print(f"  {renamed} had {OLD_NAME} -> {NEW_NAME} applied")
    print(f"  package {OLD_PACKAGE}/ became {NEW_PACKAGE}/")
    print(f"  port {OLD_PORT} became {NEW_PORT}, so both can run at once")
    if skipped:
        print(f"  left behind: {', '.join(skipped)}")
    print(f"\n{OLD_NAME} itself was not touched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
