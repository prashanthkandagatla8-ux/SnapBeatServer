"""Inspect a CapCut desktop draft and report what is machine-extractable.

This is the go/no-go check for the whole approach. The question is not "is there a JSON
file" but specifically:

  1. can clip durations be read?          -> gives the beat map for free
  2. can per-clip animations be read?     -> tells us which motion to apply
  3. are animation curves readable, or
     only opaque resource ids?            -> decides reimplement vs convert
  4. can the audio and photo paths be read?

Run:
    python tools\\inspect_capcut.py "<draft folder>"
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

US = 1_000_000  # CapCut stores time in microseconds


def load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def describe_structure(data: dict) -> None:
    print("=== top level keys ===")
    for key in sorted(data):
        value = data[key]
        kind = type(value).__name__
        size = f" len={len(value)}" if isinstance(value, (list, dict)) else ""
        print(f"  {key:32s} {kind}{size}")


def describe_canvas(data: dict) -> None:
    print("\n=== canvas / timing ===")
    canvas = data.get("canvas_config") or {}
    print(f"  size      : {canvas.get('width')} x {canvas.get('height')} "
          f"ratio={canvas.get('ratio')}")
    print(f"  fps       : {data.get('fps')}")
    duration = data.get("duration")
    if duration:
        print(f"  duration  : {duration} us  = {duration / US:.3f} s")


def describe_materials(data: dict) -> dict:
    print("\n=== materials ===")
    materials = data.get("materials") or {}
    index: dict[str, dict] = {}
    for group, items in materials.items():
        if not isinstance(items, list) or not items:
            continue
        print(f"  {group:26s} {len(items)}")
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                index[item["id"]] = {"_group": group, **item}
    return index


def describe_photos(index: dict) -> None:
    print("\n=== photo / video sources ===")
    seen = 0
    for item in index.values():
        if item.get("_group") not in ("videos", "images"):
            continue
        path = item.get("path") or ""
        if not path:
            continue
        seen += 1
        if seen <= 25:
            print(f"  [{item.get('type','?'):10s}] {Path(path).name}")
    print(f"  total sources with a path: {seen}")


def describe_audio(index: dict) -> None:
    print("\n=== audio ===")
    for item in index.values():
        if item.get("_group") != "audios":
            continue
        print(f"  name     : {item.get('name')}")
        print(f"  path     : {item.get('path')}")
        print(f"  duration : {(item.get('duration') or 0) / US:.3f} s")


def describe_animations(index: dict) -> None:
    """The critical question: are animations named, and do they carry curves?"""
    print("\n=== animations ===")
    groups = [i for i in index.values() if i.get("_group") == "material_animations"]
    if not groups:
        print("  none found under materials.material_animations")
    for group in groups:
        for anim in group.get("animations") or []:
            print(f"  name={anim.get('name')!r} type={anim.get('type')!r} "
                  f"duration={(anim.get('duration') or 0) / US:.3f}s "
                  f"start={(anim.get('start') or 0) / US:.3f}s")
            extras = {k: v for k, v in anim.items()
                      if k not in ("name", "type", "duration", "start", "id")}
            keys = ", ".join(sorted(extras))
            print(f"        other fields: {keys}")
            # If there is anything curve-like, say so explicitly.
            for key in ("keyframes", "curve", "path", "points", "material_type"):
                if key in anim:
                    print(f"        !! '{key}' present -> {str(anim[key])[:120]}")


def describe_tracks(data: dict, index: dict) -> list[dict]:
    print("\n=== tracks ===")
    clips: list[dict] = []
    for track in data.get("tracks") or []:
        segments = track.get("segments") or []
        print(f"  track type={track.get('type'):10s} segments={len(segments)}")
        if track.get("type") != "video":
            continue
        for order, segment in enumerate(segments):
            target = segment.get("target_timerange") or {}
            source = segment.get("source_timerange") or {}
            material = index.get(segment.get("material_id"), {})
            clip = {
                "order": order,
                "start_s": (target.get("start") or 0) / US,
                "duration_s": (target.get("duration") or 0) / US,
                "source_start_s": (source.get("start") or 0) / US,
                "material": Path(material.get("path", "")).name,
                "clip_transform": segment.get("clip") or {},
                "animation_ids": [
                    ref for ref in (segment.get("extra_material_refs") or [])
                    if index.get(ref, {}).get("_group") == "material_animations"
                ],
                "keyframes": segment.get("common_keyframes") or [],
            }
            clips.append(clip)
    return clips


def report_clips(clips: list[dict]) -> None:
    if not clips:
        print("\nno video segments found")
        return
    print(f"\n=== clip timing ({len(clips)} clips) ===")
    print(f"  {'#':>3} {'start':>8} {'dur':>7}  {'scale':>16} {'rot':>7}  source")
    total = 0.0
    for clip in clips:
        transform = clip["clip_transform"] or {}
        scale = transform.get("scale") or {}
        scale_text = f"{scale.get('x', 1):.3f},{scale.get('y', 1):.3f}"
        rotation = transform.get("rotation", 0)
        print(f"  {clip['order']:3d} {clip['start_s']:8.3f} {clip['duration_s']:7.3f}  "
              f"{scale_text:>16} {rotation:7.1f}  {clip['material'][:34]}")
        total += clip["duration_s"]
    print(f"  {'':3} {'':8} {total:7.3f}  <- summed clip durations")

    print("\n  duration list (what the tutorial calls the beat map):")
    print("  " + ", ".join(f"{c['duration_s']:.2f}" for c in clips))

    with_keys = sum(1 for c in clips if c["keyframes"])
    with_anim = sum(1 for c in clips if c["animation_ids"])
    print(f"\n  clips carrying explicit keyframes : {with_keys} / {len(clips)}")
    print(f"  clips carrying an animation ref    : {with_anim} / {len(clips)}")


def verdict(clips: list[dict], index: dict) -> None:
    print("\n" + "=" * 66)
    print("VERDICT")
    anims = [i for i in index.values() if i.get("_group") == "material_animations"]
    named = []
    for group in anims:
        for anim in group.get("animations") or []:
            if anim.get("name"):
                named.append(anim["name"])

    if clips:
        print("  [YES] clip durations are readable -> beat map extracts automatically")
    else:
        print("  [NO ] no clip timing found")

    if named:
        print(f"  [YES] animations are named: {sorted(set(named))}")
        print("        -> we know WHICH motion, so each one is reimplemented once")
    else:
        print("  [ ? ] no animation names found on this project")

    any_curve = any(c["keyframes"] for c in clips)
    if any_curve:
        print("  [YES] explicit keyframes present -> direct conversion possible")
    else:
        print("  [NO ] no explicit keyframes: CapCut's built-in animations are opaque.")
        print("        Motion must be reimplemented and verified against the export.")


def main() -> int:
    if len(sys.argv) > 1:
        draft = Path(sys.argv[1])
    else:
        draft = Path(r"C:\Users\prash\AppData\Local\CapCut\User Data\Projects"
                     r"\com.lveditor.draft\0817 (1)")

    content = draft / "draft_content.json"
    if not content.exists():
        print(f"draft_content.json not found in {draft}")
        return 2

    size_mb = content.stat().st_size / (1024 * 1024)
    print(f"draft : {draft.name}")
    print(f"file  : draft_content.json  {size_mb:.2f} MB\n")

    data = load(content)
    describe_structure(data)
    describe_canvas(data)
    index = describe_materials(data)
    describe_photos(index)
    describe_audio(index)
    describe_animations(index)
    clips = describe_tracks(data, index)
    report_clips(clips)
    verdict(clips, index)

    out = Path(__file__).resolve().parent.parent / "_analysis"
    out.mkdir(parents=True, exist_ok=True)
    (out / "clips.json").write_text(json.dumps(clips, indent=2), encoding="utf-8")
    print(f"\nwrote {out / 'clips.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
