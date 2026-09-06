"""Import a CapCut desktop draft into a BeatCanvas template.

Everything needed is in ``draft_content.json``: canvas size, frame rate, clip timings,
per-clip transforms, which animation each clip uses, the audio, and any markers. The one
thing *not* in there is how a built-in animation moves, which lives in the cached effect
bundle and is transcribed once into ``animations.py``.

A specific quirk is undone on import. Pendulum-style animations travel along X, so the
usual trick for a vertical bounce is to rotate every photo 90 degrees, apply the
animation, export, then rotate the finished video back. That costs an extra generation
and forces a ~1.8x scale to cover the rotated frame. When that pattern is detected, the
rotation is removed and the animation's axis is switched to Y instead, which produces the
same motion with no extra export and no crop.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import config
from .template import Clip, Template


class ImportError_(Exception):
    """Raised when a draft cannot be understood."""


def _load(draft: Path) -> dict:
    content = draft / "draft_content.json"
    if not content.exists():
        raise ImportError_(f"no draft_content.json in {draft}")
    return json.loads(content.read_text(encoding="utf-8"))


def _index_materials(data: dict) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for group, items in (data.get("materials") or {}).items():
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                index[item["id"]] = {"_group": group, **item}
    return index


def _animation_for(segment: dict, index: dict[str, dict]) -> tuple[str | None, float]:
    """The animation name and its duration in seconds for one segment."""
    for ref in segment.get("extra_material_refs") or []:
        material = index.get(ref)
        if not material or material.get("_group") != "material_animations":
            continue
        for animation in material.get("animations") or []:
            # "group" is CapCut's word for a combo animation, which is what these are.
            if animation.get("name"):
                duration = (animation.get("duration") or 0) / config.US
                return animation["name"], duration
    return None, 0.0


def _beat_markers(data: dict, index: dict[str, dict]) -> list[float]:
    markers: list[float] = []
    for material in index.values():
        if material.get("_group") != "time_marks":
            continue
        for item in material.get("mark_items") or []:
            start = ((item.get("time_range") or {}).get("start") or 0) / config.US
            markers.append(start)
    return sorted(set(markers))


def _audio(data: dict, index: dict[str, dict]) -> tuple[str, str, float]:
    for track in data.get("tracks") or []:
        if track.get("type") != "audio":
            continue
        for segment in track.get("segments") or []:
            material = index.get(segment.get("material_id")) or {}
            source = segment.get("source_timerange") or {}
            return (
                material.get("path", ""),
                material.get("name", ""),
                (source.get("start") or 0) / config.US,
            )
    # Fall back to any audio material even if no audio track is present.
    for material in index.values():
        if material.get("_group") == "audios":
            return material.get("path", ""), material.get("name", ""), 0.0
    return "", "", 0.0


def _video_track(data: dict) -> dict | None:
    """The main picture track: the video track carrying the most segments."""
    candidates = [t for t in (data.get("tracks") or []) if t.get("type") == "video"]
    if not candidates:
        return None
    return max(candidates, key=lambda t: len(t.get("segments") or []))


def _assign_slots(sources: list[str], collapse_repeats: bool) -> list[int]:
    """Map each clip to a picture slot.

    By default every clip gets its own slot, so a 20-clip template asks for 20 pictures
    and the user controls each one. With ``collapse_repeats`` the original edit's reuse
    of the same file is preserved instead, which is what you want if the template
    deliberately shows a picture more than once.
    """
    if not collapse_repeats:
        return list(range(len(sources)))

    slots: list[int] = []
    seen: dict[str, int] = {}
    for name in sources:
        key = name or f"__unique_{len(seen)}"
        if key not in seen:
            seen[key] = len(seen)
        slots.append(seen[key])
    return slots


def import_draft(draft: str | Path, name: str | None = None,
                 collapse_repeats: bool = False,
                 undo_rotate_hack: bool = True) -> Template:
    """Convert a CapCut draft folder into a Template."""
    draft = Path(draft)
    data = _load(draft)
    index = _index_materials(data)

    canvas = data.get("canvas_config") or {}
    width = int(canvas.get("width") or 1080)
    height = int(canvas.get("height") or 1920)
    fps = float(data.get("fps") or 30.0)

    track = _video_track(data)
    if track is None:
        raise ImportError_("no video track found in this draft")

    segments = track.get("segments") or []
    if not segments:
        raise ImportError_("the video track has no segments")

    notes: list[str] = []
    raw: list[dict] = []
    for order, segment in enumerate(segments):
        target = segment.get("target_timerange") or {}
        transform = segment.get("clip") or {}
        scale = transform.get("scale") or {}
        offset = transform.get("transform") or {}
        flip = transform.get("flip") or {}
        material = index.get(segment.get("material_id")) or {}
        animation, animation_duration = _animation_for(segment, index)

        raw.append({
            "order": order,
            "start": (target.get("start") or 0) / config.US,
            "duration": (target.get("duration") or 0) / config.US,
            "animation": animation,
            "animation_duration": animation_duration,
            "scale": float(scale.get("x") or 1.0),
            "rotation": float(transform.get("rotation") or 0.0),
            "offset_x": float(offset.get("x") or 0.0),
            "offset_y": float(offset.get("y") or 0.0),
            "flip_h": bool(flip.get("horizontal")),
            "flip_v": bool(flip.get("vertical")),
            "source_name": Path(material.get("path", "")).name,
        })

    # --- undo the rotate-90 workaround -------------------------------------
    axis = "x"
    rotated = [c for c in raw if abs(abs(c["rotation"]) - 90.0) < 1.0]
    if undo_rotate_hack and rotated:
        share = len(rotated) / len(raw)
        axis = "y"
        for clip in rotated:
            clip["rotation"] = 0.0
            # The ~1.8x scale exists purely to cover the frame after rotating a
            # landscape photo into a portrait canvas. Cover-fit handles that now.
            clip["scale"] = 1.0
        notes.append(
            f"{len(rotated)} of {len(raw)} clips were rotated 90 degrees "
            f"({share:.0%}); converted to a vertical animation axis and the "
            "compensating scale removed"
        )
        if share < 0.999:
            notes.append(
                "the source draft was only partly rotated, so the original export "
                "would have been inconsistent; all clips now use the vertical axis"
            )
        # The workaround also means the CapCut canvas is the wrong way round: the
        # finished video only becomes portrait after the whole export is rotated back.
        # Since the rotation is gone, the template adopts the final orientation now.
        if width > height:
            width, height = height, width
            notes.append(
                f"canvas turned to {width}x{height}: the draft was landscape only "
                "because the finished export was meant to be rotated upright"
            )

    # A near-zero scale is leftover keyframe residue rather than intent, and would
    # render the picture as an invisible speck.
    for clip in raw:
        if 0.0 < clip["scale"] < 0.05:
            notes.append(
                f"clip {clip['order']} had scale {clip['scale']:.3f}, which would be "
                "invisible; treated as 1.0"
            )
            clip["scale"] = 1.0

    slots = _assign_slots([c["source_name"] for c in raw], collapse_repeats)

    clips = [
        Clip(
            index=item["order"],
            slot=slots[position],
            start=item["start"],
            duration=item["duration"],
            animation=item["animation"],
            # A zero-length animation means CapCut applied none; fall back to the clip
            # length so the motion at least fills the time it is on screen.
            animation_duration=item["animation_duration"] or item["duration"],
            scale=item["scale"],
            rotation=item["rotation"],
            offset_x=item["offset_x"],
            offset_y=item["offset_y"],
            flip_horizontal=item["flip_h"],
            flip_vertical=item["flip_v"],
            axis=axis,
            source_name=item["source_name"],
        )
        for position, item in enumerate(raw)
    ]

    audio_path, audio_name, audio_start = _audio(data, index)
    markers = _beat_markers(data, index)

    template = Template(
        name=name or draft.name,
        width=width,
        height=height,
        fps=fps,
        clips=clips,
        audio_path=audio_path,
        audio_name=audio_name,
        audio_start=audio_start,
        beat_markers=markers,
        source_draft=str(draft),
        notes=notes,
    )

    unknown = _unknown_animations(template)
    if unknown:
        template.notes.append(
            "animations not yet in the library, these clips will not move: "
            + ", ".join(sorted(unknown))
        )

    without = [c.index for c in template.clips if not c.animation]
    if without:
        template.notes.append(
            f"{len(without)} of {len(template.clips)} clips have no animation applied "
            f"in the draft (clips {without[0]}-{without[-1]}); they will hold still. "
            "Use fill_animation() to apply one to every clip."
        )
    return template


def fill_animation(template: Template, animation: str,
                   only_missing: bool = True) -> int:
    """Apply an animation to clips that lack one.

    Half-finished drafts are normal: applying a combo to twenty clips by hand in CapCut
    is exactly the tedium this tool exists to remove. Returns how many clips changed.
    """
    # Shells habitually mangle quoted arguments, and an animation name is used as a
    # lookup key, so normalise to the library's own spelling rather than trusting input.
    from . import animations as animation_library
    canonical = animation_library.resolve_name(animation) or animation.strip()
    changed = 0
    for clip in template.clips:
        if only_missing and clip.animation:
            continue
        clip.animation = canonical
        if clip.animation_duration <= 0:
            clip.animation_duration = clip.duration
        changed += 1
    return changed


def _unknown_animations(template: Template) -> set[str]:
    from . import animations

    known = {name.lower() for name in animations.known_names()}
    return {
        clip.animation for clip in template.clips
        if clip.animation and clip.animation.lower() not in known
    }


def find_drafts() -> list[Path]:
    """Every CapCut draft folder on this machine."""
    if not config.CAPCUT_DRAFTS.exists():
        return []
    return sorted(
        entry for entry in config.CAPCUT_DRAFTS.iterdir()
        if entry.is_dir() and (entry / "draft_content.json").exists()
    )
