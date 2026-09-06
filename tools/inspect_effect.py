"""Two follow-up questions the first pass raised.

1. CapCut stored a 'beats' material and a 'time_marks' material. If those hold beat
   positions, the beat map is available even for clips the user has not cut yet.

2. 'Pendulum 1' points at a cached effect bundle on disk. If that bundle is readable
   (JSON, Lottie, or a parameter list), the motion can be converted instead of
   reimplemented by eye. That is the difference between hours and minutes per animation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

US = 1_000_000

DRAFT = Path(r"C:\Users\prash\AppData\Local\CapCut\User Data\Projects"
             r"\com.lveditor.draft\0817 (1)")
EFFECT_CACHE = Path(r"C:\Users\prash\AppData\Local\CapCut\User Data\Cache\effect")


def main() -> int:
    data = json.loads((DRAFT / "draft_content.json").read_text(encoding="utf-8"))
    materials = data.get("materials", {})

    print("=== beats material ===")
    for item in materials.get("beats", []):
        for key, value in item.items():
            text = json.dumps(value) if isinstance(value, (list, dict)) else str(value)
            print(f"  {key:24s} {text[:400]}")

    print("\n=== time_marks material ===")
    for item in materials.get("time_marks", []):
        for key, value in item.items():
            text = json.dumps(value) if isinstance(value, (list, dict)) else str(value)
            print(f"  {key:24s} {text[:400]}")

    print("\n=== keyframes block (top level) ===")
    for key, value in (data.get("keyframes") or {}).items():
        length = len(value) if isinstance(value, list) else "-"
        print(f"  {key:24s} len={length}")

    print("\n=== animation adjust params ===")
    seen = set()
    for group in materials.get("material_animations", []):
        for anim in group.get("animations") or []:
            name = anim.get("name")
            key = (name, json.dumps(anim.get("anim_adjust_params"), sort_keys=True))
            if key in seen:
                continue
            seen.add(key)
            print(f"  {name}: {json.dumps(anim.get('anim_adjust_params'))}")
            print(f"    resource_id={anim.get('resource_id')} "
                  f"category={anim.get('category_name')} panel={anim.get('panel')}")

    print("\n=== effect cache bundle ===")
    target = EFFECT_CACHE / "6811007755785081357"
    if not target.exists():
        print(f"  not found: {target}")
        # Show what is cached, in case the id differs.
        if EFFECT_CACHE.exists():
            for child in list(EFFECT_CACHE.iterdir())[:20]:
                print(f"  cached: {child.name}")
        return 0

    for path in sorted(target.rglob("*")):
        if path.is_dir():
            print(f"  [DIR ] {path.relative_to(target)}")
            continue
        size = path.stat().st_size
        print(f"  [FILE] {path.relative_to(target)}  {size} bytes")
        # Peek at anything small and text-like.
        if size < 200_000:
            head = path.read_bytes()[:400]
            printable = sum(1 for b in head if 32 <= b < 127 or b in (9, 10, 13))
            if head and printable / len(head) > 0.85:
                print("         text head: "
                      + head.decode("utf-8", "replace").replace("\n", " ")[:260])
            else:
                print(f"         binary, magic={head[:8]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
