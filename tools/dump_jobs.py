"""Write the current job list to a file, for when console output is unreliable.

Reads the database directly so it works whether or not the server is running.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatcanvas import config  # noqa: E402

OUT = config.ROOT / "_logs" / "jobs_dump.txt"
OUT.parent.mkdir(parents=True, exist_ok=True)

lines: list[str] = []
try:
    database = config.ROOT / "jobs" / "beatcanvas.db"
    lines.append(f"database: {database} (exists={database.exists()})")
    connection = sqlite3.connect(str(database))
    connection.row_factory = sqlite3.Row
    columns = [r["name"] for r in connection.execute("PRAGMA table_info(jobs)")]
    lines.append(f"columns: {columns}\n")
    rows = connection.execute(
        "SELECT * FROM jobs ORDER BY id DESC LIMIT 12").fetchall()
    for row in rows:
        job = dict(row)
        lines.append(f"#{job.get('id')} {job.get('name')} "
                     f"status={job.get('status')} stage={job.get('stage')} "
                     f"template={Path(str(job.get('template'))).name}")
        if job.get("error"):
            lines.append(f"    error: {str(job['error'])[:900]}")
        if job.get("result"):
            try:
                lines.append("    result: "
                             + json.dumps(json.loads(job["result"]))[:700])
            except Exception:
                lines.append(f"    result: {str(job['result'])[:700]}")
        if job.get("options"):
            try:
                opts = json.loads(job["options"])
                opts.pop("photo_order", None)
                lines.append(f"    options: {json.dumps(opts)[:500]}")
            except Exception:
                pass
except Exception:
    lines.append(traceback.format_exc())

OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines))
