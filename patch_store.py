import os
import re
from pathlib import Path

root = Path(r"C:\MyProjects\SnapBeatServer\beatcanvas")

# 1. Patch store.py
store_path = root / "store.py"
with open(store_path, "r", encoding="utf-8") as f:
    store_code = f.read()

# Add WAL mode
wal_patch = """        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA busy_timeout=5000;")
            self._conn.executescript(_SCHEMA)"""
store_code = store_code.replace("""        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)""", wal_patch)

# Fix recovery loop
recovery_old = """    def _recover(self) -> None:
        \"\"\"Requeue anything left mid-flight by an unclean shutdown.

        Renders are quick here, so restarting one costs seconds. Requeuing is friendlier
        than marking it failed and making the user set it up again.
        \"\"\"
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status = 'queued' WHERE status IN ('queued', 'previewing', 'rendering')"
            )
            self._conn.commit()"""

recovery_new = """    def _recover(self) -> None:
        \"\"\"Requeue anything left mid-flight by an unclean shutdown.\"\"\"
        import json
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, options FROM jobs WHERE status IN ('previewing', 'rendering')"
            ).fetchall()
            for row in rows:
                opts = json.loads(row["options"] or "{}")
                retries = opts.get("_retries", 0)
                if retries >= 3:
                    self._conn.execute(
                        "UPDATE jobs SET status = 'failed', stage = 'failed after 3 retries' WHERE id = ?",
                        (row["id"],)
                    )
                else:
                    opts["_retries"] = retries + 1
                    self._conn.execute(
                        "UPDATE jobs SET status = 'queued', options = ? WHERE id = ?",
                        (json.dumps(opts), row["id"])
                    )
            self._conn.commit()"""
store_code = store_code.replace(recovery_old, recovery_new)

with open(store_path, "w", encoding="utf-8") as f:
    f.write(store_code)

print("store.py patched")
