"""The vocabulary of visual effects, and the shared contract about what each one means.

The choreographer decides *which* effect fires and *when*; the renderer decides *how* it
looks. Both need to agree on the names and on what each one is for, and that agreement
lives here so neither side can drift from the other.

The design catalogues some sixty effects. That catalogue is a menu, not a requirement, and
it says as much: five well-timed effects beat sixty badly-timed ones. So this is a
deliberately smaller set chosen to cover all six section palettes -- something sharp for
drums, something smooth for held notes, something heavy for bass, and enough variety that
no palette has to repeat itself.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Which musical voice an effect belongs with. Straight from the design's affinity matrix:
#: a drum wants a hard edge, a held note wants a slow one, a bass note wants weight.
Family = str

PERCUSSIVE: Family = "percussive"
MELODIC: Family = "melodic"
HEAVY: Family = "heavy"


@dataclass(frozen=True)
class Effect:
    """One named effect, with the properties the choreographer reasons about."""

    name: str
    family: Family
    #: Multiplier on the beat length to get a natural duration. The design's rule: a
    #: percussive arrival happens inside half a beat, a melodic one breathes over two.
    beats: float
    #: Human-readable, and shown in the app so a choice can be understood.
    note: str


# --------------------------------------------------------------- layer 1

#: How a picture arrives.
REVEALS: dict[str, Effect] = {
    "cut": Effect("cut", PERCUSSIVE, 0.0,
                  "appears instantly, the hardest possible accent"),
    "scale_pop": Effect("scale_pop", PERCUSSIVE, 0.5,
                        "snaps in oversized and settles with a slight overshoot"),
    "pieces": Effect("pieces", PERCUSSIVE, 1.0,
                     "arrives a piece at a time, one piece per beat"),
    "shutter": Effect("shutter", PERCUSSIVE, 0.6,
                      "opens like blinds, strips staggered across the frame"),
    "split": Effect("split", PERCUSSIVE, 0.6,
                    "the frame parts down the middle to show the picture behind"),
    "fade": Effect("fade", MELODIC, 2.0,
                   "crosses over from the previous picture, never through black"),
    "soft_focus": Effect("soft_focus", MELODIC, 2.0,
                         "starts far out of focus and sharpens"),
    "zoom_unveil": Effect("zoom_unveil", MELODIC, 2.5,
                          "starts on a detail and pulls back to the whole picture"),
    "circle_wipe": Effect("circle_wipe", MELODIC, 1.5,
                          "a circle opens outward from the subject"),
    "diagonal_wipe": Effect("diagonal_wipe", MELODIC, 1.2,
                            "an angled edge sweeps across the frame"),
    "zoom_burst": Effect("zoom_burst", HEAVY, 0.75,
                         "slams out from a hard close-up with a shake"),
    "slide_in": Effect("slide_in", PERCUSSIVE, 0.75,
                       "travels in from one side and stops"),
}

#: How a picture leaves. Kept short on purpose: most cuts should simply be cuts, and the
#: transition matters far less than what happens on the beat either side of it.
TRANSITIONS: dict[str, Effect] = {
    "cut": Effect("cut", PERCUSSIVE, 0.0, "no transition at all"),
    "dissolve": Effect("dissolve", MELODIC, 1.0, "opacity blend into the next picture"),
    "whip": Effect("whip", PERCUSSIVE, 0.4,
                   "a fast smeared swipe, the picture blurring as it goes"),
    "flash": Effect("flash", HEAVY, 0.3,
                    "a bright flare across the cut, for the biggest moments"),
}

# --------------------------------------------------------------- layer 2

#: Continuous movement while a picture is on screen. These are names from
#: :data:`beatcanvas.animations.LIBRARY`, because that is what the renderer already drives;
#: this table only records which ones suit which energy.
IN_SLIDE: dict[str, Effect] = {
    "Zoom In": Effect("Zoom In", MELODIC, 0.0, "slow push toward the subject"),
    "Zoom Out": Effect("Zoom Out", MELODIC, 0.0, "slow pull back to the whole frame"),
    "Drift": Effect("Drift", MELODIC, 0.0, "gentle glide with a little zoom"),
    "Glide": Effect("Glide", MELODIC, 0.0, "steady travel that never settles"),
    "Sway": Effect("Sway", MELODIC, 0.0, "slow lean one way and back"),
    "Pulse": Effect("Pulse", PERCUSSIVE, 0.0, "breathes with the beat"),
    "Bounce": Effect("Bounce", PERCUSSIVE, 0.0, "springs on arrival"),
    "Spin": Effect("Spin", PERCUSSIVE, 0.0, "turns slowly while zooming"),
    "Punch In": Effect("Punch In", PERCUSSIVE, 0.0, "hard scale snap, then still"),
    "Cut": Effect("Cut", PERCUSSIVE, 0.0, "no movement at all"),
}

# --------------------------------------------------------------- layer 3

#: Beat reactions. The design is emphatic that these should be felt rather than seen, so
#: every amount here is small; the choreographer scales them down further still.
MICRO: dict[str, Effect] = {
    "zoom_pulse": Effect("zoom_pulse", PERCUSSIVE, 0.35,
                         "a few per cent of zoom punched in on the beat"),
    "brightness_flash": Effect("brightness_flash", PERCUSSIVE, 0.25,
                               "a brief lift in brightness on the beat"),
    "vignette_pulse": Effect("vignette_pulse", MELODIC, 0.4,
                             "the edges darken and release with the beat"),
    "shake": Effect("shake", HEAVY, 0.3,
                    "a short decaying camera shake on heavy hits"),
    "chromatic": Effect("chromatic", HEAVY, 0.25,
                        "colour channels part briefly on bass hits"),
    "bass_throb": Effect("bass_throb", HEAVY, 0.0,
                         "a continuous swell that follows the low end"),
}


# --------------------------------------------------------------- looks

@dataclass(frozen=True)
class Look:
    """A restriction on the vocabulary above, chosen for its own sake.

    This is a separate question from how the track is read. The reading decides *when*
    something happens; a look decides *what*. Keeping them apart means you can ask for
    circles over a waltz reading, or grids over a half-time one, without one choice
    quietly overruling the other.

    A look narrows rather than adds. Every name here already exists in the tables above and
    is already driven by the renderer, so no look can ask for something that has not been
    verified to draw correctly.
    """

    name: str
    label: str
    note: str
    #: Allowed arrivals, best first. Empty means the whole vocabulary is available.
    reveals: tuple[str, ...] = ()
    #: Piece outline to pin: "box", "circle", or "" to leave it to the music.
    shape: str = ""
    #: Allowed transitions, best first. Empty leaves them to the choreographer.
    transitions: tuple[str, ...] = ()


LOOKS: dict[str, Look] = {
    "mix": Look(
        "mix", "Mix",
        "The whole vocabulary. Each figure gets whatever suits its character, which is the "
        "most musical result and the least predictable one.",
    ),
    "premium": Look(
        "premium", "Premium",
        "The five that look most deliberately produced: a piece-by-piece build, a circle "
        "opening from the subject, a pull-back from a detail, blinds, and a rack focus. "
        "Nothing snaps or slams, so it reads as edited rather than as effects.",
        reveals=("pieces", "circle_wipe", "zoom_unveil", "shutter", "soft_focus"),
        transitions=("dissolve", "whip", "cut"),
    ),
    "grids": Look(
        "grids", "Grids only",
        "Every photo builds a piece at a time in square tiles, one piece per beat. The most "
        "obviously beat-locked look there is, because you can count the hits on screen.",
        reveals=("pieces",), shape="box", transitions=("cut", "dissolve"),
    ),
    "circles": Look(
        "circles", "Circles only",
        "The same piece-by-piece build with round pieces, plus the circle that opens from "
        "the subject. Softer than grids while staying just as locked to the beat.",
        reveals=("pieces", "circle_wipe"), shape="circle",
        transitions=("dissolve", "cut"),
    ),
    "wipes": Look(
        "wipes", "Wipes",
        "Edges travelling across the frame: blinds, a centre split, an angled sweep, an "
        "opening circle. Every arrival uncovers the next photo from the one before, so the "
        "frame is never empty.",
        reveals=("shutter", "split", "diagonal_wipe", "circle_wipe"),
        transitions=("whip", "cut"),
    ),
    "zooms": Look(
        "zooms", "Zooms",
        "Arrivals made of scale alone: a pull-back from a detail, a slam out of a close-up, "
        "an oversized snap that settles. Cinematic, and the most demanding on photo "
        "resolution.",
        reveals=("zoom_unveil", "zoom_burst", "scale_pop"),
        transitions=("flash", "cut"),
    ),
    "soft": Look(
        "soft", "Soft",
        "Nothing with a hard edge: crossfades, rack focus, an opening circle. For ballads, "
        "and for photo sets where movement would fight the pictures.",
        reveals=("fade", "soft_focus", "circle_wipe"),
        transitions=("dissolve",),
    ),
    "punch": Look(
        "punch", "Punch",
        "Hard cuts, snaps, slams and slides, with flares across the biggest moments. Built "
        "for drops and for tracks with an obvious kick.",
        reveals=("cut", "scale_pop", "slide_in", "zoom_burst"),
        transitions=("whip", "flash", "cut"),
    ),
}


def look(name: str) -> Look:
    return LOOKS.get(name or "mix", LOOKS["mix"])


def nearest_reveal(allowed: tuple[str, ...], current: str) -> str:
    """The allowed arrival closest in character to ``current``.

    Substituting by family rather than by position matters: a figure that earned a hard
    percussive arrival should not be handed a two-beat crossfade just because that happens
    to be first in the list. Where the family cannot be matched, the look's own first
    choice wins, since that is what the look is for.
    """
    if not allowed or current in allowed:
        return current
    wanted = REVEALS[current].family if current in REVEALS else PERCUSSIVE
    same = [name for name in allowed
            if name in REVEALS and REVEALS[name].family == wanted]
    return same[0] if same else allowed[0]


def duration_for(table: dict[str, Effect], name: str, beat_seconds: float) -> float:
    """How long an effect should take at this tempo.

    Durations are proportional to the beat rather than fixed, which is what keeps the same
    effect from feeling sluggish at 160 BPM and frantic at 70.
    """
    effect = table.get(name)
    if effect is None:
        return 0.0
    return max(0.0, effect.beats * max(beat_seconds, 1e-3))


def family_of(name: str) -> Family:
    for table in (REVEALS, TRANSITIONS, IN_SLIDE, MICRO):
        if name in table:
            return table[name].family
    return MELODIC


def describe(name: str) -> str:
    for table in (REVEALS, TRANSITIONS, IN_SLIDE, MICRO):
        if name in table:
            return table[name].note
    return name
