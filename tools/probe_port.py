"""Report whether the app is listening, and what its log last said."""
from __future__ import annotations

import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatcanvas import config  # noqa: E402

OUT = config.ROOT / "_logs" / "probe.txt"
lines = []

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
    probe.settimeout(0.5)
    listening = probe.connect_ex(("127.0.0.1", 8772)) == 0
lines.append(f"listening on 8772: {listening}")

log = config.ROOT / "_logs" / "server.log"
lines.append(f"server.log exists: {log.exists()}")
if log.exists():
    text = log.read_text(encoding="utf-8", errors="replace").splitlines()
    lines.append(f"server.log has {len(text)} lines; last 25:")
    lines += [f"  {line[:160]}" for line in text[-25:]]

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines))
