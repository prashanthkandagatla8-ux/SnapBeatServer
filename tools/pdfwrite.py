"""A very small PDF writer, because no PDF library is installed.

The environment has numpy, OpenCV, FastAPI and uvicorn and nothing else, so reportlab and
friends are not available and installing them for one document would be a poor trade. PDF's
text model is simple enough to emit directly: a catalogue, a page tree, one content stream
per page, three standard fonts that every reader already has, and a cross-reference table.

What this supports is deliberately narrow -- headings, body text, bullets, monospaced
blocks, rules and page numbers -- because that is all a written design needs. There is no
image support, no colour beyond grey, and no font embedding.

Markup accepted by :func:`render`:

    # text        document title, own page position
    ## text       chapter heading
    ### text      section heading
    #### text     minor heading
    - text        bullet
    * text        sub-bullet
    > text        indented note
    ```          toggles a monospaced block
    ---          horizontal rule
    (blank)      paragraph break
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: A4 in points, which is what PDF measures everything in.
PAGE_W, PAGE_H = 595.28, 841.89
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 56.0, 60.0, 54.0

REGULAR, BOLD, MONO = "F1", "F2", "F3"

#: Average glyph width as a fraction of point size. Courier is exactly 0.6 because it is
#: monospaced; the Helvetica figure is an average measured over lower-case prose, which is
#: close enough for line breaking and errs slightly towards breaking early.
WIDTH = {REGULAR: 0.503, BOLD: 0.535, MONO: 0.600}


@dataclass
class Line:
    """One laid-out line, ready to be placed on a page."""

    text: str
    font: str = REGULAR
    size: float = 10.0
    indent: float = 0.0
    space_before: float = 0.0
    space_after: float = 0.0
    rule: bool = False
    keep_with_next: bool = False

    @property
    def height(self) -> float:
        return self.size * 1.36 if not self.rule else 8.0


def _escape(text: str) -> str:
    """PDF string escaping, plus a fold to ASCII.

    Non-ASCII would need a font with a matching encoding and there is no reason to take on
    font embedding for a design document, so the few characters that creep in from typing
    are mapped to their ASCII equivalents rather than dropped silently.
    """
    folded = (text.replace("\u2014", "--").replace("\u2013", "-")
                  .replace("\u2018", "'").replace("\u2019", "'")
                  .replace("\u201c", '"').replace("\u201d", '"')
                  .replace("\u2192", "->").replace("\u00d7", "x")
                  .replace("\u2265", ">=").replace("\u2264", "<=")
                  .replace("\u00b0", " deg").replace("\u2026", "..."))
    folded = folded.encode("ascii", "replace").decode("ascii")
    return folded.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _wrap(text: str, font: str, size: float, width: float) -> list[str]:
    """Break text to fit ``width`` points, keeping whole words where possible."""
    limit = max(8, int(width / (size * WIDTH[font])))
    if not text:
        return [""]
    words, lines, current = text.split(" "), [], ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            lines.append(current)
        # A single word longer than the line, such as a long path, is cut rather than
        # allowed to run off the page.
        while len(word) > limit:
            lines.append(word[:limit])
            word = word[limit:]
        current = word
    if current:
        lines.append(current)
    return lines


def layout(source: str) -> list[Line]:
    """Turn the marked-up text into a flat list of laid-out lines."""
    body_width = PAGE_W - 2 * MARGIN_X
    out: list[Line] = []
    mono = False

    for raw in source.splitlines():
        stripped = raw.rstrip()

        if stripped.strip().startswith("```"):
            mono = not mono
            out.append(Line("", size=4.0))
            continue

        if mono:
            for piece in _wrap(stripped, MONO, 8.2, body_width - 10.0):
                out.append(Line(piece, MONO, 8.2, indent=10.0))
            continue

        if not stripped:
            out.append(Line("", size=5.0))
            continue

        if stripped == "---":
            out.append(Line("", rule=True, space_before=6.0, space_after=8.0))
            continue

        for marker, font, size, indent, before, keep in (
                ("#### ", BOLD, 10.5, 0.0, 9.0, True),
                ("### ", BOLD, 12.0, 0.0, 13.0, True),
                ("## ", BOLD, 15.5, 0.0, 19.0, True),
                ("# ", BOLD, 23.0, 0.0, 0.0, True)):
            if stripped.startswith(marker):
                text = stripped[len(marker):]
                pieces = _wrap(text, font, size, body_width)
                for index, piece in enumerate(pieces):
                    out.append(Line(piece, font, size, indent,
                                    space_before=before if index == 0 else 0.0,
                                    space_after=3.0 if index == len(pieces) - 1 else 0.0,
                                    keep_with_next=keep))
                break
        else:
            if stripped.startswith("- ") or stripped.startswith("* "):
                sub = stripped.startswith("* ")
                indent = 22.0 if sub else 10.0
                bullet = "-" if sub else "\u2022"
                text = stripped[2:]
                pieces = _wrap(text, REGULAR, 10.0, body_width - indent - 10.0)
                for index, piece in enumerate(pieces):
                    out.append(Line(
                        (f"{bullet}  {piece}" if index == 0 else f"    {piece}"),
                        REGULAR, 10.0, indent))
            elif stripped.startswith("> "):
                for piece in _wrap(stripped[2:], REGULAR, 9.6,
                                   body_width - 26.0):
                    out.append(Line(piece, REGULAR, 9.6, indent=26.0))
            else:
                for piece in _wrap(stripped, REGULAR, 10.0, body_width):
                    out.append(Line(piece, REGULAR, 10.0))
    return out


def paginate(lines: list[Line]) -> list[list[tuple[Line, float]]]:
    """Assign lines to pages, returning each with its baseline y."""
    pages: list[list[tuple[Line, float]]] = []
    page: list[tuple[Line, float]] = []
    y = PAGE_H - MARGIN_TOP

    index = 0
    while index < len(lines):
        line = lines[index]
        y -= line.space_before

        # A heading at the very foot of a page is worse than a slightly short page, so it
        # moves down with the text it introduces.
        need = line.height
        if line.keep_with_next:
            look = index + 1
            while look < len(lines) and look < index + 4:
                need += lines[look].height
                look += 1

        if y - need < MARGIN_BOTTOM and page:
            pages.append(page)
            page, y = [], PAGE_H - MARGIN_TOP
            y -= 0.0 if line.space_before else 0.0

        page.append((line, y))
        y -= line.height + line.space_after
        index += 1

    if page:
        pages.append(page)
    return pages


def _content(page: list[tuple[Line, float]], number: int, total: int,
             running: str) -> bytes:
    parts: list[str] = []

    if number > 1:
        parts.append("q 0.75 g BT /F1 8 Tf 1 0 0 1 "
                     f"{MARGIN_X:.1f} {PAGE_H - 40:.1f} Tm ({_escape(running)}) Tj ET Q")
        parts.append(f"q 0.85 G 0.6 w {MARGIN_X:.1f} {PAGE_H - 46:.1f} m "
                     f"{PAGE_W - MARGIN_X:.1f} {PAGE_H - 46:.1f} l S Q")

    for line, y in page:
        if line.rule:
            parts.append(f"q 0.8 G 0.6 w {MARGIN_X:.1f} {y:.1f} m "
                         f"{PAGE_W - MARGIN_X:.1f} {y:.1f} l S Q")
            continue
        if not line.text:
            continue
        x = MARGIN_X + line.indent
        parts.append(
            f"BT /{line.line_font if hasattr(line, 'line_font') else line.font} "
            f"{line.size:.1f} Tf 1 0 0 1 {x:.1f} {y:.1f} Tm "
            f"({_escape(line.text)}) Tj ET")

    footer = f"{number} of {total}"
    parts.append("q 0.55 g BT /F1 8 Tf 1 0 0 1 "
                 f"{PAGE_W / 2 - len(footer) * 2.2:.1f} {MARGIN_BOTTOM - 24:.1f} Tm "
                 f"({_escape(footer)}) Tj ET Q")
    return "\n".join(parts).encode("latin-1", "replace")


def write(source: str, target: Path, running: str = "") -> Path:
    """Render ``source`` and write a PDF to ``target``."""
    pages = paginate(layout(source))
    total = len(pages)

    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font_ids = {
        REGULAR: add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
                     b"/Encoding /WinAnsiEncoding >>"),
        BOLD: add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
                  b"/Encoding /WinAnsiEncoding >>"),
        MONO: add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier "
                  b"/Encoding /WinAnsiEncoding >>"),
    }
    resources = (f"<< /Font << /F1 {font_ids[REGULAR]} 0 R /F2 {font_ids[BOLD]} 0 R "
                 f"/F3 {font_ids[MONO]} 0 R >> >>").encode("latin-1")

    pages_id = add(b"PLACEHOLDER")          # patched once the kids are known
    page_ids: list[int] = []
    for number, page in enumerate(pages, start=1):
        stream = _content(page, number, total, running)
        stream_id = add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
                        + stream + b"\nendstream")
        page_ids.append(add(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 "
            f"{PAGE_W:.2f} {PAGE_H:.2f}] /Resources ".encode("latin-1")
            + resources
            + f" /Contents {stream_id} 0 R >>".encode("latin-1")))

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[pages_id - 1] = (f"<< /Type /Pages /Count {len(page_ids)} "
                             f"/Kids [{kids}] >>").encode("latin-1")
    catalog_id = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("latin-1"))

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode("latin-1") + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("latin-1")
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF\n").encode("latin-1")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(bytes(out))
    return target
