"""A written account of what was heard in a track and what will be done about it.

Asked for directly: a thorough analysis of every tune, what it is, what repeats, and when.
It is also the only practical way to argue with the result. A video that feels wrong is hard
to reason about; a page saying "this loops every 4 bars, here are the 2 figures in it, this
one gets a piece reveal because it is a fill" can be read and disagreed with.

Plain text on purpose, so it can go in a log, a job note or a panel in the app without
being reformatted three times.
"""
from __future__ import annotations

from .analysis import MusicAnalysis
from .rhythm import RhythmMap

RAMP = " .:-=+*#%@"


def sparkline(values, width: int = 0) -> str:
    """A rough shape in plain characters, so it survives any console or log viewer."""
    values = list(values)
    if not values:
        return ""
    if width and width != len(values):
        values = [values[int(i * (len(values) - 1) / max(1, width - 1))]
                  for i in range(width)]
    peak = max(values) or 1.0
    top = len(RAMP) - 1
    return "".join(RAMP[min(top, max(0, int(round(v / peak * top))))] for v in values)


def _clock(seconds: float) -> str:
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


def describe(music: MusicAnalysis, figures: RhythmMap,
             name: str = "", pace: str = "") -> str:
    """The full account, as one block of text."""
    out: list[str] = []
    say = out.append

    say("=" * 78)
    say(f"WHAT WAS HEARD IN {name or 'this track'}")
    say("=" * 78)

    beat_ms = music.beat_duration * 1000
    bar_seconds = music.beat_duration * music.meter
    say("")
    say(f"  length      {_clock(music.duration)}")
    say(f"  tempo       {music.bpm:.1f} BPM  ({beat_ms:.0f}ms per beat)")
    say(f"  metre       {music.meter}/4  ({bar_seconds:.2f}s per bar)")
    say(f"  beats       {len(music.grid)} on the grid, "
        f"{len(music.downbeat_times())} bar lines")
    say(f"  onsets      {len(music.percussive_onsets)} struck, "
        f"{len(music.harmonic_onsets)} held")
    shared = {p.treatment.reveal for p in figures.patterns if p.treatment is not None}
    say(f"  figures     {len(figures.patterns)} distinct, "
        f"{len(shared)} arrival(s) between them"
        + (f", looping every {figures.loop_bars} bar(s)" if figures.looped else ""))
    say(f"  fast runs   {len(music.bursts)}")
    say(f"  drops       {len(music.drops)}"
        + (f" at {', '.join(_clock(t) for t in music.drops)}" if music.drops else ""))
    if pace:
        say(f"  pace asked  {pace}")

    say("")
    say("-" * 78)
    say("IS IT A LOOP?")
    say("-" * 78)
    say("")
    if figures.looped:
        say(f"  Yes. It repeats every {figures.loop_bars} bar(s) "
            f"({figures.loop_bars * bar_seconds:.1f}s), and the repeats are "
            f"{figures.loop_strength:.0%} alike.")
        say("")
        say("  Because of that, bars were grouped by their position in the loop rather")
        say("  than by comparing each bar with its neighbours, and the section-by-section")
        say("  softening was switched off. A loop has one character throughout, so the")
        say("  picture keeps one too.")
    else:
        say(f"  No. The best repeat found was only {figures.loop_strength:.0%} alike, "
            f"below the {80}% needed")
        say("  to call it a loop, so bars were compared with each other instead.")

    say("")
    say("-" * 78)
    say("THE REPEATED PATTERNS")
    say("-" * 78)
    for pattern in figures.patterns:
        say("")
        say(f"  FIGURE {pattern.id}   {pattern.occurrences} bar(s) of "
            f"{sum(p.occurrences for p in figures.patterns)}")
        say(f"    shape        |{sparkline(pattern.signature)}|   "
            f"({figures.subdivisions} slots per beat)")
        say(f"    character    density {pattern.density:.2f}, "
            f"syncopation {pattern.syncopation:.2f}, "
            f"longest run {pattern.run_length}, accent {pattern.accent:.1f}x")
        say(f"    loudest      {pattern.voice}")
        say(f"    energy       {pattern.energy:.2f}")

        times = ", ".join(_clock(t) for t in pattern.starts[:12])
        say(f"    heard at     {times}"
            + (f"  ... and {len(pattern.starts) - 12} more"
               if len(pattern.starts) > 12 else ""))
        say(f"    bars         {pattern.bars[:16]}"
            + (" ..." if len(pattern.bars) > 16 else ""))

        if pattern.treatment is not None:
            treatment = pattern.treatment
            say(f"    GETS         {treatment.reveal} + {treatment.movement} "
                f"+ {treatment.transition}")
            say(f"    reactions    {', '.join(treatment.micro) or 'none'}")
            say(f"    BECAUSE      {treatment.because}")

    say("")
    say("-" * 78)
    say("HOW THE TRACK MOVES")
    say("-" * 78)
    say("")
    say(f"  energy   |{sparkline(music.energy_plot(400), 66)}|")
    if music.bass is not None:
        say(f"  bass     |{sparkline([float(v) for v in music.bass], 66)}|")
    if music.high is not None:
        say(f"  high     |{sparkline([float(v) for v in music.high], 66)}|")

    say("")
    say("  sections found (a loop has none really, so these are only a guide)")
    for section in music.sections:
        say(f"    {_clock(section.start):>7s} - {_clock(section.end):<7s} "
            f"{section.label:10s} energy {section.energy:.2f}")

    if music.bursts:
        say("")
        say("  fast runs of hits, which are what build a photo up piece by piece")
        for burst in music.bursts[:10]:
            say(f"    {_clock(burst.start)}  {burst.count} hits in "
                f"{(burst.end - burst.start) * 1000:.0f}ms"
                + ("  then a held note" if burst.resolves_to_sustain else ""))
        if len(music.bursts) > 10:
            say(f"    ... and {len(music.bursts) - 10} more")

    say("")
    say("-" * 78)
    say("NOTES AND LIMITS")
    say("-" * 78)
    say("")
    for note in list(figures.notes) + list(music.notes):
        say(f"  - {note}")
    say("  - tempo and metre are estimated, and everything above is measured against")
    say("    them. If the bar length looks wrong to you, that is the thing to fix first,")
    say("    because every figure boundary depends on it.")
    say("")
    return "\n".join(out)
