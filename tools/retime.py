"""Analyse the music and rebuild a template's cut points on real onsets.

    # just look at what the music does
    python tools\\retime.py --template templates\\0817-1.json --analyse-only

    # snap existing cuts onto the nearest detected hit, keeping the edit shape
    python tools\\retime.py --template templates\\0817-1.json --mode snap

    # rebuild cuts from scratch on every detected onset
    python tools\\retime.py --template templates\\0817-1.json --mode rebuild --clips 24
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatcanvas import beats  # noqa: E402
from beatcanvas.template import Clip, Template  # noqa: E402

#: Travel direction -> internal axis. Screen Y grows downward, so rising is "-y".
DIRECTIONS = {"up": "-y", "down": "y", "right": "x", "left": "-x"}


def show_analysis(analysis: beats.BeatAnalysis, window: float) -> None:
    print(f"duration : {analysis.duration:.2f}s")
    print(f"tempo    : {analysis.tempo_bpm:.1f} BPM "
          f"(beat every {60.0 / max(analysis.tempo_bpm, 1e-6):.3f}s)")
    print(f"onsets   : {len(analysis.onsets)} detected")

    early = analysis.in_range(0.0, window)
    print(f"\nfirst {window:g}s, {len(early)} onsets:")
    for index, time_s in enumerate(early):
        gap = time_s - early[index - 1] if index else 0.0
        strength = analysis.strengths[analysis.onsets.index(time_s)]
        bar = "#" * max(1, int(round(strength * 40)))
        print(f"  {index:3d} {time_s:7.3f}s  gap {gap:5.3f}s  {bar}")


def compare(template: Template, analysis: beats.BeatAnalysis) -> None:
    """Show which existing cuts sit on a hit and which do not."""
    print("\nexisting cuts vs detected onsets:")
    print(f"  {'#':>3} {'cut':>8} {'nearest':>8} {'error':>7}")
    misses = 0
    for clip in template.clips:
        if not analysis.onsets:
            break
        nearest = min(analysis.onsets, key=lambda t: abs(t - clip.start))
        error = clip.start - nearest
        flag = "" if abs(error) <= 0.06 else "  <- off the beat"
        if abs(error) > 0.06:
            misses += 1
        print(f"  {clip.index:3d} {clip.start:8.3f} {nearest:8.3f} {error:+7.3f}{flag}")
    print(f"\n  {misses} of {len(template.clips)} cuts are more than 60ms off a hit")

    # How many real onsets fall inside the template's span with no cut on them.
    span_end = template.duration
    inside = analysis.in_range(0.0, span_end)
    cut_times = [clip.start for clip in template.clips]
    unused = [t for t in inside
              if all(abs(t - cut) > 0.08 for cut in cut_times)]
    print(f"  {len(unused)} onsets inside the first {span_end:.2f}s have no cut:")
    print("   " + ", ".join(f"{t:.2f}" for t in unused[:40]))


def rebuild(template: Template, analysis: beats.BeatAnalysis, clips: int,
            min_duration: float, axis: str) -> Template:
    durations = beats.cuts_from_onsets(
        analysis.onsets, start=0.0, end=None,
        min_duration=min_duration, max_clips=clips,
    )
    if not durations:
        raise SystemExit("no usable onsets found")

    animation = next((c.animation for c in template.clips if c.animation), "Pendulum 1")
    new_clips: list[Clip] = []
    cursor = 0.0
    for index, duration in enumerate(durations):
        new_clips.append(Clip(
            index=index,
            slot=index,
            start=round(cursor, 4),
            duration=round(duration, 4),
            animation=animation,
            animation_duration=round(duration, 4),
            axis=axis,
        ))
        cursor += duration

    rebuilt = Template.from_dict(template.to_dict())
    rebuilt.clips = new_clips
    rebuilt.notes = list(template.notes) + [
        f"cut points rebuilt from {len(analysis.onsets)} detected onsets "
        f"at {analysis.tempo_bpm:.1f} BPM, minimum clip {min_duration:g}s"
    ]
    return rebuilt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", required=True)
    parser.add_argument("--audio", default="", help="defaults to the template's audio")
    parser.add_argument("--mode", choices=["snap", "rebuild"], default="snap")
    parser.add_argument("--analyse-only", action="store_true")
    parser.add_argument("--clips", type=int, default=0,
                        help="how many clips to build; 0 keeps the current count")
    parser.add_argument("--min-duration", type=float, default=0.18)
    parser.add_argument("--sensitivity", type=float, default=1.0,
                        help="higher finds more onsets")
    # Plain words rather than signed axis names: argparse treats a value like "-y" as
    # another flag, and "up" is clearer to read back later anyway.
    parser.add_argument("--direction", default="up",
                        choices=sorted(DIRECTIONS),
                        help="which way the picture travels (default up)")
    parser.add_argument("--window", type=float, default=13.0,
                        help="how many seconds to list in the report")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    axis = DIRECTIONS[args.direction]

    template = Template.load(args.template)
    audio = Path(args.audio or template.audio_path)
    if not audio.exists():
        print(f"audio not found: {audio}")
        return 2

    print(f"template : {template.name}")
    print(f"audio    : {audio.name}\n")

    analysis = beats.analyse(
        audio,
        duration=max(20.0, template.duration + 5.0),
        sensitivity=args.sensitivity,
        min_gap=max(0.08, args.min_duration * 0.5),
    )
    show_analysis(analysis, args.window)
    compare(template, analysis)

    if args.analyse_only:
        return 0

    if args.mode == "snap":
        starts = beats.snap([c.start for c in template.clips], analysis.onsets)
        for clip, start in zip(template.clips, starts):
            clip.start = round(start, 4)
        # Durations follow from the new starts so there are no gaps or overlaps.
        for index, clip in enumerate(template.clips[:-1]):
            clip.duration = round(template.clips[index + 1].start - clip.start, 4)
            clip.animation_duration = clip.duration
        template.notes.append("cut points snapped to the nearest detected onset")
        result = template
    else:
        result = rebuild(
            template, analysis,
            clips=args.clips or len(template.clips),
            min_duration=args.min_duration,
            axis=axis,
        )

    for clip in result.clips:
        clip.axis = axis
    print(f"\ndirection: {args.direction} (axis {axis})")

    print(f"\nnew durations ({len(result.clips)} clips):")
    print("  " + ", ".join(f"{d:.2f}" for d in result.durations()))
    print(f"  total {result.duration:.3f}s")

    out = Path(args.out) if args.out else Path(args.template).with_name(
        Path(args.template).stem + f"_{args.mode}.json")
    result.name = f"{template.name} ({args.mode})"
    result.save(out)
    print(f"\nsaved -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
