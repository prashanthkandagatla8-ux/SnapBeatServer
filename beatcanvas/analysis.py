"""Musical understanding: turn a track into an Event Timeline.

``beats.py`` answers "where are the hits". That is enough to cut on the beat, but cutting
on every beat is the amateur mistake the design calls out: it reads as monotonous however
accurate the timing is. To edit like a person you need to know *what each moment means* --
which hit starts a bar, where the chorus begins, whether the energy is climbing, and
whether the sound at this instant is a drum or a held note.

This module produces that. The output is an Event Timeline: beats numbered inside their
bars, labelled sections, conditioned energy curves and detected rhythmic figures. The
choreography engine reads it and decides what the picture should do.

Implementation note
-------------------
The design recommends ``allin1``/``madmom`` for beats and structure and ``demucs`` for
separating instruments. None of those are installed here and all three need PyTorch, so
everything below is classical DSP in numpy: median-filter harmonic/percussive separation,
comb-filter downbeat scoring, and a self-similarity matrix with Foote novelty for
structure. That is genuinely less accurate than the trained models -- section labels in
particular are heuristics over energy and repetition, not a learned classifier -- but it
needs no extra dependency and it drives exactly the same decisions downstream. Every
function here is deliberately behind :class:`MusicAnalysis`, so a torch-backed engine can
replace the internals later without the choreographer noticing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import beats as beat_mod

SAMPLE_RATE = beat_mod.SAMPLE_RATE
HOP = beat_mod.HOP
FRAME = beat_mod.FRAME

#: Frames per second of the spectrogram, i.e. the resolution every curve is measured at.
ANALYSIS_FPS = SAMPLE_RATE / HOP

#: Section names this module can produce. Ordered roughly by energy.
SECTION_TYPES = ("intro", "verse", "build", "chorus", "drop", "breakdown",
                 "bridge", "outro")

#: What the loudest thing at a given moment appears to be. A proxy for real stem
#: separation: derived from which frequency band dominates and whether the sound is
#: percussive or held, not from an instrument classifier.
VOICES = ("drums", "bass", "melody", "ambient")


@dataclass(frozen=True)
class Preset:
    """One way of reading a track.

    Tempo and metre are estimates, and an estimate can be wrong in ways no amount of
    tuning reliably prevents -- a four-four ballad read as three-four at one and a half
    times the speed scores beautifully and is still wrong. Rather than pretend otherwise,
    these offer a handful of deliberately different readings so a wrong one can be stepped
    around in a second instead of waited on.

    They also differ in how much detail counts as a change, which matters as much as the
    tempo: the same track read coarsely gives one steady groove, and read finely gives a
    dozen variations. Neither is more correct, but they make very different videos.
    """

    name: str
    note: str
    #: Multiply the chosen tempo by this. 0 leaves it alone.
    tempo_factor: float = 0.0
    #: Force the metre. 0 decides from the music.
    meter: int = 0
    #: Slots per beat when comparing bars. Higher notices smaller differences.
    subdivisions: int = 4
    #: How alike two bars must be to count as the same figure. Lower groups more together.
    same_figure: float = 0.86
    #: Whether to look for a loop and group bars by position within it.
    use_loop: bool = True
    #: Whether every figure shares one entrance. On by default: a track that repeats should
    #: look like it repeats, and four different entrances in twenty seconds reads as an
    #: edit that cannot make up its mind rather than as detail.
    cohere: bool = True


PRESETS: dict[str, Preset] = {
    "auto": Preset(
        "auto", "Works the tempo and metre out from the music, and groups bars into "
        "figures. The sensible default.",
    ),
    "one-groove": Preset(
        "one-groove",
        "Treats the whole track as a single groove: one animation from beginning to end, "
        "changing only the photos. Best for a loop, and for anything that felt restless.",
        subdivisions=2, same_figure=0.55,
    ),
    "four-four": Preset(
        "four-four",
        "Forces four beats to the bar. Use when the bar length above looks half or a "
        "third of what you feel.",
        meter=4,
    ),
    "half-time": Preset(
        "half-time",
        "Halves the detected tempo, so photos change half as often and the movement is "
        "calmer. Use when the edit feels twice as busy as the song.",
        tempo_factor=0.5, meter=4,
    ),
    "double-time": Preset(
        "double-time",
        "Doubles the detected tempo for a busier, sharper edit. Use when the edit feels "
        "sluggish against the song.",
        tempo_factor=2.0, meter=4,
    ),
    "fine-detail": Preset(
        "fine-detail",
        "Notices small differences between bars, so fills and variations each get their "
        "own treatment. The most varied, and the most restless.",
        subdivisions=8, same_figure=0.93, cohere=False,
    ),
    "waltz": Preset(
        "waltz", "Forces three beats to the bar, for a genuine waltz or a 6/8 feel.",
        meter=3,
    ),
    "two-thirds": Preset(
        "two-thirds",
        "Slows the tempo to two thirds and forces four beats to the bar. This is the fix "
        "when a four-four song has been read as three-four: three of the wrong beats "
        "occupy the same time as two of the right ones, so halving overshoots and only "
        "two thirds lands on the real bar.",
        tempo_factor=2.0 / 3.0, meter=4,
    ),
}


def preset(name: str) -> Preset:
    return PRESETS.get(name or "auto", PRESETS["auto"])


# --------------------------------------------------------------------- helpers

def _median_filter(data: np.ndarray, size: int, axis: int) -> np.ndarray:
    """Median filter along one axis of a 2-D array, in memory-safe blocks.

    A sliding window over a whole song at once would allocate frames x bins x size
    floats, which runs into gigabytes, so the work is done in blocks. Filtering across
    frequency treats each frame independently, so those blocks are simply a memory cap;
    filtering along time needs real context either side, so those blocks overlap by half
    a window and the overlap is trimmed off afterwards.
    """
    if size < 3:
        return data
    if size % 2 == 0:
        size += 1
    pad = size // 2
    frames, bins = data.shape
    out = np.empty_like(data)
    block = max(32, int(4_000_000 // max(1, bins * size)))

    if axis == 1:
        for start in range(0, frames, block):
            stop = min(frames, start + block)
            chunk = np.pad(data[start:stop], ((0, 0), (pad, pad)), mode="edge")
            windows = np.lib.stride_tricks.sliding_window_view(chunk, size, axis=1)
            out[start:stop] = np.median(windows, axis=-1)
        return out

    for start in range(0, frames, block):
        stop = min(frames, start + block)
        lo, hi = max(0, start - pad), min(frames, stop + pad)
        chunk = data[lo:hi]
        before, after = pad - (start - lo), pad - (hi - stop)
        if before or after:
            chunk = np.pad(chunk, ((before, after), (0, 0)), mode="edge")
        windows = np.lib.stride_tricks.sliding_window_view(chunk, size, axis=0)
        out[start:stop] = np.median(windows, axis=-1)
    return out


def _to_bands(magnitude: np.ndarray, count: int = 160) -> np.ndarray:
    """Sum a spectrogram into log-spaced frequency bands.

    Telling a struck sound from a held one does not need individual FFT bins, and the
    median filtering that does it is by far the most expensive stage here. Reducing a
    thousand bins to a couple of hundred bands cuts that cost by an order of magnitude
    while preserving what the separation actually keys on: whether energy is smeared
    across frequency at one instant, or steady in frequency across time.
    """
    bins = magnitude.shape[1]
    if count >= bins:
        return magnitude
    edges = np.unique(np.round(np.geomspace(1, bins, count + 1)).astype(int))
    edges[0] = 0
    return np.stack([magnitude[:, edges[i]:max(edges[i] + 1, edges[i + 1])].sum(axis=1)
                     for i in range(len(edges) - 1)], axis=1).astype(np.float32)


def _flux(magnitude: np.ndarray) -> np.ndarray:
    """Spectral flux: how much energy rose since the previous frame.

    Only increases count, because energy falling away is not a new note.
    """
    compressed = np.log1p(magnitude * 12.0)
    rise = np.maximum(np.diff(compressed, axis=0), 0.0).sum(axis=1)
    peak = float(rise.max()) if len(rise) else 0.0
    if peak > 0:
        rise = rise / peak
    return rise.astype(np.float32)


def _resample(curve: np.ndarray, count: int) -> np.ndarray:
    """Stretch or squash a curve to ``count`` points by linear interpolation."""
    if count <= 0:
        return np.zeros(0, dtype=np.float32)
    if len(curve) == 0:
        return np.zeros(count, dtype=np.float32)
    if len(curve) == 1:
        return np.full(count, float(curve[0]), dtype=np.float32)
    source = np.linspace(0.0, 1.0, len(curve))
    target = np.linspace(0.0, 1.0, count)
    return np.interp(target, source, curve).astype(np.float32)


def condition(raw: np.ndarray, fps: float, attack: float, decay: float,
              gamma: float = 1.5) -> np.ndarray:
    """Turn a raw feature into a usable 0..1 control signal.

    Raw audio features are far too jumpy to drive a picture directly; connected straight
    to a scale or a brightness they produce strobing. The design's four-stage pipeline is
    applied here: adaptive normalisation against this track's own 5th/95th percentiles so
    the range is stable whatever the mix level, an envelope follower with a fast attack
    and a slow decay so transients stay punchy but nothing flickers, then a power curve to
    emphasise peaks over the quiet middle.
    """
    if len(raw) == 0:
        return raw.astype(np.float32)

    low, high = np.percentile(raw, 5), np.percentile(raw, 95)
    normalised = np.clip((raw - low) / (high - low + 1e-6), 0.0, 1.0)

    step = 1.0 / max(fps, 1e-6)
    # The design quotes attack constants as low as 2ms. Those belong to audio-rate
    # processing; at 30 frames per second a 2ms attack is indistinguishable from an
    # instant jump, and an instant jump in a control signal is precisely the strobing
    # this pipeline exists to prevent. So the attack is floored at a couple of frames,
    # which keeps it far faster than the decay -- the asymmetry that matters -- while
    # guaranteeing the curve cannot leap in a single frame.
    attack = max(attack, step * 2.5)
    rise = float(np.exp(-step / max(attack, 1e-4)))
    fall = float(np.exp(-step / max(decay, 1e-4)))

    out = np.empty_like(normalised, dtype=np.float32)
    current = float(normalised[0])
    for index, value in enumerate(normalised):
        # Asymmetric: chase upward quickly, release slowly.
        alpha = rise if value > current else fall
        current = alpha * current + (1.0 - alpha) * float(value)
        out[index] = current
    return np.power(out, gamma).astype(np.float32)


# --------------------------------------------------------------------- model

@dataclass
class GridBeat:
    """One beat of the metrical grid, with its place in the bar."""

    time: float
    #: 1-based position within the bar. 1 is the downbeat.
    beat_in_bar: int
    bar: int
    strength: float
    #: What the loudest thing at this instant appears to be, from :data:`VOICES`.
    voice: str = "drums"
    energy: float = 0.0

    @property
    def is_downbeat(self) -> bool:
        return self.beat_in_bar == 1

    def to_dict(self) -> dict:
        return {"time": round(self.time, 4), "beat_in_bar": self.beat_in_bar,
                "bar": self.bar, "is_downbeat": self.is_downbeat,
                "strength": round(self.strength, 4), "voice": self.voice,
                "energy": round(self.energy, 4)}


@dataclass
class Section:
    """A stretch of the track that behaves as one musical unit."""

    label: str
    start: float
    end: float
    energy: float
    #: Index of the repetition group this section belongs to. Sections sharing a group
    #: sound alike, which is how a repeated chorus is recognised.
    group: int = 0

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        return {"type": self.label, "start_ms": int(self.start * 1000),
                "end_ms": int(self.end * 1000), "energy": round(self.energy, 3),
                "group": self.group}


@dataclass
class Burst:
    """A run of hits close enough together to read as one rhythmic figure.

    The design's worked example -- five quick beats then a violin -- is this: the run
    reveals a photo piece by piece and the sustained note that follows completes it.
    """

    start: float
    end: float
    times: list[float] = field(default_factory=list)
    #: True when a held, harmonic sound follows the run rather than another hit.
    resolves_to_sustain: bool = False

    @property
    def count(self) -> int:
        return len(self.times)

    def to_dict(self) -> dict:
        return {"start_ms": int(self.start * 1000), "end_ms": int(self.end * 1000),
                "hit_count": self.count,
                "avg_interval_ms": int(round(
                    1000 * (self.end - self.start) / max(1, self.count - 1))),
                "resolves_to_sustain": self.resolves_to_sustain}


@dataclass
class MusicAnalysis:
    """Everything the choreographer needs to know about one track."""

    duration: float = 0.0
    bpm: float = 0.0
    meter: int = 4
    fps: float = 30.0

    grid: list[GridBeat] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    bursts: list[Burst] = field(default_factory=list)

    percussive_onsets: list[float] = field(default_factory=list)
    harmonic_onsets: list[float] = field(default_factory=list)
    drops: list[float] = field(default_factory=list)

    #: Conditioned 0..1 curves, one value per *video* frame.
    energy: np.ndarray | None = None
    bass: np.ndarray | None = None
    mid: np.ndarray | None = None
    high: np.ndarray | None = None
    brightness: np.ndarray | None = None

    #: Raw onset envelope and its time base, kept for drawing the beat editor.
    envelope: np.ndarray | None = None
    envelope_times: np.ndarray | None = None

    notes: list[str] = field(default_factory=list)

    #: Which reading produced this. Recorded so a report says how it was listened to.
    preset: str = "auto"

    # -- convenience ---------------------------------------------------

    @property
    def beat_duration(self) -> float:
        return 60.0 / self.bpm if self.bpm > 1e-6 else 0.5

    def beat_times(self) -> list[float]:
        return [b.time for b in self.grid]

    def downbeat_times(self) -> list[float]:
        return [b.time for b in self.grid if b.is_downbeat]

    def phrase_times(self, bars: int = 4) -> list[float]:
        """Starts of every ``bars``-bar phrase, where the biggest changes belong."""
        return [b.time for b in self.grid
                if b.is_downbeat and (b.bar - 1) % max(1, bars) == 0]

    def section_at(self, time: float) -> Section | None:
        for section in self.sections:
            if section.start <= time < section.end:
                return section
        return self.sections[-1] if self.sections else None

    def curve_at(self, curve: np.ndarray | None, time: float) -> float:
        """Sample one of the conditioned curves at a moment in time."""
        if curve is None or not len(curve):
            return 0.0
        index = int(round(time * self.fps))
        return float(curve[min(max(index, 0), len(curve) - 1)])

    def energy_at(self, time: float) -> float:
        return self.curve_at(self.energy, time)

    def envelope_plot(self, points: int = 900) -> list[float]:
        """The onset envelope reduced for drawing, keeping peaks rather than averages."""
        if self.envelope is None or not len(self.envelope):
            return []
        chunks = np.array_split(self.envelope, min(points, len(self.envelope)))
        return [round(float(chunk.max()), 4) for chunk in chunks if len(chunk)]

    def energy_plot(self, points: int = 200) -> list[float]:
        if self.energy is None or not len(self.energy):
            return []
        chunks = np.array_split(self.energy, min(points, len(self.energy)))
        return [round(float(chunk.mean()), 4) for chunk in chunks if len(chunk)]

    def to_dict(self) -> dict:
        """The Event Timeline, in the shape the design specifies."""
        return {
            "bpm": round(self.bpm, 2),
            "duration_ms": int(self.duration * 1000),
            "time_signature": f"{self.meter}/4",
            "fps": self.fps,
            "sections": [s.to_dict() for s in self.sections],
            "beats": [b.to_dict() for b in self.grid],
            "downbeats": [round(t, 4) for t in self.downbeat_times()],
            "percussive_onsets": [round(t, 4) for t in self.percussive_onsets],
            "harmonic_onsets": [round(t, 4) for t in self.harmonic_onsets],
            "drops": [round(t, 4) for t in self.drops],
            "burst_patterns": [b.to_dict() for b in self.bursts],
            "energy_curves": {
                "energy": self.energy_plot(400),
                "bass": [round(float(v), 4) for v in _resample(
                    self.bass if self.bass is not None else np.zeros(1), 400)],
                "brightness": [round(float(v), 4) for v in _resample(
                    self.brightness if self.brightness is not None
                    else np.zeros(1), 400)],
            },
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------- stages

def separate(magnitude: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Split a spectrogram into held and struck content.

    A sustained note is steady over time and narrow in frequency, so a median along time
    keeps it. A drum hit is the opposite -- brief but spread across the spectrum -- so a
    median along frequency keeps that. Soft masks built from the two then divide the
    original energy between them. This is the classic median-filter method; it is what
    stands in for Demucs here, and it is enough to tell a drum from a held note even
    though it cannot name the instrument.
    """
    banded = _to_bands(magnitude)
    harmonic = _median_filter(banded, 31, axis=0)
    percussive = _median_filter(banded, 9, axis=1)
    total = harmonic ** 2 + percussive ** 2 + 1e-9
    return (banded * (harmonic ** 2 / total),
            banded * (percussive ** 2 / total))


def estimate_grid(flux: np.ndarray, low_flux: np.ndarray, seed_bpm: float,
                  duration: float) -> tuple[float, int, int, float]:
    """Settle the tempo and the metre together, not one after the other.

    Choosing a tempo first and a metre afterwards is what produces the classic failure:
    autocorrelation reports a tempo four thirds or three halves of the real one, the metre
    stage takes that as given, and the best it can do is find a bar length that fits the
    wrong beat -- so the bar comes out roughly right while the beats inside it are wrong.
    Everything downstream then drifts against the music even though photo changes look
    correctly placed.

    Deciding jointly fixes it, because the two constrain each other. Bar lines carry
    distinctive evidence -- kick drums and bass land there, and chords change there -- so
    asking "which combination of tempo and metre puts strong low-frequency hits on bar one
    and weaker ones elsewhere" can rule out a tempo that no metre can make sense of.

    Returns the tempo, the metre, which beat of the grid is the first bar line, and the
    grid's offset in seconds.
    """
    if seed_bpm <= 1e-6 or not len(flux):
        return 0.0, 4, 0, 0.0

    # The seed and the ratios it is most often wrong by, each with how much it should be
    # trusted. Autocorrelation is usually right about the pulse and wrong mainly by an
    # octave, so the seed itself and its halves and doubles are likelier than the ratios
    # that change the metre. Without this weighting the estimator will happily take a
    # three-against-four reading that halves the bar and calls the half a bar.
    factors = {1.0: 1.0, 0.5: 0.92, 2.0: 0.92,
               4.0 / 3.0: 0.90, 0.75: 0.90, 1.5: 0.85, 2.0 / 3.0: 0.85}

    # Three beats to the bar is treated as a last resort, and the margin is deliberately
    # large.
    #
    # The reason is a specific trap rather than mere rarity. Reading a four-four track as
    # three-four at one and a half times the tempo produces a bar exactly half the true
    # length, so every other detected bar line is a real one and the rest fall on the
    # backbeat -- which in most popular music is also accented. That scores extremely well
    # while being wrong, and telling the two apart properly means noticing that the evidence
    # on those bar lines alternates in strength. Requiring a wide margin is the cheap
    # approximation of that, and it costs only the genuine waltz, which is rare in the
    # material this is used on.
    prior_meter = {4: 1.0, 3: 0.45}

    best = (seed_bpm, 4, 0, 0.0, -1e9)
    for factor, trust in factors.items():
        bpm = seed_bpm * factor
        if not 60.0 <= bpm <= 200.0:
            continue
        period = 60.0 / bpm
        offset, on_beat = _best_phase(flux, period)
        if on_beat <= 0:
            continue

        times = np.arange(offset, max(duration - 1e-3, offset + period), period)
        if len(times) < 8:
            continue
        frames = np.clip(np.round(times * ANALYSIS_FPS).astype(int),
                         0, len(flux) - 1)
        low_frames = np.clip(frames, 0, max(0, len(low_flux) - 1))
        # Weighted towards the low end, which is where the evidence for a bar line is.
        evidence = low_flux[low_frames] * 2.0 + flux[frames]
        scale = float(evidence.mean()) + 1e-9

        # Tapping preference: mid tempos are likelier than extremes.
        prior_tempo = 1.0 - min(1.0, abs(np.log2(bpm / 110.0))) * 0.3

        for meter in (4, 3):
            if len(times) < meter * 2:
                continue
            for phase in range(meter):
                picked = evidence[phase::meter]
                others = np.delete(evidence, np.arange(phase, len(evidence), meter))
                if not len(picked) or not len(others):
                    continue
                # Relative, so it can be compared across different tempos.
                contrast = float(picked.mean() - others.mean()) / scale
                score = (on_beat * 0.7 + contrast * 1.3) \
                    * prior_meter[meter] * prior_tempo * trust
                if score > best[4]:
                    best = (bpm, meter, phase, offset, score)

    return best[0], best[1], best[2], best[3]


def refine_tempo(flux: np.ndarray, seed_bpm: float) -> float:
    """Correct the octave errors a plain autocorrelation is prone to.

    Autocorrelation happily reports half or double the tempo a listener would tap. Each
    candidate is scored by how much onset energy actually lands on its grid, with a mild
    preference for the 70--150 BPM range where most music sits, and the winner is kept.
    """
    if seed_bpm <= 1e-6:
        return 0.0
    candidates = [seed_bpm * f for f in (0.5, 1.0, 2.0, 1.5, 2.0 / 3.0)]
    best, best_score = seed_bpm, -1.0
    for bpm in candidates:
        if not 55.0 <= bpm <= 190.0:
            continue
        _, score = _best_phase(flux, 60.0 / bpm)
        # Human tapping preference: mid tempos are more likely than extremes.
        prior = 1.0 - min(1.0, abs(np.log2(bpm / 105.0))) * 0.25
        score *= prior
        if score > best_score:
            best, best_score = bpm, score
    return float(best)


def _best_phase(flux: np.ndarray, period: float) -> tuple[float, float]:
    """The grid offset that captures the most onset energy, and its score."""
    if period <= 1e-6 or not len(flux):
        return 0.0, 0.0
    frames_per_beat = period * ANALYSIS_FPS
    if frames_per_beat < 2:
        return 0.0, 0.0
    best_offset, best_score = 0.0, -1.0
    # Sixteen trial offsets across one beat is finer than the ear can resolve.
    for step in range(16):
        offset = step / 16.0 * frames_per_beat
        positions = np.arange(offset, len(flux) - 1, frames_per_beat)
        if len(positions) < 2:
            continue
        score = float(flux[np.round(positions).astype(int)].mean())
        if score > best_score:
            best_offset, best_score = offset / ANALYSIS_FPS, score
    return best_offset, max(best_score, 0.0)


def build_grid(flux: np.ndarray, onsets: list[float], strengths: list[float],
               bpm: float, duration: float) -> list[float]:
    """Lay a regular beat grid over the track and pull it onto real hits.

    A pure grid drifts away from the music; pure onsets are irregular and miss beats the
    player implied but did not strike. Doing both gives a grid that is regular enough to
    count bars against and close enough to the audio to cut on.
    """
    period = 60.0 / bpm if bpm > 1e-6 else 0.0
    if period <= 1e-6:
        return list(onsets)

    offset, _ = _best_phase(flux, period)
    times = list(np.arange(offset, max(duration - 1e-3, offset + period), period))
    if not times:
        return list(onsets)

    if onsets:
        onset_array = np.asarray(onsets, dtype=np.float64)
        tolerance = period * 0.22
        pulled = []
        for time_s in times:
            nearest = onset_array[int(np.argmin(np.abs(onset_array - time_s)))]
            pulled.append(float(nearest) if abs(nearest - time_s) <= tolerance
                          else float(time_s))
        # Snapping can push two grid beats onto the same hit; keep the grid monotonic.
        times = []
        for value in pulled:
            if not times or value - times[-1] > period * 0.4:
                times.append(value)
    return [t for t in times if 0.0 <= t <= duration]


def find_downbeats(times: list[float], low_flux: np.ndarray, flux: np.ndarray,
                   meter_options: tuple[int, ...] = (4, 3)) -> tuple[int, int]:
    """Work out the metre and which beat starts the bar.

    Bar ones are not just louder, they are *lower*: kick drums and bass notes land there,
    and chords tend to change there. So each candidate phase is scored on low-frequency
    onset energy as well as overall onset energy, and the phase that consistently catches
    both wins. Returns the metre and the index of the first downbeat.
    """
    if len(times) < 4:
        return 4, 0

    frames = np.clip(np.round(np.asarray(times) * ANALYSIS_FPS).astype(int),
                     0, max(0, len(flux) - 1))
    low_frames = np.clip(frames, 0, max(0, len(low_flux) - 1))
    overall = flux[frames]
    lows = low_flux[low_frames]
    # Low end carries most of the evidence for a bar line, so it is weighted higher.
    evidence = lows * 2.0 + overall

    # Four beats to the bar is overwhelmingly the most common metre in popular music, and
    # a three-beat reading of a four-beat track will win on noise often enough to matter:
    # every third beat is sometimes loud by chance. So three has to beat four by a clear
    # margin, not merely edge it. This is the same kind of prior the tempo estimate uses.
    prior = {4: 1.0, 3: 0.72, 2: 0.6}

    best = (4, 0, -1e9)
    for meter in meter_options:
        if len(times) < meter * 2:
            continue
        for phase in range(meter):
            picked = evidence[phase::meter]
            others = np.delete(evidence, np.arange(phase, len(evidence), meter))
            if not len(picked) or not len(others):
                continue
            # A real downbeat phase stands out from the beats around it.
            score = float(picked.mean() - others.mean()) * prior.get(meter, 0.5)
            if score > best[2]:
                best = (meter, phase, score)
    return best[0], best[1]


def _beat_features(magnitude: np.ndarray, times: list[float],
                   bands: int = 24) -> np.ndarray:
    """One normalised timbre vector per beat, for comparing beats to each other."""
    if not times:
        return np.zeros((0, bands), dtype=np.float32)
    bins = magnitude.shape[1]
    # Roughly logarithmic band edges: fine at the bottom where pitch lives, coarse at
    # the top where only noise character matters.
    edges = np.unique(np.round(
        np.geomspace(1, bins - 1, bands + 1)).astype(int))
    frames = np.clip(np.round(np.asarray(times) * ANALYSIS_FPS).astype(int),
                     0, magnitude.shape[0] - 1)

    vectors = []
    for index, start in enumerate(frames):
        stop = frames[index + 1] if index + 1 < len(frames) else magnitude.shape[0]
        stop = max(start + 1, min(stop, magnitude.shape[0]))
        window = magnitude[start:stop]
        spectrum = np.log1p(window.mean(axis=0) * 12.0)
        vector = np.array([spectrum[edges[i]:edges[i + 1]].mean()
                           for i in range(len(edges) - 1)], dtype=np.float32)
        vectors.append(vector)

    features = np.asarray(vectors, dtype=np.float32)
    if features.shape[1] < bands:
        features = np.pad(features, ((0, 0), (0, bands - features.shape[1])))
    # Compare shape, not loudness, so the same riff played quietly still matches.
    norms = np.linalg.norm(features, axis=1, keepdims=True) + 1e-9
    return features / norms


def _novelty(similarity: np.ndarray, width: int = 8) -> np.ndarray:
    """Foote novelty: how unlike the past the future is, at each point.

    A checkerboard kernel slid down the diagonal of the similarity matrix responds when
    the music before a point stops resembling the music after it, which is what a section
    boundary is.
    """
    size = width * 2
    if similarity.shape[0] < size + 2:
        return np.zeros(similarity.shape[0], dtype=np.float32)

    kernel = np.ones((size, size), dtype=np.float32)
    kernel[:width, width:] = -1.0
    kernel[width:, :width] = -1.0
    # Taper so beats far from the centre matter less than those either side of it.
    taper = np.hanning(size).astype(np.float32)
    kernel *= np.outer(taper, taper)

    curve = np.zeros(similarity.shape[0], dtype=np.float32)
    for centre in range(width, similarity.shape[0] - width):
        block = similarity[centre - width:centre + width,
                           centre - width:centre + width]
        curve[centre] = float((block * kernel).sum())
    curve = np.maximum(curve, 0.0)
    peak = float(curve.max())
    return curve / peak if peak > 0 else curve


def segment(grid: list[GridBeat], features: np.ndarray, energy: np.ndarray,
            fps: float, meter: int) -> list[Section]:
    """Cut the track into sections and give each one a name.

    Boundaries come from novelty in the self-similarity matrix, then get pulled onto the
    nearest bar line, because music does not change section mid-bar. Naming is heuristic:
    sections that sound alike are grouped, the loudest recurring group is taken to be the
    chorus, quiet ends are the intro and outro, a short climbing section before a loud one
    is a build, and a quiet one after a loud one is a breakdown.
    """
    if len(grid) < meter * 4 or features.shape[0] != len(grid):
        end = grid[-1].time if grid else 0.0
        return [Section("verse", 0.0, max(end, 1.0), 0.5, 0)]

    similarity = features @ features.T
    novelty = _novelty(similarity, width=min(8, max(2, len(grid) // 8)))

    # Candidate boundaries: novelty peaks that stand clear of the local average.
    threshold = float(novelty.mean() + novelty.std() * 0.25)
    candidates = [i for i in range(1, len(novelty) - 1)
                  if novelty[i] >= threshold
                  and novelty[i] >= novelty[i - 1] and novelty[i] >= novelty[i + 1]]

    # Snap to bar lines and keep sections musically long enough to register.
    downbeat_indices = [i for i, b in enumerate(grid) if b.is_downbeat]
    min_bars, max_bars = 2, 8
    bounds = [0]
    for index in candidates:
        if not downbeat_indices:
            break
        nearest = min(downbeat_indices, key=lambda d: abs(d - index))
        bars_since = (grid[nearest].bar - grid[bounds[-1]].bar)
        if bars_since >= min_bars and nearest > bounds[-1]:
            bounds.append(nearest)

    # A homogeneous passage yields no novelty peak at all, which can leave a single
    # section running for a minute. That is useless to choreograph against: the whole
    # point of sections is to change the visual language periodically. Long stretches are
    # therefore split on their strongest interior bar line.
    if downbeat_indices:
        split = True
        while split:
            split = False
            for position in range(len(bounds) - 1):
                start, stop = bounds[position], bounds[position + 1]
                if grid[stop].bar - grid[start].bar <= max_bars:
                    continue
                inner = [d for d in downbeat_indices
                         if grid[d].bar - grid[start].bar >= min_bars
                         and grid[stop].bar - grid[d].bar >= min_bars]
                if not inner:
                    continue
                bounds.insert(position + 1,
                              max(inner, key=lambda d: float(novelty[d])))
                split = True
                break
        # The tail after the last boundary needs the same treatment.
        while grid[-1].bar - grid[bounds[-1]].bar > max_bars:
            inner = [d for d in downbeat_indices
                     if grid[d].bar - grid[bounds[-1]].bar >= min_bars
                     and grid[-1].bar - grid[d].bar >= min_bars]
            if not inner:
                break
            bounds.append(min(inner))
    bounds.append(len(grid))

    raw: list[Section] = []
    for position in range(len(bounds) - 1):
        start_index, stop_index = bounds[position], bounds[position + 1]
        if stop_index <= start_index:
            continue
        start = grid[start_index].time
        end = (grid[stop_index].time if stop_index < len(grid)
               else grid[-1].time + (60.0 / max(1e-6, 120.0)))
        window = energy[int(start * fps):max(int(end * fps), int(start * fps) + 1)]
        level = float(window.mean()) if len(window) else 0.0
        raw.append(Section("verse", start, end, level, position))

    if not raw:
        return [Section("verse", 0.0, max(grid[-1].time, 1.0), 0.5, 0)]

    _group_sections(raw, features, bounds)
    _label_sections(raw)
    return raw


def _span_track(sections: list[Section], duration: float) -> list[Section]:
    """Stretch the first and last section to cover the track end to end.

    The beat grid starts at the first detected pulse and stops at the last, so a little
    audio sits outside it. Sections have to cover everything or the choreographer would
    have moments with no style attached, so the outer two are extended.
    """
    if not sections:
        return sections
    sections[0].start = 0.0
    sections[-1].end = max(duration, sections[-1].start + 0.1)
    return sections


def _group_sections(sections: list[Section], features: np.ndarray,
                    bounds: list[int]) -> None:
    """Mark sections that sound like each other with the same group number."""
    centroids = []
    for position in range(len(sections)):
        block = features[bounds[position]:bounds[position + 1]]
        centroids.append(block.mean(axis=0) if len(block) else features[0])

    groups: list[int] = []
    next_group = 0
    for index, centroid in enumerate(centroids):
        assigned = None
        for earlier in range(index):
            similarity = float(np.dot(centroid, centroids[earlier]) /
                               (np.linalg.norm(centroid) *
                                np.linalg.norm(centroids[earlier]) + 1e-9))
            if similarity > 0.985:
                assigned = groups[earlier]
                break
        if assigned is None:
            assigned = next_group
            next_group += 1
        groups.append(assigned)

    for section, group in zip(sections, groups):
        section.group = group


def _label_sections(sections: list[Section]) -> None:
    """Name each section from its energy, its position and what repeats."""
    levels = np.array([s.energy for s in sections], dtype=np.float32)
    loud = float(np.percentile(levels, 70)) if len(levels) else 0.5
    quiet = float(np.percentile(levels, 30)) if len(levels) else 0.3

    # The chorus is the loudest thing that comes back. If nothing repeats, it is simply
    # the loudest section.
    repeated = {}
    for section in sections:
        repeated.setdefault(section.group, []).append(section)
    chorus_group = None
    best = -1.0
    for group, members in repeated.items():
        if len(members) < 2:
            continue
        level = float(np.mean([m.energy for m in members]))
        if level > best:
            chorus_group, best = group, level
    if chorus_group is None and len(sections):
        chorus_group = sections[int(np.argmax(levels))].group

    for index, section in enumerate(sections):
        first, last = index == 0, index == len(sections) - 1
        previous = sections[index - 1] if index else None
        following = sections[index + 1] if index + 1 < len(sections) else None

        if first and section.energy <= loud:
            section.label = "intro"
        elif last and section.energy <= loud:
            section.label = "outro"
        elif section.group == chorus_group and section.energy >= quiet:
            section.label = "chorus"
        elif (following is not None and section.energy < following.energy - 0.12
              and section.duration <= 12.0):
            section.label = "build"
        elif (previous is not None and previous.label in ("chorus", "drop")
              and section.energy <= quiet):
            section.label = "breakdown"
        elif section.energy <= quiet:
            section.label = "bridge"
        else:
            section.label = "verse"

    # A very loud section right after a build is the drop, which wants its own treatment.
    for index in range(1, len(sections)):
        if sections[index - 1].label == "build" and sections[index].energy >= loud:
            sections[index].label = "drop"


def find_bursts(onsets: list[float], harmonic: list[float], period: float,
                minimum: int = 3) -> list[Burst]:
    """Find runs of hits tight enough to read as one figure.

    "Tight" is measured against the beat, not in fixed milliseconds, so the same figure
    is recognised at any tempo. A run that gives way to a held note is flagged, because
    that combination is the one the design singles out: the run builds the picture up and
    the held note completes it.
    """
    if len(onsets) < minimum:
        return []
    gap = max(0.09, period * 0.34) if period > 0 else 0.16
    harmonic_array = np.asarray(harmonic, dtype=np.float64) if harmonic else None

    bursts: list[Burst] = []
    run = [onsets[0]]
    for time_s in onsets[1:]:
        if time_s - run[-1] <= gap:
            run.append(time_s)
            continue
        if len(run) >= minimum:
            bursts.append(_make_burst(run, time_s, harmonic_array, gap))
        run = [time_s]
    if len(run) >= minimum:
        bursts.append(_make_burst(run, None, harmonic_array, gap))
    return bursts


def _make_burst(run: list[float], next_onset: float | None,
                harmonic: np.ndarray | None, gap: float) -> Burst:
    sustained = False
    if next_onset is not None and harmonic is not None and len(harmonic):
        # A held note after the run: the next event is harmonic and no longer tight.
        near = harmonic[np.abs(harmonic - next_onset) < gap * 0.8]
        sustained = bool(len(near)) and (next_onset - run[-1]) > gap
    return Burst(start=run[0], end=run[-1], times=list(run),
                 resolves_to_sustain=sustained)


def find_drops(energy: np.ndarray, fps: float) -> list[float]:
    """Moments where the track goes from held back to full force at once.

    A drop is the single most useful event to know about, because it is where the biggest
    visual moment belongs. It shows up as energy stepping from low to high inside a
    fraction of a second.
    """
    if energy is None or len(energy) < 4:
        return []
    look = max(2, int(round(0.5 * fps)))
    hold = max(2, int(round(1.0 * fps)))
    drops: list[float] = []
    last = -1e9
    for index in range(look, len(energy) - hold):
        before = float(energy[index - look:index].mean())
        after = float(energy[index:index + hold].mean())
        time_s = index / fps
        # A drop is a step change that *stays* up. Requiring the level to hold for a
        # second afterwards rejects ordinary loud beats, which spike and fall straight
        # back and would otherwise each be reported as a drop.
        if before < 0.25 and after > 0.70 and (after - before) > 0.45 \
                and time_s - last > 12.0:
            drops.append(round(time_s, 3))
            last = time_s
    return drops


def _voice_for(time: float, bass: np.ndarray, mid: np.ndarray, high: np.ndarray,
               perc_set: np.ndarray, harm_set: np.ndarray, fps: float) -> str:
    """Guess what dominates at one instant.

    Not instrument recognition: a comparison of which band holds the energy and whether
    the nearest onset was struck or held. Good enough to choose between a sharp reaction
    and a smooth one, which is all the choreographer asks of it.
    """
    index = min(max(int(round(time * fps)), 0), max(0, len(bass) - 1))
    low = float(bass[index]) if len(bass) else 0.0
    middle = float(mid[index]) if len(mid) else 0.0
    top = float(high[index]) if len(high) else 0.0

    percussive = bool(len(perc_set)) and float(np.min(np.abs(perc_set - time))) < 0.05
    harmonic = bool(len(harm_set)) and float(np.min(np.abs(harm_set - time))) < 0.05

    if max(low, middle, top) < 0.10:
        return "ambient"
    # Whether the sound was struck or held is the strongest evidence available, so it is
    # tested before band balance. Comparing the high band against the mid one is a poor
    # test for a drum: in a mix the mid band almost always holds more energy, which would
    # attribute nearly every hit to the melody.
    if percussive and not harmonic:
        return "drums"
    if low > middle and low > top:
        return "bass"
    if harmonic or middle >= max(low, top):
        return "melody"
    return "drums" if percussive else "melody"


# --------------------------------------------------------------------- entry

def analyse(path: str | Path, duration: float | None = None, fps: float = 30.0,
            sensitivity: float = 1.0, preset: str = "auto") -> MusicAnalysis:
    """Listen to a track and describe it.

    ``duration`` of None or 0 analyses the whole thing. ``fps`` decides the resolution of
    the energy curves, so pass the frame rate the video will be rendered at and every
    curve can then be indexed straight by frame number.

    ``preset`` names one of :data:`PRESETS`. The default works everything out from the
    music; the others overrule part of that reading, because a tempo or metre estimate
    that is confidently wrong cannot be argued out of it and is faster to simply replace.
    """
    reading = PRESETS.get(preset or "auto", PRESETS["auto"])
    samples = beat_mod.load_audio(path, duration or None)
    total = len(samples) / SAMPLE_RATE
    magnitude = beat_mod._stft_magnitude(samples)

    harmonic_mag, percussive_mag = separate(magnitude)

    overall_flux = _flux(magnitude)
    perc_flux = _flux(percussive_mag)
    harm_flux = _flux(harmonic_mag)
    times = (np.arange(len(overall_flux)) + 1) * HOP / SAMPLE_RATE

    # Low band only, for deciding where bars start.
    bin_freqs = np.fft.rfftfreq(FRAME, 1.0 / SAMPLE_RATE)
    low_bins = bin_freqs < 150.0
    low_flux = _flux(magnitude[:, low_bins])

    perc_onsets, perc_strengths = beat_mod.pick_peaks(
        perc_flux, times, sensitivity=sensitivity, min_gap=0.055)
    harm_onsets, _ = beat_mod.pick_peaks(
        harm_flux, times, sensitivity=sensitivity * 0.85, min_gap=0.12)
    all_onsets, all_strengths = beat_mod.pick_peaks(
        overall_flux, times, sensitivity=sensitivity, min_gap=0.06)

    # Tempo and metre decided together: each rules out readings the other cannot explain.
    bpm, meter, phase, _offset = estimate_grid(
        overall_flux, low_flux, beat_mod.estimate_tempo(overall_flux), total)

    # A preset overrules either half of that. Halving or doubling is applied to whatever
    # was chosen rather than to a fixed number, so it stays relative to the actual song.
    if reading.tempo_factor:
        bpm = float(np.clip(bpm * reading.tempo_factor, 40.0, 240.0))
    if reading.meter:
        meter = reading.meter

    grid_times = build_grid(overall_flux, all_onsets, all_strengths, bpm, total)
    # Snapping the grid onto real hits can drop or add a beat at the edges, so where the
    # bar starts is confirmed against the grid actually built rather than assumed from the
    # even one it was scored on.
    meter, phase = find_downbeats(grid_times, low_flux, overall_flux,
                                  meter_options=(meter,))

    # Energy curves, at video frame resolution so they can be indexed by frame.
    frame_count = max(1, int(np.ceil(total * fps)))
    band = lambda lo, hi: magnitude[:, (bin_freqs >= lo) & (bin_freqs < hi)].sum(axis=1)
    bass = condition(_resample(band(20.0, 150.0), frame_count), fps, 0.010, 0.200)
    mid = condition(_resample(band(150.0, 4000.0), frame_count), fps, 0.030, 0.300)
    high = condition(_resample(band(4000.0, SAMPLE_RATE / 2), frame_count),
                     fps, 0.005, 0.080)

    centroid_weights = bin_freqs[None, :]
    centroid = ((magnitude * centroid_weights).sum(axis=1) /
                (magnitude.sum(axis=1) + 1e-9))
    brightness = condition(_resample(centroid, frame_count), fps, 0.050, 0.400)

    loudness = np.sqrt(np.maximum((magnitude ** 2).mean(axis=1), 0.0))
    energy = condition(_resample(loudness, frame_count), fps, 0.020, 0.350, gamma=1.2)

    perc_array = np.asarray(perc_onsets, dtype=np.float64)
    harm_array = np.asarray(harm_onsets, dtype=np.float64)
    strength_lookup = dict(zip(all_onsets, all_strengths))

    beat_bars: list[GridBeat] = []
    for index, time_s in enumerate(grid_times):
        position = (index - phase) % meter
        bar = (index - phase) // meter + 1
        nearest = min(strength_lookup, key=lambda t: abs(t - time_s)) \
            if strength_lookup else None
        strength = (strength_lookup[nearest]
                    if nearest is not None and abs(nearest - time_s) < 0.08 else 0.0)
        beat_bars.append(GridBeat(
            time=float(time_s), beat_in_bar=int(position) + 1, bar=int(max(bar, 1)),
            strength=float(strength),
            voice=_voice_for(time_s, bass, mid, high, perc_array, harm_array, fps),
            energy=float(energy[min(int(time_s * fps), frame_count - 1)]),
        ))

    features = _beat_features(magnitude, [b.time for b in beat_bars])
    sections = _span_track(segment(beat_bars, features, energy, fps, meter), total)
    bursts = find_bursts(all_onsets, harm_onsets,
                         60.0 / bpm if bpm > 1e-6 else 0.0)
    drops = find_drops(energy, fps)

    notes = [
        f"{len(beat_bars)} beats at {bpm:.1f} BPM in {meter}/4, "
        f"{len(sections)} sections",
        "structure and metre come from classical DSP, not a trained model, so section "
        "names are a best guess from energy and repetition",
    ]

    return MusicAnalysis(
        duration=total, bpm=bpm, meter=meter, fps=fps,
        grid=beat_bars, sections=sections, bursts=bursts,
        percussive_onsets=perc_onsets, harmonic_onsets=harm_onsets, drops=drops,
        energy=energy, bass=bass, mid=mid, high=high, brightness=brightness,
        envelope=overall_flux, envelope_times=times.astype(np.float32),
        notes=notes + ([] if reading.name == "auto" else
                       [f"read as {reading.name}: {reading.note}"]),
        preset=reading.name,
    )
