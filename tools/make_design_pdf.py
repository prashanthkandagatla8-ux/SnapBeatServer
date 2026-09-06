"""Render docs/BeatCanvas-Design.md into a PDF.

    tools\\run.bat tools\\make_design_pdf.py

The Markdown file is the source of truth and stays in the repository where it can be diffed;
the PDF is generated from it so the two cannot drift apart.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pdfwrite  # noqa: E402

from beatcanvas import config  # noqa: E402

SOURCE = config.ROOT / "docs" / "BeatCanvas-Design.md"
TARGET = config.ROOT / "docs" / "BeatCanvas-Design.pdf"


def main() -> int:
    if not SOURCE.exists():
        print(f"source not found: {SOURCE}")
        return 2
    text = SOURCE.read_text(encoding="utf-8")

    # Tables in the source are pipe-delimited for the benefit of Markdown readers. The PDF has
    # no table support, so they are shown as monospaced rows, which keeps the columns aligned
    # without pretending to be a layout engine.
    lines, out, in_table = text.splitlines(), [], False
    for line in lines:
        stripped = line.strip()
        looks_like_row = stripped.startswith("|") and stripped.endswith("|")
        separator = looks_like_row and set(stripped) <= set("|-: ")
        if looks_like_row and not in_table:
            out.append("```")
            in_table = True
        if in_table and not looks_like_row:
            out.append("```")
            in_table = False
        if separator:
            continue
        if in_table:
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            out.append("  ".join(c.ljust(14) for c in cells).rstrip())
        else:
            out.append(line)
    if in_table:
        out.append("```")

    written = pdfwrite.write("\n".join(out), TARGET,
                             running="BeatCanvas - complete design")
    size = written.stat().st_size
    pages = len(pdfwrite.paginate(pdfwrite.layout("\n".join(out))))
    print(f"written : {written}")
    print(f"          {pages} pages, {size / 1024:.0f} KB")
    print(f"source  : {SOURCE}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(3)
