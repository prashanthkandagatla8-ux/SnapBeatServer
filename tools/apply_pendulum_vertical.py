"""Apply the Pendulum Vertical (bottom-to-top) combo animation to clips in a CapCut project.

Usage:
    python apply_pendulum_vertical.py <draft_folder>

Example:
    python apply_pendulum_vertical.py "C:/Users/prash/AppData/Local/CapCut/User Data/Projects/com.lveditor.draft/0817 (1)"

This script:
  1. Ensures the Pendulum Vertical effect bundle exists in CapCut's local cache.
  2. Reads the project's draft_content.json.
  3. Replaces every Pendulum 1 combo animation with Pendulum Vertical,
     or adds Pendulum Vertical to clips that have no combo animation yet.
  4. Backs up the original and writes the modified draft.

After running, reopen the project in CapCut to see the vertical pendulum.
"""
from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────────────

CAPCUT_CACHE = Path.home() / "AppData/Local/CapCut/User Data/Cache/effect"

ORIGINAL_ID = "6811007755785081357"
VERTICAL_ID = "6811007755785081358"
VERTICAL_NAME = "Pendulum Vertical"

ORIGINAL_HASH = "47cc5d94c283293166c3d3d3fde86655"


# ── Ensure the vertical effect bundle exists ───────────────────────────────────

def ensure_vertical_bundle() -> Path:
    """Copy the Pendulum 1 bundle and patch it to vertical if not already done."""
    src = CAPCUT_CACHE / ORIGINAL_ID / ORIGINAL_HASH
    dst_root = CAPCUT_CACHE / VERTICAL_ID
    dst = dst_root / ORIGINAL_HASH

    if not src.exists():
        print(f"ERROR: Pendulum 1 bundle not found at {src}")
        print("       Open CapCut, apply Pendulum 1 to any clip, then retry.")
        sys.exit(1)

    if dst.exists() and (dst / "Transform.lua").exists():
        content = (dst / "Transform.lua").read_text(encoding="utf-8")
        if "Pendulum Vertical" in content:
            print(f"  [OK] Vertical bundle already exists at {dst}")
            return dst

    # Copy entire bundle
    if dst_root.exists():
        shutil.rmtree(dst_root)
    shutil.copytree(src.parent, dst_root)
    print(f"  [OK] Copied bundle to {dst_root}")

    # Patch Transform.lua: swap X motion -> Y motion (bottom-to-top)
    lua_path = dst / "Transform.lua"
    lua = lua_path.read_text(encoding="utf-8")

    # Replace the action definitions
    lua = lua.replace(
        "-- peizhidonghuaxiaoguo",
        "-- peizhidonghuaxiaoguo (Pendulum Vertical - bottom to top)"
    )

    # Position phase 1: X(-0.6,0,0)->(-0.1,0,0) becomes Y(0,0.6,0)->(0,0.1,0)
    lua = lua.replace(
        "startPosition = Amaz.Vector3f(-0.6, 0.0, 0.0)",
        "startPosition = Amaz.Vector3f(0.0, 0.6, 0.0)",
        1  # only first occurrence
    )
    lua = lua.replace(
        "endPosition = Amaz.Vector3f(-0.1, 0.0, 0.0)",
        "endPosition = Amaz.Vector3f(0.0, 0.1, 0.0)",
        1
    )

    # Position phase 2: X(-0.1,0,0)->(-0.6,0,0) becomes Y(0,0.1,0)->(0,0.6,0)
    lua = lua.replace(
        "startPosition = Amaz.Vector3f(-0.1, 0.0, 0.0)",
        "startPosition = Amaz.Vector3f(0.0, 0.1, 0.0)",
        1
    )
    lua = lua.replace(
        "endPosition = Amaz.Vector3f(-0.6, 0.0, 0.0)",
        "endPosition = Amaz.Vector3f(0.0, 0.6, 0.0)",
        1
    )

    # Blur direction: horizontal -> vertical
    lua = lua.replace(
        "blurDirection = Amaz.Vector2f(1, 0)",
        "blurDirection = Amaz.Vector2f(0, 1)"
    )

    lua_path.write_text(lua, encoding="utf-8")
    print(f"  [OK] Patched Transform.lua for vertical motion")
    return dst


# -- Patch a CapCut draft -------------------------------------------------------

def patch_draft(draft_dir: Path, mode: str = "replace") -> None:
    """Patch a CapCut draft to use Pendulum Vertical.

    mode:
      "replace" - swap every Pendulum 1 for Pendulum Vertical
      "all"     - also add Pendulum Vertical to clips that have no combo animation
    """
    content_path = draft_dir / "draft_content.json"
    if not content_path.exists():
        print(f"ERROR: {content_path} not found. Is this a CapCut draft folder?")
        sys.exit(1)

    bundle_path = ensure_vertical_bundle()

    # Backup
    backup = content_path.with_suffix(".json.bak")
    if not backup.exists():
        shutil.copy2(content_path, backup)
        print(f"  [OK] Backed up to {backup.name}")

    data = json.loads(content_path.read_text(encoding="utf-8"))

    # Build the animation entry template
    vertical_anim = {
        "id": VERTICAL_ID,
        "type": "group",
        "start": 0,
        "duration": 2400000,  # default, CapCut overrides per-clip
        "path": str(bundle_path).replace("\\", "/"),
        "platform": "all",
        "resource_id": VERTICAL_ID,
        "third_resource_id": VERTICAL_ID,
        "source_platform": 1,
        "name": VERTICAL_NAME,
        "category_id": "2037708350",
        "category_name": "Custom",
        "panel": "video",
        "material_type": "video",
        "anim_adjust_params": None,
        "request_id": "",
    }

    # Process material_animations
    mat_anims = data.get("materials", {}).get("material_animations", [])
    replaced = 0
    added = 0

    for mat in mat_anims:
        anims = mat.get("animations", [])

        # Replace existing Pendulum 1
        for anim in anims:
            if anim.get("resource_id") == ORIGINAL_ID or anim.get("name") == "Pendulum 1":
                anim["id"] = VERTICAL_ID
                anim["path"] = str(bundle_path).replace("\\", "/")
                anim["resource_id"] = VERTICAL_ID
                anim["third_resource_id"] = VERTICAL_ID
                anim["name"] = VERTICAL_NAME
                anim["category_name"] = "Custom"
                replaced += 1

        # Optionally add to empty slots
        if mode == "all" and not anims:
            mat["animations"] = [dict(vertical_anim)]
            added += 1

    content_path.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8"
    )

    print(f"\n  Done! Replaced {replaced} Pendulum 1 -> Pendulum Vertical")
    if added:
        print(f"        Added Pendulum Vertical to {added} empty animation slots")
    print(f"\n  -> Now reopen the project '{draft_dir.name}' in CapCut.")


def get_latest_draft() -> Path | None:
    """Find the most recently modified CapCut draft folder."""
    drafts_dir = Path.home() / "AppData/Local/CapCut/User Data/Projects/com.lveditor.draft"
    if not drafts_dir.exists():
        return None
    valid = []
    for d in drafts_dir.iterdir():
        if d.is_dir() and (d / "draft_content.json").exists():
            # Get latest modified time of draft_content.json
            mtime = (d / "draft_content.json").stat().st_mtime
            valid.append((mtime, d))
    if not valid:
        return None
    valid.sort(key=lambda x: x[0], reverse=True)
    return valid[0][1]


# -- CLI ------------------------------------------------------------------------

def main():
    mode = "replace"
    target_path: Path | None = None

    for arg in sys.argv[1:]:
        if arg.startswith("--mode="):
            mode = arg.split("=", 1)[1]
        elif not target_path:
            target_path = Path(arg)

    if not target_path or str(target_path).lower() in ("latest", "--latest", "-l"):
        latest = get_latest_draft()
        if not latest:
            print("ERROR: No CapCut projects found in AppData/Local/CapCut/User Data/Projects.")
            return
        target_path = latest
        print(f"  [Auto-Detected Latest Project] -> '{target_path.name}'")

    print(f"\n  Pendulum Vertical Patcher")
    print(f"  ========================")
    print(f"  Draft: {target_path}")
    print(f"  Mode:  {mode}\n")

    patch_draft(target_path, mode)


if __name__ == "__main__":
    main()
