"""Report which analysis libraries are actually available in this environment.

The design leans on madmom / allin1 / demucs. Those are heavy and may not be installed,
and what gets built depends on the answer, so it is worth establishing rather than assuming.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatcanvas import config  # noqa: E402

WANTED = [
    "numpy", "scipy", "cv2", "fastapi", "uvicorn",      # already relied on
    "librosa", "soundfile", "sklearn",                   # useful, mid-weight
    "madmom", "allin1", "demucs", "torch", "essentia",   # the heavy recommendations
]

OUT = config.ROOT / "_logs" / "deps.txt"
lines = [f"python: {sys.version.split()[0]}", f"exe: {sys.executable}", ""]

for name in WANTED:
    try:
        module = importlib.import_module(name)
        version = getattr(module, "__version__", "installed")
        lines.append(f"  {name:12s} {version}")
    except Exception as exc:
        lines.append(f"  {name:12s} MISSING ({type(exc).__name__})")

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines))
