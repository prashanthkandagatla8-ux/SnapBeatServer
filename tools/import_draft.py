"""Import a CapCut draft into a BeatCanvas template.

    python tools\\import_draft.py                       # list available drafts
    python tools\\import_draft.py --draft "0817 (1)"    # import one
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatcanvas import animations, capcut, config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft", default="", help="draft folder name or full path")
    parser.add_argument("--name", default="", help="template name")
    parser.add_argument("--collapse-repeats", action="store_true",
                        help="reuse one slot where the edit reused the same file")
    parser.add_argument("--keep-rotate-hack", action="store_true",
                        help="do not convert 90-degree rotated clips to a vertical axis")
    parser.add_argument("--fill", default="",
                        help="apply this animation to clips that have none, "
                             "e.g. --fill \"Pendulum 1\"")
    args = parser.parse_args()

    drafts = capcut.find_drafts()
    if not args.draft:
        print(f"CapCut drafts in {config.CAPCUT_DRAFTS}:")
        for entry in drafts:
            print(f"  {entry.name}")
        if not drafts:
            print("  none found")
        return 0

    candidate = Path(args.draft)
    if not candidate.exists():
        # Draft folder names contain spaces and brackets, which shells mangle. Fall back
        # to a loose match on the alphanumeric part so "0817" finds "0817 (1)".
        def squash(text: str) -> str:
            return "".join(ch for ch in text.lower() if ch.isalnum())

        wanted = squash(args.draft)
        matches = [d for d in drafts if squash(d.name) == wanted]
        if not matches:
            matches = [d for d in drafts if wanted and wanted in squash(d.name)]
        if not matches:
            print(f"draft not found: {args.draft!r}")
            print("available:")
            for entry in drafts:
                print(f"  {entry.name}")
            return 2
        if len(matches) > 1:
            print(f"{args.draft!r} matches several drafts, be more specific:")
            for entry in matches:
                print(f"  {entry.name}")
            return 2
        candidate = matches[0]
        print(f"matched draft: {candidate.name}\n")

    template = capcut.import_draft(
        candidate,
        name=args.name or None,
        collapse_repeats=args.collapse_repeats,
        undo_rotate_hack=not args.keep_rotate_hack,
    )

    if args.fill:
        filled = capcut.fill_animation(template, args.fill)
        print(f"applied '{args.fill}' to {filled} clip(s) that had no animation\n")

    for line in template.summary():
        print(line)

    print("\nclip timing:")
    print(f"  {'#':>3} {'slot':>4} {'start':>8} {'dur':>7} {'anim dur':>9} "
          f"{'scale':>6} {'rot':>6} {'axis':>5}  animation")
    for clip in template.clips:
        print(f"  {clip.index:3d} {clip.slot:4d} {clip.start:8.3f} {clip.duration:7.3f} "
              f"{clip.animation_duration:9.3f} {clip.scale:6.3f} {clip.rotation:6.1f} "
              f"{clip.axis:>5}  {clip.animation or '-'}")

    print(f"\n  durations: {', '.join(f'{d:.2f}' for d in template.durations())}")

    print("\nanimation library:")
    for name in animations.known_names():
        for line in animations.describe(animations.get(name)):
            print(f"  {line}")

    path = template.save()
    print(f"\nsaved template -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
