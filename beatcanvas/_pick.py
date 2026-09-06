"""Native Windows file and folder pickers, run as a separate process.

    python -m beatcanvas._pick folder [starting directory]
    python -m beatcanvas._pick music  [starting directory]
    python -m beatcanvas._pick photos [starting directory]

Prints the chosen path to stdout, or nothing if the dialog was cancelled.

Run as its own process on purpose. Tk wants to own a thread and does not take kindly to
being created and destroyed repeatedly inside a web server's worker threads; a separate
process sidesteps that entirely, and a dialog that goes wrong cannot take the server with
it.
"""
from __future__ import annotations

import sys

AUDIO = [
    ("Audio and video", "*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.wma *.mp4 *.mov *.mkv"),
    ("All files", "*.*"),
]
IMAGES = [
    ("Pictures", "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff *.heic"),
    ("All files", "*.*"),
]


def main() -> int:
    kind = sys.argv[1] if len(sys.argv) > 1 else "folder"
    start = sys.argv[2] if len(sys.argv) > 2 else ""

    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:                       # pragma: no cover
        print(f"__error__ tkinter unavailable: {exc}", file=sys.stderr)
        return 2

    root = tk.Tk()
    root.withdraw()
    # Without this the dialog can open behind the browser and look like nothing happened.
    root.attributes("-topmost", True)

    initial = start or None
    if kind == "music":
        chosen = filedialog.askopenfilename(
            title="Choose the music", initialdir=initial, filetypes=AUDIO)
    elif kind == "photos":
        picked = filedialog.askopenfilenames(
            title="Choose photos", initialdir=initial, filetypes=IMAGES)
        chosen = "\n".join(picked) if picked else ""
    else:
        chosen = filedialog.askdirectory(
            title=("Choose the folder to save videos in" if kind == "output"
                   else "Choose the folder with your photos"),
            initialdir=initial, mustexist=(kind != "output"))

    root.destroy()
    if chosen:
        sys.stdout.write(chosen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
