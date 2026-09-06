"""Show and check what the analysis engine hears in a track.

The choreography is only as good as this, so the point is twofold: assert the things that
must hold (a bar has the right number of beats, sections tile the track, curves stay in
range) and print the musical picture in a form a person can sanity-check against the song.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from beatcanvas import analysis, config  # noqa: E402

MUSIC = Path(r"C:\Users\prash\Music\Little Do You Know Beat Cry.mp4")
REPORT = config.ROOT / "_logs" / "analysis_report.txt"

failures = 0
_lines: list[str] = []


def say(text: str = "") -> None:
    # The Windows console is cp1252 here, so anything outside it would raise. The report
    # file is the real output; the console is a convenience.
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"))
    _lines.append(text)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(_lines) + "\n", encoding="utf-8")


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures
    say(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures += 1


def bar_chart(values: list[float], width: int = 60) -> str:
    """A plain-ASCII sparkline, so it survives any console encoding."""
    ramp = " .:-=+*#%@"
    if not values:
        return ""
    picked = [values[int(i * (len(values) - 1) / max(1, width - 1))]
              for i in range(width)]
    top = len(ramp) - 1
    return "".join(ramp[min(top, max(0, int(round(v * top))))] for v in picked)


def main() -> int:
    if not MUSIC.exists():
        say(f"music not found: {MUSIC}")
        return 2

    started = time.time()
    result = analysis.analyse(MUSIC, duration=0.0, fps=30.0)
    elapsed = time.time() - started

    say(f"track    : {MUSIC.name}")
    say(f"length   : {result.duration:.1f}s   analysed in {elapsed:.1f}s")
    say(f"tempo    : {result.bpm:.1f} BPM in {result.meter}/4  "
        f"(beat = {result.beat_duration * 1000:.0f}ms)")
    say(f"beats    : {len(result.grid)} on the grid, "
        f"{len(result.downbeat_times())} bar starts")
    say(f"onsets   : {len(result.percussive_onsets)} struck, "
        f"{len(result.harmonic_onsets)} held")
    say(f"figures  : {len(result.bursts)} quick runs, {len(result.drops)} drops")
    say("")

    say("1. the metrical grid holds together")
    check("a tempo was found", 55.0 <= result.bpm <= 190.0, f"{result.bpm:.1f} BPM")
    check("beats were placed", len(result.grid) > 8, str(len(result.grid)))
    gaps = np.diff([b.time for b in result.grid]) if len(result.grid) > 2 else np.zeros(1)
    check("beat spacing is regular",
          float(gaps.std()) < result.beat_duration * 0.35,
          f"spread {gaps.std() * 1000:.0f}ms around {gaps.mean() * 1000:.0f}ms")
    positions = [b.beat_in_bar for b in result.grid]
    check("beat numbering never exceeds the metre",
          max(positions) == result.meter and min(positions) == 1,
          f"1..{max(positions)}")
    downbeat_gaps = np.diff(result.downbeat_times())
    check("bar starts are one bar apart",
          len(downbeat_gaps) == 0 or
          abs(float(np.median(downbeat_gaps)) -
              result.beat_duration * result.meter) < result.beat_duration * 0.5,
          f"median {float(np.median(downbeat_gaps)) if len(downbeat_gaps) else 0:.2f}s "
          f"vs expected {result.beat_duration * result.meter:.2f}s")
    check("bar starts really are the strongest beats",
          _downbeats_are_stronger(result),
          _downbeat_margin(result))

    say("\n2. struck and held sounds were told apart")
    check("both kinds were found",
          len(result.percussive_onsets) > 4 and len(result.harmonic_onsets) > 2,
          f"{len(result.percussive_onsets)} struck, "
          f"{len(result.harmonic_onsets)} held")
    check("they are not the same list",
          result.percussive_onsets != result.harmonic_onsets)
    voices = {}
    for beat in result.grid:
        voices[beat.voice] = voices.get(beat.voice, 0) + 1
    check("beats were attributed to more than one voice", len(voices) > 1,
          ", ".join(f"{k}={v}" for k, v in sorted(voices.items())))

    say("\n3. the energy curves are usable as controls")
    for name in ("energy", "bass", "mid", "high", "brightness"):
        curve = getattr(result, name)
        ok = (curve is not None and len(curve) > 0
              and float(curve.min()) >= -1e-6 and float(curve.max()) <= 1.0 + 1e-6)
        check(f"{name} stays inside 0..1", ok,
              f"{float(curve.min()):.3f}..{float(curve.max()):.3f}, {len(curve)} frames"
              if curve is not None else "missing")
    frames_expected = int(np.ceil(result.duration * 30.0))
    check("one value per video frame",
          result.energy is not None and abs(len(result.energy) - frames_expected) <= 1,
          f"{len(result.energy)} vs {frames_expected}")
    jumps = np.abs(np.diff(result.energy)) if result.energy is not None else np.zeros(1)
    check("the curve is smooth enough not to strobe",
          float(jumps.max()) < 0.35,
          f"biggest frame-to-frame step {float(jumps.max()):.3f}")

    say("\n4. the track was divided into sections")
    check("more than one section was found", len(result.sections) > 1,
          str(len(result.sections)))
    tiled = all(abs(a.end - b.start) < 1e-6
                for a, b in zip(result.sections, result.sections[1:]))
    check("sections tile the track with no gaps", tiled)
    check("the first section starts at zero",
          abs(result.sections[0].start) < 0.05,
          f"{result.sections[0].start:.2f}s")
    check("sections are long enough to register",
          all(s.duration > 1.0 for s in result.sections),
          f"shortest {min(s.duration for s in result.sections):.1f}s")
    labels = {s.label for s in result.sections}
    check("labels come from the known set",
          labels <= set(analysis.SECTION_TYPES), ", ".join(sorted(labels)))

    say("\n   section map")
    for section in result.sections:
        say(f"        {section.start:6.1f}s - {section.end:6.1f}s  "
            f"{section.label:10s} energy {section.energy:.2f}  "
            f"group {section.group}  {bar_chart(_slice(result, section), 34)}")

    say("\n   energy over the whole track")
    say(f"        {bar_chart(result.energy_plot(400))}")
    say(f"        bass    {bar_chart([float(v) for v in result.bass])}")
    say(f"        high    {bar_chart([float(v) for v in result.high])}")

    if result.bursts:
        say("\n   quick runs (these drive piece-by-piece reveals)")
        for burst in result.bursts[:12]:
            say(f"        {burst.start:6.2f}s  {burst.count} hits in "
                f"{(burst.end - burst.start) * 1000:4.0f}ms"
                + ("  then a held note" if burst.resolves_to_sustain else ""))

    if result.drops:
        say(f"\n   drops at {', '.join(f'{t:.2f}s' for t in result.drops)}")

    say("\n5. the timeline exports in the documented shape")
    payload = result.to_dict()
    for key in ("bpm", "duration_ms", "time_signature", "sections", "beats",
                "downbeats", "percussive_onsets", "harmonic_onsets",
                "burst_patterns", "energy_curves"):
        check(f"timeline has '{key}'", key in payload)
    first = payload["beats"][0] if payload["beats"] else {}
    check("a beat carries its bar position",
          {"time", "beat_in_bar", "bar", "is_downbeat"} <= set(first),
          ", ".join(sorted(first)))

    say("\n" + "=" * 68)
    if failures:
        say(f"{failures} check(s) FAILED")
        return 1
    say("the analysis engine understands this track")
    return 0


def _slice(result, section) -> list[float]:
    if result.energy is None:
        return []
    lo = int(section.start * result.fps)
    hi = max(lo + 1, int(section.end * result.fps))
    return [float(v) for v in result.energy[lo:hi]]


def _downbeats_are_stronger(result) -> bool:
    """Bar ones should carry more weight than the beats around them, on average."""
    downs = [b.strength for b in result.grid if b.is_downbeat]
    others = [b.strength for b in result.grid if not b.is_downbeat]
    if not downs or not others:
        return False
    return float(np.mean(downs)) >= float(np.mean(others))


def _downbeat_margin(result) -> str:
    downs = [b.strength for b in result.grid if b.is_downbeat]
    others = [b.strength for b in result.grid if not b.is_downbeat]
    if not downs or not others:
        return "not enough beats"
    return (f"bar ones average {float(np.mean(downs)):.3f} "
            f"vs {float(np.mean(others)):.3f} for the rest")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback
        say("\nthe check itself crashed:\n" + traceback.format_exc())
        sys.exit(3)
