"""The director: turn an Event Timeline into an edit.

:mod:`analysis` works out what the music is doing. This decides what the picture should do
about it. That decision is the whole product -- anything can cut on a beat, and cutting on
every beat is exactly what makes the amateur tools look amateur.

Four ideas from the design do the real work here:

**Sync selectively.** Photo changes are locked to phrase starts and bar lines, the moments
a listener already feels as structural. Everything between those is handled by drift and
small reactions instead of more cuts. Tying every layer to every beat is the trap the
design calls Mickey-Mousing; the point is contrast, not saturation.

**Let the section decide the language.** A chorus and a verse get different reveals,
different movement and different reaction strengths, so the video changes character when
the song does rather than running one effect end to end.

**Follow the voice.** A struck sound gets a hard-edged arrival, a held note gets a slow
one, a bass note gets something with weight. Sharpness is matched to the sound that
triggered it.

**Land slightly early.** Hearing is quicker than seeing, so a cut placed exactly on the
transient reads as late. Everything is pulled forward by a frame or two.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import effects, rhythm
from .analysis import MusicAnalysis
from .template import Clip, Template

# --------------------------------------------------------------------- pace

#: How busy the edit should be. Not only a cut rate: the design is clear that pace changes
#: which *kinds* of effect are used, so each mode carries its own reaction strength too.
@dataclass(frozen=True)
class Pace:
    name: str
    #: Photos change on a phrase boundary this many bars apart. Fractions mean more often
    #: than once a bar: 0.5 is twice a bar.
    bars_per_photo: float
    #: Scales every beat reaction.
    micro: float
    #: Whether quick runs of hits are allowed to reveal a photo piece by piece.
    use_pieces: bool
    note: str


PACES: dict[str, Pace] = {
    "slow": Pace("slow", 2.0, 0.30, False,
                 "A photo every couple of bars. Movement inside the shot carries the "
                 "beats in between. For cinematic and emotional edits."),
    "medium": Pace("medium", 1.0, 0.60, True,
                   "A photo per bar, with the beats between handled by movement and small "
                   "reactions rather than more cuts. The balanced choice."),
    "fast": Pace("fast", 0.5, 0.90, True,
                 "A photo every couple of beats, reacting visibly on each one. For "
                 "energetic and up-tempo tracks."),
    "accurate": Pace("accurate", 0.25, 1.00, True,
                     "A photo on every beat, reacting to every musical event. Maximum "
                     "sync, and the most intense."),
}

#: Sixty milliseconds is about two frames at 30fps. Hearing runs ahead of seeing, so a cut
#: placed exactly on the transient is perceived as arriving late; pulling it forward makes
#: both senses land together. The design puts the window at 30-60ms.
EARLY_OFFSET = 0.055

#: How long a photo must be fully visible -- arrived, unmasked, undistorted -- before it is
#: allowed to leave.
#:
#: Without this the pace can ask for photos so short that the arrival is still finishing
#: when the next one is due, and the viewer never actually sees any of them. A quarter of a
#: second is around the lower limit for recognising a photographic image; four tenths gives
#: it room to register rather than merely flicker past.
MIN_FULL_VIEW = 0.40


# --------------------------------------------------------------------- palettes

@dataclass(frozen=True)
class Palette:
    """The visual language of one kind of section."""

    reveals: tuple[str, ...]
    transitions: tuple[str, ...]
    in_slide: tuple[str, ...]
    micro: tuple[str, ...]
    #: Multiplies the section's own energy when scaling reactions.
    intensity: float
    #: Multiplies how often photos change. Above 1 holds photos longer.
    hold: float
    note: str


PALETTES: dict[str, Palette] = {
    "intro": Palette(
        reveals=("fade", "soft_focus", "circle_wipe"),
        transitions=("dissolve",),
        in_slide=("Zoom In", "Drift", "Sway"),
        micro=("vignette_pulse",),
        intensity=0.45, hold=1.6,
        note="sets the mood: slow arrivals, almost no reaction"),
    "verse": Palette(
        reveals=("fade", "diagonal_wipe", "slide_in", "circle_wipe"),
        transitions=("dissolve", "cut"),
        in_slide=("Zoom In", "Zoom Out", "Glide", "Drift"),
        micro=("zoom_pulse", "vignette_pulse"),
        intensity=0.6, hold=1.15,
        note="storytelling: movement inside the shot rather than more cuts"),
    "build": Palette(
        reveals=("pieces", "shutter", "scale_pop"),
        transitions=("whip", "cut"),
        in_slide=("Punch In", "Spin", "Pulse"),
        micro=("zoom_pulse", "brightness_flash", "chromatic"),
        intensity=0.85, hold=0.6,
        note="rising tension: cuts accelerate and reactions grow"),
    "chorus": Palette(
        reveals=("cut", "scale_pop", "split", "zoom_burst"),
        transitions=("cut", "whip"),
        in_slide=("Bounce", "Punch In", "Pulse", "Zoom In"),
        micro=("zoom_pulse", "brightness_flash", "shake"),
        intensity=1.0, hold=0.85,
        note="peak energy: hard arrivals and every reaction on"),
    "drop": Palette(
        reveals=("zoom_burst", "cut", "split"),
        transitions=("flash", "cut"),
        in_slide=("Punch In", "Bounce"),
        micro=("zoom_pulse", "brightness_flash", "shake", "chromatic"),
        intensity=1.0, hold=0.7,
        note="maximum impact: the loudest visual moment in the edit"),
    "breakdown": Palette(
        reveals=("soft_focus", "circle_wipe", "fade"),
        transitions=("dissolve",),
        in_slide=("Drift", "Sway", "Zoom Out"),
        micro=("bass_throb",),
        intensity=0.35, hold=1.8,
        note="stripped back: room to breathe, reactions almost off"),
    "bridge": Palette(
        reveals=("soft_focus", "zoom_unveil", "fade"),
        transitions=("dissolve",),
        in_slide=("Sway", "Drift", "Zoom Out"),
        micro=("bass_throb", "vignette_pulse"),
        intensity=0.4, hold=1.6,
        note="deliberate contrast with the chorus around it"),
    "outro": Palette(
        reveals=("fade", "soft_focus"),
        transitions=("dissolve",),
        in_slide=("Zoom Out", "Drift"),
        micro=("vignette_pulse",),
        intensity=0.3, hold=2.0,
        note="winds down and lets the last photo hold"),
}

#: What each voice wants from an arrival, in preference order. Applied as a filter over
#: the section palette, so the section still sets the language and the voice only picks
#: within it.
VOICE_PREFERENCE: dict[str, tuple[str, ...]] = {
    "drums": ("cut", "scale_pop", "pieces", "shutter", "split", "slide_in"),
    "bass": ("zoom_burst", "circle_wipe", "split", "scale_pop"),
    "melody": ("fade", "diagonal_wipe", "circle_wipe", "soft_focus", "zoom_unveil",
               "slide_in"),
    "ambient": ("soft_focus", "fade", "circle_wipe", "zoom_unveil"),
}


# --------------------------------------------------------------------- model

@dataclass
class Plan:
    """The finished edit, plus an account of why it looks the way it does."""

    template: Template
    pace: str = "medium"
    #: One line per photo, so a person can read back what the director decided.
    explain: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: The repeating figures the treatments came from.
    rhythm: "rhythm.RhythmMap | None" = None

    @property
    def slides(self) -> int:
        return len({c.slot for c in self.template.clips})


class _Casting:
    """Decides which effect a musical situation gets, and remembers the answer.

    The obvious way to avoid monotony is to keep changing the effect, and that is wrong.
    Changing it on every photo makes the visuals churn independently of the music: on a
    track with a steady rhythm the picture keeps switching treatment while nothing in the
    audio has changed, which reads as arbitrary rather than designed.

    What a real editor does is the opposite. The same musical situation gets the same
    visual treatment, so a recurring hook acquires a recurring motif, and the treatment
    changes when the *music* changes. So the choice here is a function of the situation --
    the section, the dominant voice, roughly how loud it is, whether there is a run of hits
    to work with -- and identical situations reuse their earlier answer.

    Monotony is still guarded against, but only where it is real: if one situation persists
    for a whole phrase, a single deliberate variation is allowed as an accent. That is a
    change the listener can attribute to the music having gone on, rather than noise.
    """

    def __init__(self, phrase: int = 8, seed: int = 7) -> None:
        self._cast: dict[tuple, str] = {}
        self._runs: dict[tuple, int] = {}
        self._random = random.Random(seed)

    def pick(self, situation: tuple, options: tuple[str, ...] | list[str],
             prefer: tuple[str, ...] = (), phrase: int = 8) -> str:
        pool = [o for o in options if o]
        if not pool:
            return ""
        ranked = [o for o in prefer if o in pool] or pool

        settled = self._cast.get(situation)
        if settled is None or settled not in pool:
            settled = ranked[0]
            self._cast[situation] = settled
            self._runs[situation] = 0

        self._runs[situation] = self._runs.get(situation, 0) + 1
        # Long enough on one treatment that a single accent is welcome. Deterministic, so
        # the same track always produces the same edit.
        if phrase > 0 and self._runs[situation] % phrase == 0 and len(ranked) > 1:
            return ranked[1]
        return settled


# --------------------------------------------------------------------- helpers

def _photo_change_times(music: MusicAnalysis, pace: Pace,
                        figures: "rhythm.RhythmMap | None" = None
                        ) -> list[tuple[float, str]]:
    """Decide when the photo changes, and note why each change happens.

    Changes are placed on phrase starts and bar lines rather than on beats, because those
    are the moments a listener already hears as structural. Section boundaries always
    force a change; a build accelerates towards its end; and every section's own palette
    can hold photos for longer or shorter than the pace asks.
    """
    if not music.grid:
        return []

    changes: list[tuple[float, str]] = []
    for section in music.sections:
        palette = PALETTES.get(section.label, PALETTES["verse"])
        # Everything is worked out in beats rather than bars. A pace of one bar per photo
        # is four beats and a quarter of a bar is one beat, so counting beats covers the
        # whole range with one rule instead of a special case once the gap drops below a
        # bar. The section's own hold then stretches or compresses it.
        #
        # The result is snapped to a musical quantity rather than used raw. A gap of five
        # beats would walk steadily across the bar so that changes stop coinciding with
        # anything a listener feels; whole bars keep the slower paces on bar lines, and
        # one or two beats keeps the faster ones on beats, which is the ladder the design
        # describes.
        # On a looped track the per-section hold is also spurious, and letting it vary makes
        # the cutting rate wander through a passage that never changes.
        hold = 1.0 if (figures is not None and figures.looped) else palette.hold
        raw = pace.bars_per_photo * music.meter * hold
        if raw >= music.meter:
            every = int(round(raw / music.meter)) * music.meter
        else:
            every = 2 if raw >= 1.5 else 1
        every = max(1, every)

        inside_beats = [b for b in music.grid
                        if section.start <= b.time < section.end]
        if not inside_beats:
            changes.append((section.start, f"{section.label} starts"))
            continue

        # Start counting from the section's first bar line where there is one, so a change
        # every four beats lands on bar lines rather than drifting across them.
        anchor = next((position for position, b in enumerate(inside_beats)
                       if b.is_downbeat), 0)

        # A section boundary is always a change: it is the strongest cue in the track.
        changes.append((inside_beats[anchor].time, f"{section.label} starts"))

        if section.label == "build":
            # Tension comes from the cuts arriving faster, so the gap halves as the
            # section progresses instead of staying put.
            step, index = float(every), anchor + every
            while index < len(inside_beats):
                changes.append((inside_beats[index].time, "build accelerating"))
                step = max(1.0, step / 2.0)
                index += max(1, int(round(step)))
            continue

        for beat in inside_beats[anchor + every::every]:
            # A figure whose treatment builds the photo out of the figure's own hits needs
            # the photo to span the figure. Changing photo partway through a fill would
            # contradict the treatment chosen for it, so inside those bars only the bar line
            # is allowed to change the photo. The figure decides the cutting here, not the
            # pace -- which is the point of deciding by figure at all.
            if not beat.is_downbeat and figures is not None:
                pattern = figures.pattern_at(beat.time, music)
                if (pattern is not None and pattern.treatment is not None
                        and pattern.treatment.reveal == "pieces"):
                    continue
            changes.append((beat.time, f"{section.label}, every {every} beat(s)"))

    # A drop must land on a photo change, or the biggest moment in the music passes
    # without the picture acknowledging it.
    for drop in music.drops:
        nearest = min(music.grid, key=lambda b: abs(b.time - drop))
        changes.append((nearest.time, "drop"))

    merged: list[tuple[float, str]] = []
    for time_s, why in sorted(changes):
        if merged and time_s - merged[-1][0] < music.beat_duration * 0.45:
            # Two cues on nearly the same instant: keep the more specific reason.
            if why == "drop":
                merged[-1] = (merged[-1][0], why)
            continue
        merged.append((time_s, why))
    return merged


def _beats_between(music: MusicAnalysis, start: float, end: float) -> list[float]:
    return [b.time for b in music.grid if start <= b.time < end]


def _burst_for(music: MusicAnalysis, start: float, end: float):
    """The quick run of hits inside this stretch, if there is one worth using."""
    for burst in music.bursts:
        if start - 0.02 <= burst.start and burst.end <= end + 0.02 and burst.count >= 3:
            return burst
    return None


def _dominant_voice(music: MusicAnalysis, start: float, end: float) -> str:
    inside = [b.voice for b in music.grid if start <= b.time < end]
    if not inside:
        return "melody"
    return max(set(inside), key=inside.count)


def _focal(index: int) -> tuple[float, float]:
    """Where a zoom should aim.

    Without a subject detector the next best thing is composition: the rule-of-thirds
    intersections are where a photographer most likely put the subject, and cycling them
    stops every photo zooming at its own centre.
    """
    points = ((0.38, 0.38), (0.62, 0.38), (0.5, 0.5), (0.38, 0.62), (0.62, 0.62))
    return points[index % len(points)]


# --------------------------------------------------------------------- entry

def _enforce_full_view(changes: list[tuple[float, str]], limit: float,
                       arrival_of, dropped: list[str]) -> list[tuple[float, str]]:
    """Drop photo changes that would not leave the photo on screen long enough to see.

    The pace says how often to change; this says how often it is *possible* to change and
    still show anybody anything. Where the two disagree the photo is held instead, which
    keeps every remaining change on its beat -- dropping a change never moves the others --
    and costs only that one extra photo.

    Working backwards would let a late drop invalidate an earlier decision, so this walks
    forward and only ever removes the change that is too close to the one before it.
    """
    if not changes:
        return changes
    kept = [changes[0]]
    for time_s, why in changes[1:]:
        start = kept[-1][0]
        needed = arrival_of(start, time_s) + MIN_FULL_VIEW + 2.0 / 30.0
        if time_s - start < needed:
            dropped.append(f"{time_s:.2f}s ({why}): only "
                           f"{time_s - start:.2f}s available, needs {needed:.2f}s")
            continue
        kept.append((time_s, why))
    # The last photo has the same right to be seen as the others.
    if len(kept) > 1:
        start = kept[-1][0]
        if limit - start < arrival_of(start, limit) + MIN_FULL_VIEW + 2.0 / 30.0:
            dropped.append(f"{start:.2f}s (last photo): "
                           f"only {limit - start:.2f}s before the end")
            kept.pop()
    return kept


def compose(music: MusicAnalysis, pace: str = "medium", width: int = 1080,
            height: int = 1920, fps: float = 30.0, max_seconds: float = 0.0,
            seed: int = 7, rhythm_map: "rhythm.RhythmMap | None" = None) -> Plan:
    """Choreograph an edit for ``music``.

    The result is a :class:`Template` whose clips carry all three layers, so it renders
    through the existing pipeline. ``max_seconds`` of 0 follows the track to its end.

    The animation identity comes from the repeating rhythmic figure playing at the time, so
    a groove that returns is answered the same way for the whole song. The section still
    decides how hard the reactions hit and how long a photo is held, because the same figure
    in a chorus should feel stronger than in a verse.
    """
    mode = PACES.get(pace, PACES["medium"])
    limit = music.duration if not max_seconds else min(max_seconds, music.duration)
    beat = music.beat_duration
    casting_reveal, casting_move = _Casting(seed=seed), _Casting(seed=seed + 1)
    figures = rhythm_map if rhythm_map is not None else rhythm.analyse(music)

    changes = [(t, why) for t, why in _photo_change_times(music, mode, figures)
               if t < limit]
    if not changes:
        changes = [(0.0, "no structure detected")]

    # How long the arrival for a given photo will take, needed before the clips exist so
    # that changes leaving too little time can be removed first.
    def arrival_seconds(start: float, end: float) -> float:
        pattern = figures.pattern_at(start, music)
        kind = pattern.treatment.reveal if pattern and pattern.treatment else "cut"
        if kind == "pieces":
            burst = _burst_for(music, start, end)
            if burst is not None:
                return max(burst.end - start, beat * 0.5)
            return beat * 0.5
        return effects.duration_for(effects.REVEALS, kind, beat)

    dropped: list[str] = []
    changes = _enforce_full_view(changes, limit, arrival_seconds, dropped)

    # Pull every change forward by the perceptual offset, then work the clip boundaries out
    # from the shifted times rather than the original ones. Shifting a start without
    # shifting the previous clip's end would leave a sliver with no picture in it.
    #
    # The first change is moved to zero regardless: the track's first beat is rarely at
    # zero, and honouring it literally would open the video on a black frame.
    shifted = [max(0.0, t - EARLY_OFFSET) for t, _ in changes]
    shifted[0] = 0.0

    clips: list[Clip] = []
    explain: list[str] = []

    for slot, (start, why) in enumerate(changes):
        clip_start = shifted[slot]
        clip_end = shifted[slot + 1] if slot + 1 < len(shifted) else limit
        end = changes[slot + 1][0] if slot + 1 < len(changes) else limit
        if clip_end - clip_start < 0.12:
            continue

        section = music.section_at(start)
        label = section.label if section else "verse"
        palette = PALETTES.get(label, PALETTES["verse"])
        voice = _dominant_voice(music, start, end)

        calm = label in ("intro", "breakdown", "bridge", "outro")
        burst = _burst_for(music, start, end)

        # A run of quick hits is the clearest invitation in the timeline: it is exactly
        # what a piece-by-piece reveal is for, one piece per hit with the held note
        # afterwards completing the picture. So a run adds that option and puts it first
        # in preference -- but it does not force it, because this track has a run in
        # nearly every bar and forcing it would make all of them identical. Variety still
        # gets the final say. Calm sections are left out entirely: uncovering a photo in
        # pieces is a percussive gesture and has no place in a breakdown.
        energy = music.energy_at(start)

        # The rhythmic figure decides what the picture does. Its treatment was settled once
        # for the whole song, so every repeat of the figure is answered identically and the
        # edit reads as composed rather than shuffled.
        pattern = figures.pattern_at(start, music)
        treatment = pattern.treatment if pattern is not None else None
        if treatment is not None:
            reveal, movement = treatment.reveal, treatment.movement
            transition = treatment.transition
            micro_kinds = treatment.micro
            why = f"{why}, figure {pattern.id} ({pattern.occurrences} bars)"
        else:
            candidates = list(palette.reveals)
            prefer = list(VOICE_PREFERENCE.get(voice, ()))
            situation = (label, voice, int(energy * 3), burst is not None)
            reveal = casting_reveal.pick(situation, tuple(candidates), tuple(prefer))
            movement = casting_move.pick(situation, palette.in_slide)
            transition = palette.transitions[slot % len(palette.transitions)]
            micro_kinds = palette.micro

        # A calm section softens the figure's treatment rather than replacing it. The
        # softened version belongs to the figure, so a figure appearing in an intro and
        # again in a breakdown is softened the same way both times; taking the section's
        # own first choice instead gave one figure several different arrivals.
        # A looped track has one character from beginning to end. Its "sections" are an
        # artefact of looking for structure in music that has none to find, so softening by
        # section there would change the treatment for no reason the listener can hear --
        # which is the churn this is meant to prevent. On a loop the figure decides, full
        # stop.
        if figures.looped:
            calm = False

        if calm and treatment is not None:
            reveal = treatment.gentle_reveal
            movement = treatment.gentle_movement
            transition = "dissolve"
            micro_kinds = treatment.gentle_micro
            why = f"{why}, softened for a {label}"

        # A drop overrides taste: the design wants the single most emphatic combination
        # available at exactly this moment.
        if why == "drop":
            reveal, transition = "zoom_burst", "flash"
            movement = "Punch In"

        intensity = max(0.05, min(1.0, energy * palette.intensity * mode.micro))

        beats_inside = _beats_between(music, start, end)
        reveal_at: list[float] = []
        pieces = 0

        if reveal == "pieces":
            if burst is not None:
                reveal_at = list(burst.times)
                why = f"{why}, {len(reveal_at)} quick hits to build on"
            else:
                # Chosen without a run to follow: use the beats it does have.
                reveal_at = beats_inside[:6] or [start]
            pieces = len(reveal_at)

        reveal_seconds = effects.duration_for(effects.REVEALS, reveal, beat)
        # An arrival must finish with time to spare. A fade is two beats long by the tempo
        # rule, but at a fast pace a photo lasts one, so left alone the arrival would never
        # complete and every photo would sit in a permanent half-dissolve. Capping it so
        # that MIN_FULL_VIEW remains is what guarantees the photo is actually seen whole.
        on_screen = clip_end - clip_start
        # A couple of frames on top of the promise. An arrival that ends midway through a
        # frame means the first wholly clean frame is the next one, so reserving only the
        # promised time delivers slightly less than promised.
        reserve = MIN_FULL_VIEW + 2.0 / max(fps, 1.0)
        headroom = max(0.0, on_screen - reserve)
        reveal_seconds = min(reveal_seconds, headroom)
        if reveal == "pieces" and reveal_at:
            # The pieces themselves are timed by the music and cannot be squeezed, so if
            # the run would not leave the photo whole for long enough, the tail of the run
            # is dropped and those pieces land together on the last one kept.
            while len(reveal_at) > 1 and (clip_end - reveal_at[-1]) < reserve:
                reveal_at.pop()
            pieces = len(reveal_at)
            reveal_seconds = max(reveal_at[-1] - start, beat * 0.25)
            if pieces < 2:
                # Nothing left to build with; a plain arrival is honest about that.
                reveal, pieces, reveal_at = "cut", 0, []
                reveal_seconds = 0.0

        micro = tuple(m for m in micro_kinds if m in effects.MICRO)
        # Reactions fire on beats, but not on the beat the photo arrives on: that beat is
        # already an accent and doubling it muddies both.
        micro_beats = [t for t in beats_inside if t - start > beat * 0.35]
        if calm:
            # Quiet sections react on bar lines only, so they stay calm.
            downs = {b.time for b in music.grid if b.is_downbeat}
            micro_beats = [t for t in micro_beats if t in downs]
        if not micro_beats and beats_inside:
            # At a fast pace a photo lasts a single beat, so excluding the arrival beat
            # leaves nothing at all and the reaction layer falls silent exactly where it
            # should be most active. A short photo reacts on its own beat instead.
            micro_beats = list(beats_inside)

        clips.append(Clip(
            index=len(clips), slot=slot,
            start=round(clip_start, 4),
            duration=round(clip_end - clip_start, 4),
            animation=movement,
            animation_duration=round(clip_end - clip_start, 4),
            axis="-y", cover_mode="zoom",
            intensity=round(0.6 + 0.5 * energy, 3),
            reveal_kind=reveal,
            reveal_duration=round(reveal_seconds, 4),
            reveal_at=[round(t - EARLY_OFFSET, 4) for t in reveal_at],
            # The count is read off the clock from reveal_at, so this stays 0: were the
            # times ever lost, no reveal is a better failure than a wrong one.
            reveal_total=pieces, reveal_shown=0,
            reveal_seed=slot, reveal_order="random",
            reveal_shape="box",
            transition=transition,
            transition_duration=round(
                effects.duration_for(effects.TRANSITIONS, transition, beat), 4),
            micro=list(micro),
            micro_beats=[round(t - EARLY_OFFSET, 4) for t in micro_beats],
            micro_intensity=round(intensity, 3),
            focal_x=_focal(slot)[0], focal_y=_focal(slot)[1],
            section=label,
            figure=pattern.id if pattern is not None else -1,
        ))

        explain.append(
            f"{start:6.2f}s  {label:9s} {voice:7s} "
            f"{reveal:13s} {movement:9s} out:{transition:8s} "
            f"react {intensity:.2f} on {len(micro_beats)} beat(s)"
            + (f"  [{pieces} pieces on the run]" if pieces else "")
            + f"   ({why})")

    template = Template(
        name=f"Choreographed ({mode.name})",
        width=width, height=height, fps=fps,
        clips=clips, music_mode="fixed",
        animation="Cut", axis="-y", cover_mode="zoom",
        min_clip_duration=0.12,
        max_seconds=limit,
        notes=[f"choreographed at {mode.name} pace from {music.bpm:.1f} BPM, "
               f"{len(music.sections)} sections",
               mode.note],
    )

    notes = [
        f"{len(clips)} photo changes over {limit:.1f}s",
        f"cuts pulled {EARLY_OFFSET * 1000:.0f}ms early so they read as on time",
        f"every photo is held fully visible for at least {MIN_FULL_VIEW:.2f}s",
    ] + list(figures.notes)
    if dropped:
        notes.append(
            f"{len(dropped)} change(s) dropped so the photo before them could be seen: "
            + "; ".join(dropped[:4]) + (" ..." if len(dropped) > 4 else ""))
    return Plan(template=template, pace=mode.name, explain=explain, notes=notes,
                rhythm=figures)
