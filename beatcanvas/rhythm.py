"""Find the repeating rhythmic figures in a track, and settle one treatment for each.

A song is not a stream of unrelated beats. It is a handful of rhythmic figures repeated and
rearranged: a verse groove, a chorus groove, a fill that announces the change. A listener
recognises those figures long before they could name them, and expects the picture to
recognise them too. When the same figure comes back and the picture does something
different, the edit feels arbitrary; when it comes back and the picture answers the same
way, the edit feels composed.

So the unit of decision here is the figure, not the beat. Each bar is reduced to a rhythm
signature, bars with the same signature are grouped, and every group is assigned one
treatment chosen from what that figure actually is -- how dense it is, whether it pushes
against the beat, whether it is a fill. That treatment is then used for that figure
wherever it appears, for the length of the song.

Everything is numpy. Signatures come from the onset envelope rather than from discrete
detected onsets, so a missed or doubled onset shifts a signature slightly instead of
changing it into a different figure.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import effects
from .analysis import MusicAnalysis

#: Slots per beat. Four resolves sixteenth notes, which is fine enough for the fills that
#: matter and coarse enough that a hit landing a few milliseconds early stays in its slot.
SUBDIVISIONS = 4

#: Two bars count as the same figure above this cosine similarity. Set by what it has to
#: tolerate: the same groove played twice is never bit-identical, but a different groove
#: puts its energy in different slots and falls well below.
SAME_FIGURE = 0.86

#: A figure seen fewer times than this is treated as a variation of its nearest neighbour
#: rather than a figure of its own. Without this, every slightly odd bar becomes its own
#: pattern and the edit is back to changing constantly.
RARE = 2

#: How alike bars one loop apart must be before the track is called a loop.
#:
#: Deliberately lower than :data:`SAME_FIGURE`. Comparing bar to bar is asking "are these
#: the same figure"; comparing across a whole track is asking "is this the same passage
#: coming round again", and the answer has to survive the bar grid being slightly off,
#: which it usually is. A loop found this way is far more reliable evidence of repetition
#: than bar-by-bar similarity, because it is averaged over the entire track.
LOOP_SIMILAR = 0.80

#: Longest loop worth looking for, in bars. Beyond about this the "loop" is really the song
#: structure, which sections already describe.
MAX_LOOP_BARS = 16


#: The gentle counterpart of each arrival.
#:
#: A figure can be busy while the passage it sits in is not -- the same drum pattern turns
#: up in a breakdown, played quietly. Punching there is wrong however the drums behave. So
#: each figure carries a softened version of its own treatment, decided once, rather than
#: borrowing whatever the section's palette happens to offer. Borrowing was what let one
#: figure end up with two or three different arrivals across the song.
GENTLE_ARRIVAL = {
    "pieces": "circle_wipe",
    "cut": "fade",
    "scale_pop": "fade",
    "zoom_burst": "circle_wipe",
    "slide_in": "fade",
    "shutter": "circle_wipe",
    "split": "circle_wipe",
    "circle_wipe": "circle_wipe",
    "diagonal_wipe": "diagonal_wipe",
    "soft_focus": "soft_focus",
    "fade": "fade",
    "zoom_unveil": "soft_focus",
}

#: Movement that settles rather than drives, for the same reason.
GENTLE_MOVEMENT = {
    "Punch In": "Zoom In", "Pulse": "Zoom In", "Bounce": "Zoom In",
    "Spin": "Sway", "Cut": "Zoom In", "Glide": "Glide",
    "Zoom In": "Zoom In", "Zoom Out": "Zoom Out", "Drift": "Drift", "Sway": "Sway",
}


@dataclass
class Treatment:
    """What the picture does for one rhythmic figure."""

    reveal: str
    movement: str
    transition: str
    micro: tuple[str, ...]
    #: Why this was chosen, for the report and for the app to show.
    because: str = ""
    #: An explicit softened arrival. Set when a look restricts the vocabulary, because the
    #: default softening can otherwise step outside the look: the gentle form of a grid
    #: build is a circle, which is not a grid.
    gentle: str = ""

    @property
    def gentle_reveal(self) -> str:
        return self.gentle or GENTLE_ARRIVAL.get(self.reveal, "fade")

    @property
    def gentle_movement(self) -> str:
        return GENTLE_MOVEMENT.get(self.movement, "Zoom In")

    @property
    def gentle_micro(self) -> tuple[str, ...]:
        # A quiet passage keeps only the reaction that can be felt without being seen.
        return tuple(m for m in self.micro if m in ("vignette_pulse", "bass_throb")) \
            or ("vignette_pulse",)


@dataclass
class Pattern:
    """One rhythmic figure, and everywhere it occurs."""

    id: int
    signature: np.ndarray
    bars: list[int] = field(default_factory=list)
    starts: list[float] = field(default_factory=list)

    #: Share of slots carrying real energy. A busy groove is near 1, a sparse one near 0.
    density: float = 0.0
    #: Share of the figure's energy that lands away from the main beats. High means it
    #: pushes against the pulse, which suits movement that travels.
    syncopation: float = 0.0
    #: Longest run of consecutive occupied slots: a fill or roll.
    run_length: int = 0
    #: Loudest slot relative to the mean, so a figure with one hard accent is separable
    #: from one that is evenly busy.
    accent: float = 0.0
    energy: float = 0.0
    voice: str = "melody"

    treatment: Treatment | None = None

    @property
    def occurrences(self) -> int:
        return len(self.bars)

    @property
    def is_fill(self) -> bool:
        return self.run_length >= SUBDIVISIONS

    def describe(self) -> str:
        return (f"figure {self.id}: {self.occurrences} bars, "
                f"density {self.density:.2f}, syncopation {self.syncopation:.2f}, "
                f"run {self.run_length}, accent {self.accent:.2f}, "
                f"voice {self.voice}")

    def to_dict(self) -> dict:
        return {
            "id": self.id, "occurrences": self.occurrences,
            "bars": list(self.bars),
            "density": round(self.density, 3),
            "syncopation": round(self.syncopation, 3),
            "run_length": self.run_length,
            "accent": round(self.accent, 3),
            "energy": round(self.energy, 3),
            "voice": self.voice,
            "signature": [round(float(v), 3) for v in self.signature],
            "treatment": None if self.treatment is None else {
                "reveal": self.treatment.reveal,
                "movement": self.treatment.movement,
                "transition": self.treatment.transition,
                "micro": list(self.treatment.micro),
                "because": self.treatment.because,
            },
        }


@dataclass
class RhythmMap:
    """Every figure in the track, and which figure each bar belongs to."""

    patterns: list[Pattern] = field(default_factory=list)
    #: Bar number -> pattern id.
    by_bar: dict[int, int] = field(default_factory=dict)
    subdivisions: int = SUBDIVISIONS
    notes: list[str] = field(default_factory=list)

    #: Length of the repeating passage in bars, or 0 if the track does not loop.
    loop_bars: int = 0
    #: How alike the repeats are, 0..1.
    loop_strength: float = 0.0

    @property
    def looped(self) -> bool:
        return self.loop_bars > 0

    def pattern_at(self, time: float, music: MusicAnalysis) -> Pattern | None:
        """The figure playing at this moment."""
        if not self.patterns:
            return None
        bar = next((b.bar for b in reversed(music.grid) if b.time <= time + 1e-6), None)
        if bar is None:
            bar = music.grid[0].bar if music.grid else 1
        pattern_id = self.by_bar.get(bar)
        if pattern_id is None:
            # A bar with no figure of its own borrows the most common one, so there is
            # always an answer and it is never a surprising one.
            pattern_id = self.dominant().id if self.patterns else 0
        return next((p for p in self.patterns if p.id == pattern_id), self.patterns[0])

    def dominant(self) -> Pattern:
        return max(self.patterns, key=lambda p: p.occurrences)

    def to_dict(self) -> dict:
        return {"subdivisions": self.subdivisions,
                "patterns": [p.to_dict() for p in self.patterns],
                "notes": list(self.notes)}


# --------------------------------------------------------------- signatures

def _sample(envelope: np.ndarray, times: np.ndarray, at: np.ndarray) -> np.ndarray:
    if envelope is None or not len(envelope):
        return np.zeros(len(at), dtype=np.float32)
    return np.interp(at, times, envelope).astype(np.float32)


def bar_windows(music: MusicAnalysis) -> list[tuple[int, float, float]]:
    """Each bar as (bar number, start, end).

    Built from the detected bar lines. A trailing partial bar is dropped: a figure cannot
    be compared against a fragment of one.
    """
    downs = [(b.bar, b.time) for b in music.grid if b.is_downbeat]
    if len(downs) < 2:
        if not music.grid:
            return []
        span = music.beat_duration * music.meter
        return [(1, music.grid[0].time, music.grid[0].time + span)]
    windows = []
    for (bar, start), (_, following) in zip(downs, downs[1:]):
        if following - start > music.beat_duration * 0.5:
            windows.append((bar, start, following))
    return windows


def signatures(music: MusicAnalysis,
               subdivisions: int = SUBDIVISIONS) -> tuple[list[tuple[int, float, float]],
                                                          np.ndarray]:
    """A unit-length rhythm vector per bar.

    The bar is divided into equal slots and each slot takes the peak of the onset envelope
    inside it, so a hit registers in its slot at the strength it was played. Taking the
    peak rather than the mean matters: a sharp hit occupies a small part of its slot and
    would be averaged away.

    Each vector is then scaled to unit length, which is what makes the comparison about
    *where* the hits are rather than how loud the passage is. The same groove played
    quietly still matches itself played loud.
    """
    windows = bar_windows(music)
    slots = max(2, music.meter * subdivisions)
    if not windows:
        return [], np.zeros((0, slots), dtype=np.float32)

    envelope = music.envelope
    times = (music.envelope_times if music.envelope_times is not None
             else np.zeros(0, dtype=np.float32))

    rows = []
    for _, start, end in windows:
        edges = np.linspace(start, end, slots + 1)
        vector = np.empty(slots, dtype=np.float32)
        for index in range(slots):
            # A handful of samples inside the slot, reduced by their peak.
            probe = np.linspace(edges[index], edges[index + 1], 5)
            vector[index] = float(_sample(envelope, times, probe).max())
        rows.append(vector)

    matrix = np.asarray(rows, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9
    return windows, matrix / norms


# --------------------------------------------------------------- grouping

def find_loop(matrix: np.ndarray, max_bars: int = MAX_LOOP_BARS) -> tuple[int, float]:
    """How long the repeating passage is, in bars, and how convincing the repeat is.

    Much of the music people bring to this is built on a loop: four or eight bars played
    over and over, sometimes with a fill on the last one. Recognising that is far more
    reliable than comparing bars one at a time, because the evidence is averaged over the
    whole track rather than resting on a single pair of bars whose boundaries may be
    slightly misplaced.

    It matters because the alternative fails in a particular, visible way. If the bar grid
    is a little off -- and it usually is, since it comes from an estimated tempo -- then the
    same loop sampled twice produces two slightly different signatures, they fall on either
    side of the similarity threshold, and one unchanging groove is split into a dozen
    "figures" each with its own animation. The picture then changes constantly while the
    music does not, which is exactly the complaint this is here to answer.

    Shorter loops are preferred where the evidence is comparable, because an eight-bar
    reading of a four-bar loop is also true but says less.
    """
    bars = len(matrix)
    if bars < 4:
        return 0, 0.0

    best_period, best_score = 0, 0.0
    for period in range(1, min(max_bars, bars // 2) + 1):
        pairs = [float(np.dot(matrix[i], matrix[i + period]))
                 for i in range(bars - period)]
        if not pairs:
            continue
        score = float(np.mean(pairs))
        # A shorter period saying the same thing is the better description, so a longer one
        # has to be clearly better to win.
        if score > best_score + 0.01:
            best_period, best_score = period, score

    if best_score < LOOP_SIMILAR:
        return 0, best_score
    return best_period, best_score


def group_by_loop(matrix: np.ndarray, period: int,
                  threshold: float = SAME_FIGURE) -> list[int]:
    """Group bars by their position in the loop, then merge positions that match.

    Position in the loop is the grouping, so every third bar of a four-bar loop is the same
    figure by construction rather than by a similarity test that might narrowly fail. Then
    positions whose averaged signatures are alike are merged, which is what collapses "three
    bars of groove and a fill" into two figures instead of four.
    """
    bars = len(matrix)
    centroids = []
    for position in range(period):
        rows = matrix[position::period]
        mean = rows.mean(axis=0)
        centroids.append(mean / (np.linalg.norm(mean) + 1e-9))

    # Merge alike positions, keeping the earliest as the representative.
    merged: dict[int, int] = {}
    for position in range(period):
        for earlier in range(position):
            if float(np.dot(centroids[position], centroids[earlier])) >= threshold:
                merged[position] = merged.get(earlier, earlier)
                break
    return [merged.get(i % period, i % period) for i in range(bars)]


def group(matrix: np.ndarray, threshold: float = SAME_FIGURE) -> list[int]:
    """Assign each bar to a figure, by similarity to the figures already seen.

    Deliberately sequential rather than a global clustering: figures are numbered in the
    order they first appear, so figure 0 is whatever the song opens with. That makes the
    numbering stable and the report readable, and it means adding more of the song cannot
    renumber what came before.
    """
    if not len(matrix):
        return []
    centroids: list[np.ndarray] = []
    counts: list[int] = []
    labels: list[int] = []

    for vector in matrix:
        best, score = -1, -1.0
        for index, centroid in enumerate(centroids):
            similarity = float(np.dot(vector, centroid))
            if similarity > score:
                best, score = index, similarity
        if best >= 0 and score >= threshold:
            labels.append(best)
            # Fold the bar into the figure's average so the figure represents all its
            # occurrences rather than only the first.
            total = counts[best] + 1
            blended = (centroids[best] * counts[best] + vector) / total
            centroids[best] = blended / (np.linalg.norm(blended) + 1e-9)
            counts[best] = total
        else:
            centroids.append(vector.copy())
            counts.append(1)
            labels.append(len(centroids) - 1)

    return _absorb_rare(labels, centroids, counts, threshold)


def _absorb_rare(labels: list[int], centroids: list[np.ndarray], counts: list[int],
                 threshold: float) -> list[int]:
    """Fold one-off figures into the nearest common one.

    A bar that is merely a scruffy version of the main groove should not become a figure in
    its own right, or the picture ends up with a treatment that appears once and reads as a
    mistake.
    """
    common = [i for i, n in enumerate(counts) if n > RARE]
    if not common or len(common) == len(counts):
        return labels
    remap: dict[int, int] = {}
    for index, count in enumerate(counts):
        if count > RARE:
            continue
        nearest = max(common, key=lambda c: float(np.dot(centroids[index], centroids[c])))
        remap[index] = nearest
    return [remap.get(label, label) for label in labels]


# --------------------------------------------------------------- description

def _measure(pattern: Pattern, meter: int, subdivisions: int) -> None:
    """Work out the qualities of a figure that decide how it should look."""
    signature = pattern.signature
    peak = float(signature.max()) or 1.0
    occupied = signature > peak * 0.35

    pattern.density = float(occupied.mean())
    pattern.accent = float(peak / (float(signature.mean()) + 1e-9))

    # Slots that fall on a main beat, against everything between them.
    on_beat = np.zeros(len(signature), dtype=bool)
    on_beat[::subdivisions] = True
    total = float(signature.sum()) + 1e-9
    pattern.syncopation = float(signature[~on_beat].sum() / total)

    longest = current = 0
    for flag in occupied:
        current = current + 1 if flag else 0
        longest = max(longest, current)
    pattern.run_length = int(longest)


def choose(pattern: Pattern) -> Treatment:
    """Pick the treatment that suits this figure.

    The reasoning is the design's affinity idea applied to a whole figure rather than to a
    single beat, which is what lets one decision cover every repeat of it:

    * a fill -- a run of consecutive fast hits -- is what a piece-by-piece reveal exists
      for, one piece per hit, and it is the only figure that gets it
    * a sparse figure has room for a slow arrival and slow movement
    * a dense, on-beat figure wants a hard cut and a punch; anything gradual would still be
      arriving when the next photo is due
    * a syncopated figure pushes against the pulse, so movement that travels suits it
      better than movement that settles
    * one hard accent in an otherwise quiet bar is an impact, so it gets the heavy arrival
    """
    if pattern.is_fill:
        return Treatment(
            reveal="pieces", movement="Pulse", transition="cut",
            micro=("zoom_pulse", "brightness_flash"),
            because=f"a fill: {pattern.run_length} hits in a row, so the photo is built "
                    f"one piece per hit")

    if pattern.density < 0.22:
        return Treatment(
            reveal="soft_focus", movement="Zoom In", transition="dissolve",
            micro=("vignette_pulse",),
            because=f"sparse ({pattern.density:.2f} of the bar occupied), so there is room "
                    f"for a slow arrival")

    if pattern.accent > 3.2 and pattern.density < 0.45:
        return Treatment(
            reveal="zoom_burst", movement="Punch In", transition="flash",
            micro=("zoom_pulse", "shake", "chromatic"),
            because=f"one hard accent in a quiet bar (peak {pattern.accent:.1f}x the "
                    f"average), which reads as an impact")

    if pattern.syncopation > 0.62:
        return Treatment(
            reveal="slide_in", movement="Glide", transition="whip",
            micro=("zoom_pulse",),
            because=f"syncopated ({pattern.syncopation:.2f} of the energy off the beat), "
                    f"so the picture travels rather than settles")

    if pattern.density > 0.55:
        return Treatment(
            reveal="cut", movement="Punch In", transition="cut",
            micro=("zoom_pulse", "brightness_flash"),
            because=f"busy and on the beat ({pattern.density:.2f} occupied), so the photo "
                    f"lands hard and holds still")

    return Treatment(
        reveal="circle_wipe", movement="Zoom In", transition="dissolve",
        micro=("zoom_pulse", "vignette_pulse"),
        because=f"a steady middling groove ({pattern.density:.2f} occupied), so the "
                f"arrival opens and the movement is gentle")


# --------------------------------------------------------------- entry

def analyse(music: MusicAnalysis, subdivisions: int = SUBDIVISIONS,
            threshold: float = SAME_FIGURE, use_loop: bool = True,
            cohere: bool = True, look: str = "mix") -> RhythmMap:
    """Find the repeating figures in ``music`` and settle a treatment for each."""
    windows, matrix = signatures(music, subdivisions)
    if not windows:
        return RhythmMap(notes=["no bars were found, so no figures could be compared"])

    # Look for a loop first. When the track is built on one, position in the loop is a far
    # better guide to what a bar is than comparing that bar against its neighbours.
    loop_bars, loop_strength = find_loop(matrix) if use_loop else (0, 0.0)
    if loop_bars:
        labels = group_by_loop(matrix, loop_bars, threshold)
    else:
        labels = group(matrix, threshold)
    slots = matrix.shape[1]

    patterns: dict[int, Pattern] = {}
    for (bar, start, _), label, vector in zip(windows, labels, matrix):
        pattern = patterns.get(label)
        if pattern is None:
            pattern = Pattern(id=label, signature=vector.copy())
            patterns[label] = pattern
        else:
            blended = pattern.signature * pattern.occurrences + vector
            pattern.signature = blended / (np.linalg.norm(blended) + 1e-9)
        pattern.bars.append(bar)
        pattern.starts.append(start)

    # Renumber by first appearance so figure 0 is the one the song opens with.
    ordered = sorted(patterns.values(), key=lambda p: min(p.bars))
    for new_id, pattern in enumerate(ordered):
        pattern.id = new_id

    for pattern in ordered:
        _measure(pattern, music.meter, subdivisions)
        inside = [b.voice for b in music.grid
                  if any(s <= b.time < s + music.beat_duration * music.meter
                         for s in pattern.starts)]
        pattern.voice = (max(set(inside), key=inside.count) if inside else "melody")
        pattern.energy = float(np.mean([music.energy_at(s) for s in pattern.starts]))
        pattern.treatment = choose(pattern)

    # Cohesion. Giving every figure its own arrival is right in principle and wrong in
    # effect: two figures that alternate bar by bar change the entrance on every second
    # photo, and a viewer does not see "figure A, figure B", they see an edit that cannot
    # make up its mind. On a track built from one repeating groove the arrival is the loud,
    # obvious event and should stay put; what separates the figures is then carried by the
    # movement inside the frame and the reactions, which are felt rather than noticed.
    total_bars = sum(p.occurrences for p in ordered) or 1
    lead = max(ordered, key=lambda p: p.occurrences)
    share = lead.occurrences / total_bars
    cohered: list[str] = []
    # This is on unless a preset turns it off, rather than only when a loop is detected.
    # Gating it on loop detection meant that whenever the loop was missed -- a short window,
    # a track that only mostly repeats -- the entrance started changing again, which is the
    # one thing that was complained about. The detector being uncertain is not a reason to
    # make the video restless.
    if cohere and len(ordered) > 1 and lead.treatment is not None:
        anchor = lead.treatment.reveal
        for pattern in ordered:
            if pattern is lead or pattern.treatment is None:
                continue
            if pattern.treatment.reveal == anchor:
                continue
            cohered.append(f"figure {pattern.id} {pattern.treatment.reveal}->{anchor}")
            pattern.treatment.because += (
                f"; its arrival was changed from {pattern.treatment.reveal} to {anchor} "
                f"to match figure {lead.id}, because this track repeats and a shifting "
                f"entrance reads as indecision rather than as detail")
            pattern.treatment.reveal = anchor

    # The look, applied last so it has the final say. It narrows the vocabulary without
    # touching the reasoning: each figure keeps the character it earned and is given the
    # closest arrival the look allows, rather than the look's first entry regardless.
    chosen_look = effects.look(look)
    restyled: list[str] = []
    if chosen_look.reveals:
        softest = min(chosen_look.reveals,
                      key=lambda n: -effects.REVEALS[n].beats
                      if n in effects.REVEALS else 0.0)
        for pattern in ordered:
            if pattern.treatment is None:
                continue
            wanted = effects.nearest_reveal(chosen_look.reveals, pattern.treatment.reveal)
            if wanted != pattern.treatment.reveal:
                restyled.append(f"figure {pattern.id} "
                                f"{pattern.treatment.reveal}->{wanted}")
                pattern.treatment.because += (
                    f"; the {chosen_look.name} look allows only "
                    f"{', '.join(chosen_look.reveals)}, so its arrival became {wanted}")
                pattern.treatment.reveal = wanted
            # The softened form has to stay inside the look too, so a quiet passage does not
            # become the one place a different kind of arrival appears.
            pattern.treatment.gentle = softest

    by_bar = {}
    for (bar, _, _), label in zip(windows, labels):
        by_bar[bar] = next(p.id for p in ordered
                           if bar in p.bars and p.signature is not None)

    repeats = sum(1 for p in ordered if p.occurrences > 1)
    notes = [
        f"{len(windows)} bars reduced to {len(ordered)} repeating figure(s) "
        f"at {slots} slots per bar; {repeats} of them recur",
        "each figure keeps one treatment wherever it appears, so a groove that comes back "
        "is answered the same way",
    ]
    if cohered:
        notes.append(
            f"the figures were made to share one arrival ({lead.treatment.reveal}, from "
            f"figure {lead.id}, which covers {share:.0%} of the bars): "
            f"{'; '.join(cohered)}. They still differ in how the picture moves once it is "
            f"there, which is felt without being noticed")
    elif len(ordered) > 1 and not cohere:
        notes.append("this reading lets each figure keep its own arrival, so the entrance "
                     "changes when the figure does")
    if chosen_look.reveals:
        notes.append(
            f"the {chosen_look.name} look restricts arrivals to "
            f"{', '.join(chosen_look.reveals)}"
            + (f" (pieces drawn as {chosen_look.shape})" if chosen_look.shape else "")
            + (f": {'; '.join(restyled)}" if restyled
               else ", which is what the music had chosen anyway"))
    if loop_bars:
        notes.insert(0, f"this track loops every {loop_bars} bar(s) "
                        f"({loop_strength:.0%} alike), so figures were grouped by position "
                        f"in the loop rather than by comparing neighbouring bars")
    else:
        notes.insert(0, f"no loop was found (best repeat was only "
                        f"{loop_strength:.0%} alike), so bars were compared to each other")
    return RhythmMap(patterns=ordered, by_bar=by_bar, subdivisions=subdivisions,
                     notes=notes, loop_bars=loop_bars, loop_strength=loop_strength)
