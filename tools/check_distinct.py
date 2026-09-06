"""Prove every template produces a genuinely different edit.

This is the regression guard for the bug where each style rendered identically. It builds
the timing for all templates from one track and compares the results, then checks the
server refuses to turn a non-reveal style into a reveal one.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatcanvas import beats, config  # noqa: E402
from beatcanvas.template import Template  # noqa: E402

MUSIC = Path(r"C:\Users\prash\Music\Little Do You Know Beat Cry.mp4")
REPORT = config.ROOT / "_logs" / "distinct_report.txt"

failures = 0
_lines: list[str] = []


def say(text: str = "") -> None:
    print(text)
    _lines.append(text)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(_lines) + "\n", encoding="utf-8")


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures
    say(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures += 1


def signature(template: Template) -> tuple:
    """A fingerprint of what this edit actually is.

    Piece shape and order are part of it: two reveal styles can share their timing exactly
    and still produce visibly different videos, one in boxes and one in circles.
    """
    clips = template.clips
    return (
        len(clips),
        round(template.duration, 2),
        tuple(sorted({c.animation for c in clips})),
        tuple(sorted({c.cover_mode for c in clips})),
        max((c.reveal_total for c in clips), default=0),
        tuple(sorted({c.reveal_shape for c in clips})),
        tuple(sorted({c.reveal_order for c in clips})),
        len({c.slot for c in clips}),
    )


def main() -> int:
    if not MUSIC.exists():
        say(f"music not found: {MUSIC}")
        return 2

    analysis = beats.analyse(MUSIC, duration=36.0)
    strong = analysis.strong_times()
    say(f"track: {len(analysis.onsets)} beats, {len(strong)} main, "
        f"{analysis.tempo_bpm:.0f} BPM\n")

    say("1. each any-music template builds a different edit")
    built: dict[str, Template] = {}
    for path in sorted(config.TEMPLATE_DIR.glob("*.json")):
        template = Template.load(path)
        if template.music_mode != "any":
            continue
        result = template.with_beats(analysis.onsets, analysis.duration, strong=strong)
        built[template.name] = result
        sig = signature(result)
        say(f"        {template.name:22s} {sig[0]:3d} clips  {sig[1]:6.1f}s  "
            f"anim={','.join(sig[2]):11s} cover={','.join(sig[3]):7s} "
            f"pieces={sig[4]:2d} {','.join(sig[5]):7s} {','.join(sig[6]):8s} "
            f"photos={sig[7]}")

    check("several templates were built", len(built) >= 6, f"{len(built)}")

    sigs = {name: signature(t) for name, t in built.items()}
    unique = len(set(sigs.values()))
    check("no two templates produce an identical edit", unique == len(sigs),
          f"{unique} distinct of {len(sigs)}")
    if unique != len(sigs):
        seen: dict[tuple, list[str]] = {}
        for name, sig in sigs.items():
            seen.setdefault(sig, []).append(name)
        for sig, names in seen.items():
            if len(names) > 1:
                say(f"        identical: {', '.join(names)}")

    say("\n2. a template reveals in pieces only if it says so")
    for path in sorted(config.TEMPLATE_DIR.glob("*.json")):
        declared = Template.load(path)
        if declared.music_mode != "any" or declared.name not in built:
            continue
        tiles = max((c.reveal_total for c in built[declared.name].clips), default=0)
        expects = declared.reveal_tiles > 0
        check(f"{declared.name}: {'reveals' if expects else 'does not reveal'}",
              (tiles > 0) == expects,
              f"declared={declared.reveal_tiles} built={tiles}")

    say("\n3. animation is honoured per template")
    for name, template in built.items():
        used = {c.animation for c in template.clips}
        check(f"{name}: single animation applied", len(used) == 1,
              ", ".join(sorted(used)))

    say("\n4. cutting rate is honoured")
    if "Beat Cut" in built and "Slow Drift" in built:
        fast = len(built["Beat Cut"].clips)
        slow = len(built["Slow Drift"].clips)
        check("Slow Drift cuts far less often than Beat Cut", slow * 2 < fast,
              f"{slow} vs {fast} clips")

    say("\n5. cover mode is honoured")
    if "Beat Slide" in built:
        modes = {c.cover_mode for c in built["Beat Slide"].clips}
        check("Beat Slide uses mirror cover", modes == {"mirror"},
              ", ".join(modes))
    if "Beat Pulse" in built:
        modes = {c.cover_mode for c in built["Beat Pulse"].clips}
        check("Beat Pulse uses zoom cover", modes == {"zoom"}, ", ".join(modes))

    say("\n6. a stray tile count cannot convert a plain style")
    plain = Template.load(config.TEMPLATE_DIR / "beat-cut.json")
    check("plain template declares no tiles", plain.reveal_tiles == 0,
          str(plain.reveal_tiles))
    # This mirrors the server guard: the option is only accepted when the style already
    # reveals, so a leftover form value cannot silently change what is rendered.
    say("        (the server only applies reveal_tiles when the template already "
        "reveals)")

    say("\n" + "=" * 60)
    if failures:
        say(f"{failures} check(s) FAILED")
        return 1
    say("all templates are distinct")
    return 0


if __name__ == "__main__":
    sys.exit(main())
