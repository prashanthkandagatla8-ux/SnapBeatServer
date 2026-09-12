"""The template model: what a CapCut edit becomes once the photos are stripped out.

A template is the timing and the motion, with numbered slots where pictures go. It
carries no image data, so one template plus any set of photos produces a video.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import config


#: How to reconcile the photo count with the template's slot count.
FILL_MODES = ("repeat", "trim")

#: Outline used for each revealed piece. The count is never fixed here: it follows the
#: beats. Only the shape of a piece is a matter of taste.
REVEAL_SHAPES = ("box", "circle")

#: Below two pictures there is nothing to cut between, so the beat sync is pointless.
MIN_PHOTOS = 2

def resolve_audio(audio_path: str, near: Path | None = None) -> str:
    """Find the music that belongs to a fixed template.

    A template stores only the file name so the style stays portable, which means the
    name has to be looked up rather than trusted as a path. Searched in the folder that
    travels with the templates first, then beside the template, then in the user's own
    music folders. The name is returned unchanged when nothing matches, so the error the
    caller raises still says which file is missing.
    """
    if not audio_path:
        return ""
    normalized_path = audio_path.replace("\\", "/")
    candidate = Path(normalized_path)
    if candidate.exists():
        return str(candidate if candidate.is_absolute() else candidate.resolve())

    filename = candidate.name
    stem = candidate.stem

    home_music = Path.home() / "Music"
    folders = [config.TEMPLATE_MUSIC_DIR, config.TEMPLATE_DIR, home_music]
    if near:
        folders.insert(0, near)
    for folder in folders:
        found = folder / filename
        if found.exists():
            return str(found)
    # A track renamed with a track number still counts as the same file.
    for folder in folders:
        if not folder.is_dir():
            continue
        for found in sorted(folder.glob(f"*{stem}*")):
            if found.is_file():
                return str(found)
    return audio_path


#: Where a template's timing comes from.
#:
#: ``fixed``  the cut points and the music are both part of the template. Reproduces one
#:            specific edit exactly, and only works with that track.
#: ``any``    the template carries no timing at all, only a style. Cuts are generated
#:            from the onsets of whatever music is supplied, so one template covers
#:            every song.
MUSIC_MODES = ("fixed", "any")


@dataclass
class Clip:
    """One picture on screen for one span of time."""

    index: int
    #: Slot number this clip pulls its picture from. Repeated slots reuse a picture.
    slot: int
    start: float                  # seconds on the timeline
    duration: float               # seconds
    animation: str | None = None
    #: How long the animation runs. CapCut lets this be shorter than the clip.
    animation_duration: float = 0.0
    #: Extra user scale on top of the automatic cover-fit.
    scale: float = 1.0
    #: Extra user rotation in degrees, on top of the animation's own rotation.
    rotation: float = 0.0
    #: Static offset in half-canvas-heights.
    offset_x: float = 0.0
    offset_y: float = 0.0
    flip_horizontal: bool = False
    flip_vertical: bool = False
    #: Which axis the animation travels along for this clip.
    axis: str = "x"
    #: Multiplier on the animation's amplitude.
    intensity: float = 1.0
    #: How the frame stays filled while the picture is off centre: "mirror" reflects the
    #: picture into the gap, "zoom" scales up so no gap exists.
    cover_mode: str = "mirror"
    #: Progressive reveal. ``reveal_total`` is how many pieces this picture is divided
    #: into and ``reveal_shown`` how many are visible on this clip. Zero disables it and
    #: the whole picture shows. Consecutive clips sharing a slot with a rising
    #: ``reveal_shown`` build one picture up piece by piece.
    #:
    #: The count is decided per picture from the beats it spans, so a photo held across
    #: four in-between beats is cut into four pieces and one lands on each. It is not a
    #: fixed grid and it is not capped.
    reveal_total: int = 0
    reveal_shown: int = 0
    #: Fixed per-picture so the piece order is scrambled but repeatable.
    reveal_seed: int = 0
    #: How the pieces are ordered as they appear: "sequence", "spiral", "random"
    reveal_order: str = "sequence"
    #: Piece outline: "box" or "circle".
    reveal_shape: str = "box"

    # -- the three layers of motion -------------------------------------
    # A choreographed edit moves on three independent layers at once: how the picture
    # arrives, how it drifts while it is up, and how it twitches on each beat. Keeping
    # them separate is what stops everything landing on the same accent and reading as
    # one blunt effect.

    #: Layer 1, how this picture arrives. See :data:`beatcanvas.effects.REVEALS`.
    #: ``""`` means it simply appears, which is the right answer on a hard cut.
    reveal_kind: str = ""
    #: How long the arrival takes, in seconds.
    reveal_duration: float = 0.0
    #: Absolute times at which each piece of a piece-by-piece reveal lands. Baked in so
    #: the renderer needs no access to the music analysis.
    reveal_at: list[float] = field(default_factory=list)

    #: Layer 1, how this picture leaves, and over how long.
    transition: str = ""
    transition_duration: float = 0.0

    #: Layer 3, the beat reactions active on this clip, from :data:`effects.MICRO`.
    micro: list[str] = field(default_factory=list)
    #: Absolute times of the beats those reactions fire on.
    micro_beats: list[float] = field(default_factory=list)
    #: Scales every reaction on this clip. Folds together the section energy and the
    #: pace the user asked for, so a quiet verse twitches less than a chorus.
    micro_intensity: float = 0.0

    #: Where a zoom should head for, in 0..1 of the frame. Defaults to the middle.
    focal_x: float = 0.5
    focal_y: float = 0.5

    #: Which section of the music this clip belongs to. Carried for explanation only.
    section: str = ""

    #: Which repeating rhythmic figure decided this clip's treatment, or -1 if none did.
    #: Recorded rather than recomputed: a clip's start is shifted earlier than the beat it
    #: was decided on, and the first clip is pinned to zero, so looking the figure up from
    #: the start time afterwards can land in the wrong bar.
    figure: int = -1

    #: Original CapCut source file name, kept for traceability only.
    source_name: str = ""

    @property
    def end(self) -> float:
        return self.start + self.duration

    def contains(self, time: float) -> bool:
        return self.start <= time < self.end


@dataclass
class Template:
    name: str
    width: int = 1080
    height: int = 1920
    fps: float = 30.0
    clips: list[Clip] = field(default_factory=list)
    #: "fixed" keeps the bundled music and baked timing; "any" derives both at render
    #: time from whatever track is supplied.
    music_mode: str = "fixed"
    #: Style applied when generating clips for an "any" template.
    animation: str = "Cut"
    axis: str = "-y"
    cover_mode: str = "zoom"
    #: One clip per this many detected beats. 2 halves the cutting rate.
    beats_per_clip: int = 1
    #: Switches the piece reveal on. The picture then changes only on main beats, and the
    #: in-between beats each reveal one more piece of it.
    #:
    #: This is a flag, not a piece count. How many pieces a picture is cut into is decided
    #: per picture from how many beats it spans, so the reveal always keeps step with the
    #: music instead of forcing a fixed grid. Any positive value enables it.
    reveal_tiles: int = 0
    #: How the pieces are ordered as they appear.
    reveal_order: str = "random"
    #: Piece outline: "box" or "circle".
    reveal_shape: str = "box"
    #: Clips shorter than this read as a flicker, so nearby beats get merged.
    min_clip_duration: float = 0.20
    #: Cap the output length in seconds. 0 follows the music to its end.
    max_seconds: float = 0.0
    #: Music travels with the template only when the mode is "fixed".
    audio_path: str = ""
    audio_name: str = ""
    audio_start: float = 0.0
    #: Beat positions in seconds, if CapCut had them. Informational.
    beat_markers: list[float] = field(default_factory=list)
    #: Where this came from, and any notes worth carrying forward.
    source_draft: str = ""
    notes: list[str] = field(default_factory=list)
    version: int = 1

    # -- derived ---------------------------------------------------------

    @property
    def duration(self) -> float:
        return max((clip.end for clip in self.clips), default=0.0)

    @property
    def frame_count(self) -> int:
        return int(round(self.duration * self.fps))

    @property
    def aspect(self) -> float:
        return self.width / max(1, self.height)

    @property
    def slot_count(self) -> int:
        """How many distinct pictures this template asks for."""
        return (max((clip.slot for clip in self.clips), default=-1) + 1)

    def clip_before(self, clip: Clip) -> Clip | None:
        """The clip immediately before ``clip``, or None if it is the first.

        A cross-dissolve needs the outgoing picture, which is whatever was on screen just
        before this clip started.
        """
        for index, candidate in enumerate(self.clips):
            if candidate is clip:
                return self.clips[index - 1] if index > 0 else None
        return None

    def clip_at(self, time: float) -> Clip | None:
        for clip in self.clips:
            if clip.contains(time):
                return clip
        # Past the end, hold the final clip so the last frame is not blank.
        if self.clips and time >= self.clips[-1].end:
            return self.clips[-1]
        return None

    def durations(self) -> list[float]:
        return [round(clip.duration, 3) for clip in self.clips]

    # -- adapting to however many photos were supplied ---------------------

    def adapt(self, photo_count: int, mode: str = "repeat") -> "Template":
        """Reconcile the template's slot count with the number of photos supplied.

        ``repeat``  keep the full length and cycle the photos round again. Two photos
                    across twenty-eight slots simply alternate.
        ``trim``    keep the full photo order and cut the render short instead, so
                    nothing repeats.

        The cut points always stay where they are, so whichever mode is chosen the
        result is still on the beat. Trimming only ever removes whole clips.
        """
        if photo_count < MIN_PHOTOS:
            raise ValueError(
                f"at least {MIN_PHOTOS} photos are needed, got {photo_count}"
            )
        if mode not in FILL_MODES:
            raise ValueError(f"unknown fill mode {mode!r}, expected one of {FILL_MODES}")

        adapted = Template.from_dict(self.to_dict())

        if mode == "repeat" or photo_count >= self.slot_count:
            return adapted

        # trim: keep clips until adding another would need a photo we do not have.
        kept: list[Clip] = []
        used: dict[int, int] = {}
        for clip in adapted.clips:
            if clip.slot not in used:
                if len(used) >= photo_count:
                    break
                used[clip.slot] = len(used)
            kept.append(clip)

        if not kept:
            raise ValueError("trimming left no clips; supply more photos")

        # Renumber so slots are contiguous from zero.
        for clip in kept:
            clip.slot = used[clip.slot]
        adapted.clips = kept
        adapted.notes = list(adapted.notes) + [
            f"trimmed to {len(kept)} clips ({adapted.duration:.2f}s) "
            f"to match {photo_count} photos without repeating"
        ]
        return adapted

    def plan_for(self, photo_count: int, mode: str) -> str:
        """A one-line description of what a given photo count will produce."""
        if photo_count < MIN_PHOTOS:
            return f"need at least {MIN_PHOTOS} photos"
        if photo_count >= self.slot_count:
            extra = photo_count - self.slot_count
            tail = f", {extra} unused" if extra else ""
            return (f"{self.slot_count} slots filled once{tail}; "
                    f"full length {self.duration:.2f}s")
        if mode == "repeat":
            rounds = self.slot_count / photo_count
            return (f"{photo_count} photos cycled {rounds:.1f}x over "
                    f"{self.slot_count} slots; full length {self.duration:.2f}s")
        trimmed = self.adapt(photo_count, "trim")
        return (f"cut short to {len(trimmed.clips)} clips, "
                f"{trimmed.duration:.2f}s, nothing repeated")

    # -- persistence -----------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "music_mode": self.music_mode,
            "animation": self.animation,
            "axis": self.axis,
            "cover_mode": self.cover_mode,
            "beats_per_clip": self.beats_per_clip,
            "reveal_tiles": self.reveal_tiles,
            "reveal_order": self.reveal_order,
            "reveal_shape": self.reveal_shape,
            "min_clip_duration": self.min_clip_duration,
            "max_seconds": self.max_seconds,
            "audio_path": self.audio_path,
            "audio_name": self.audio_name,
            "audio_start": self.audio_start,
            "source_draft": self.source_draft,
            "notes": self.notes,
            "beat_markers": [round(b, 4) for b in self.beat_markers],
            "clips": [asdict(clip) for clip in self.clips],
        }

    # -- building timing from arbitrary music ------------------------------

    def with_beats(self, onsets: list[float], audio_duration: float,
                   strong: list[float] | None = None) -> "Template":
        """Return a copy whose clips are cut to ``onsets``.

        Only meaningful for an ``any`` template, which carries a style but no timing.
        Beats closer together than ``min_clip_duration`` are skipped rather than producing
        a clip too short to register.

        ``strong`` marks the main pulse. When ``reveal_tiles`` is set the picture changes
        only on those, and each beat in between uncovers one more piece of the same
        picture instead of cutting to a new one.

        ``beats_per_clip`` means "hold each photo longer", but what gets thinned depends on
        the style. Normally it thins the beats themselves. For a reveal style it thins only
        the main beats, so a photo spans more of them and gains more pieces, and every
        in-between beat survives to drive one.
        """
        built = Template.from_dict(self.to_dict())

        usable = sorted(t for t in onsets if t >= 0.0)
        if not usable:
            # No detectable rhythm. Fall back to an even split so a render still happens
            # rather than failing on music the detector cannot read.
            step = max(self.min_clip_duration, 0.5)
            span = self.max_seconds or audio_duration
            usable = [i * step for i in range(max(2, int(span // step)))]
            built.notes = list(built.notes) + [
                "no clear beats were detected in this track, so cuts were spaced evenly"
            ]

        if usable[0] > 0.0:
            usable = [0.0] + usable

        # max_seconds of 0 means "follow the music to its end". Checking for None rather
        # than falsiness matters here: 0 is a real choice, not a missing value.
        limit = audio_duration if not self.max_seconds else min(self.max_seconds,
                                                               audio_duration)

        starts: list[float] = [usable[0]]
        for time_s in usable[1:]:
            if time_s - starts[-1] < self.min_clip_duration:
                continue
            if time_s > limit:
                break
            starts.append(time_s)

        strong_set = sorted(strong or [])
        reveal_mode = bool(self.reveal_tiles) and bool(strong_set)

        stride = max(1, int(self.beats_per_clip))
        if stride > 1:
            if reveal_mode:
                # A reveal style needs every in-between beat, because those are what
                # uncover the pieces. Holding a photo for longer therefore means spanning
                # more main beats, so the thinning is applied to the main beats and the
                # in-between ones are all kept. Thinning every beat instead would throw
                # away the beats the reveal runs on, leaving a single piece per photo and
                # no reveal at all.
                thinned = strong_set[::stride]
                if len(thinned) >= 2:
                    strong_set = thinned
            else:
                starts = starts[::stride]

        if len(starts) < 2:
            starts = [0.0, max(limit, self.min_clip_duration * 2)]

        # The final clip needs an end. Running it out to the length limit is wrong when
        # the beats stop earlier than that, which is exactly what happens with markers
        # set by hand: the last picture would sit there for the remainder of the cap.
        # Giving it the typical gap instead keeps it in proportion with its neighbours.
        gaps = [b - a for a, b in zip(starts, starts[1:]) if b > a]
        typical = sorted(gaps)[len(gaps) // 2] if gaps else self.min_clip_duration
        end = min(limit, starts[-1] + max(typical, self.min_clip_duration))

        if reveal_mode and len(strong_set) < len(starts):
            # The order the pieces appear in is a separate matter from what drives them,
            # so every reveal style is beat-driven here regardless of its ordering.
            built.clips = self._reveal_clips(starts, strong_set, end)
        else:
            # With no main beats to group by there is nothing to reveal against, so the
            # style falls back to a piece count spread over each clip's own length.
            built.clips = self._simple_clips(starts, end)

        built.audio_start = 0.0
        return built

    def _simple_clips(self, starts: list[float], end: float) -> list[Clip]:
        """One picture per boundary, which is what most styles want."""
        clips: list[Clip] = []
        for index, start in enumerate(starts):
            stop = starts[index + 1] if index + 1 < len(starts) else end
            duration = round(stop - start, 4)
            if duration < self.min_clip_duration * 0.5:
                continue
            clips.append(Clip(
                index=len(clips), slot=len(clips),
                start=round(start, 4), duration=duration,
                animation=self.animation, animation_duration=duration,
                axis=self.axis, cover_mode=self.cover_mode,
                reveal_total=self.reveal_tiles,
                reveal_shown=0,
                reveal_seed=len(clips),
                reveal_order=self.reveal_order,
                reveal_shape=self.reveal_shape,
            ))
        return clips

    def _reveal_clips(self, starts: list[float], strong: list[float],
                      end: float) -> list[Clip]:
        """Change picture on main beats, reveal one more piece on each beat in between.

        Every boundary still produces a clip, so the cutting stays locked to the music.
        What changes is the slot: it only advances on a main beat.

        How many pieces a picture is cut into is decided by that picture's own group: a
        photo spanning a main beat and four in-between beats is cut into five pieces and
        exactly one lands on each beat, so the last beat of the group completes it. A
        different group with more beats gets more pieces. Nothing is rounded to a grid
        size and there is no upper limit, because forcing a fixed count is what made the
        reveal drift out of step with the music.
        """

        def is_strong(value: float) -> bool:
            return any(abs(value - s) < 0.02 for s in strong)

        # Group the boundaries: a new group begins at each main beat.
        groups: list[list[float]] = []
        for start in starts:
            if not groups or is_strong(start):
                groups.append([start])
            else:
                groups[-1].append(start)

        clips: list[Clip] = []
        for slot, group in enumerate(groups):
            # Only beats that actually survive as clips may count towards the pieces, or
            # the picture would never finish.
            kept: list[tuple[float, float]] = []
            for position, start in enumerate(group):
                if position + 1 < len(group):
                    stop = group[position + 1]
                elif slot + 1 < len(groups):
                    stop = groups[slot + 1][0]
                else:
                    stop = end
                duration = round(stop - start, 4)
                if duration < self.min_clip_duration * 0.5:
                    continue
                kept.append((start, duration))

            pieces = len(kept)
            for position, (start, duration) in enumerate(kept):
                clips.append(Clip(
                    index=len(clips), slot=slot,
                    start=round(start, 4), duration=duration,
                    animation=self.animation, animation_duration=duration,
                    axis=self.axis, cover_mode=self.cover_mode,
                    # One piece per beat, so the count and the reveal step together.
                    reveal_total=pieces, reveal_shown=position + 1,
                    reveal_seed=slot, reveal_order=self.reveal_order,
                    reveal_shape=self.reveal_shape,
                ))
        return clips

    @classmethod
    def from_dict(cls, data: dict) -> "Template":
        clips = [Clip(**clip) for clip in data.get("clips", [])]
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        payload = {k: v for k, v in data.items() if k in known and k != "clips"}
        return cls(clips=clips, **payload)

    def save(self, path: str | Path | None = None) -> Path:
        target = Path(path) if path else config.TEMPLATE_DIR / f"{_slug(self.name)}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: str | Path) -> "Template":
        source = Path(path)
        template = cls.from_dict(json.loads(source.read_text(encoding="utf-8")))
        # Only a fixed template brings its own music, and it stores that as a bare file
        # name, so the real location has to be worked out on load.
        if template.music_mode == "fixed":
            template.audio_path = resolve_audio(template.audio_path,
                                                near=source.parent)
        return template

    def summary(self) -> list[str]:
        lines = [
            f"template : {self.name}",
            f"canvas   : {self.width}x{self.height} @ {self.fps:g} fps",
            f"duration : {self.duration:.3f}s  ({self.frame_count} frames)",
            f"clips    : {len(self.clips)}   slots: {self.slot_count}",
        ]
        if self.audio_name or self.audio_path:
            lines.append(f"audio    : {self.audio_name or Path(self.audio_path).name}")
        animations = sorted({c.animation for c in self.clips if c.animation})
        lines.append(f"animations: {', '.join(animations) if animations else 'none'}")
        axes = sorted({c.axis for c in self.clips})
        lines.append(f"axes     : {', '.join(axes)}")
        if self.beat_markers:
            lines.append(f"beats    : {len(self.beat_markers)} markers")
        for note in self.notes:
            lines.append(f"note     : {note}")
        return lines


def _slug(text: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in text)
    while "--" in safe:
        safe = safe.replace("--", "-")
    return safe.strip("-").lower() or "template"


def list_templates() -> list[Path]:
    return sorted(config.TEMPLATE_DIR.glob("*.json"))
