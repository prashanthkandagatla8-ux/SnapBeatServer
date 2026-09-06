"""Beat and onset detection.

Your CapCut draft carried 376 markers spaced exactly 0.4 s apart. That is a metronome
grid at 150 BPM, not a transcription of the music, so anything landing between grid lines
- syncopation, fills, off-beat hits - simply has no marker to cut on. That is why
intermediate beats were missed.

This finds actual note onsets instead, using spectral flux: for each short window,
measure how much energy *increased* per frequency band since the previous window and sum
those increases. A struck note raises many bands at once and produces a sharp spike; a
sustained note does not. Peaks in that signal are the onsets.

Implemented with numpy only, so there is no extra dependency to install.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import config

SAMPLE_RATE = 22050
FRAME = 2048
HOP = 256                      # ~11.6 ms, fine enough to place a cut accurately


@dataclass
class Beat:
    """One detected hit, with enough information to treat it differently."""

    time: float
    strength: float
    #: True for the main pulse, False for the quieter fills between. Templates use this
    #: to decide what a beat means: a strong beat might change the picture while the weak
    #: ones only reveal more of it.
    strong: bool = True

    def to_dict(self) -> dict:
        return {"time": round(self.time, 4),
                "strength": round(self.strength, 4),
                "strong": bool(self.strong)}


@dataclass
class BeatAnalysis:
    onsets: list[float] = field(default_factory=list)
    strengths: list[float] = field(default_factory=list)
    tempo_bpm: float = 0.0
    duration: float = 0.0
    #: The onset envelope and its time base, kept for plotting.
    envelope: np.ndarray | None = None
    envelope_times: np.ndarray | None = None
    #: Onsets with a strong/weak label attached.
    beats: list[Beat] = field(default_factory=list)

    def strongest(self, count: int) -> list[float]:
        """The ``count`` most prominent onsets, back in time order."""
        if not self.onsets:
            return []
        order = np.argsort(self.strengths)[::-1][:count]
        return sorted(float(self.onsets[i]) for i in order)

    def in_range(self, start: float, end: float) -> list[float]:
        return [t for t in self.onsets if start <= t <= end]

    def strong_times(self) -> list[float]:
        return [b.time for b in self.beats if b.strong]

    def weak_times(self) -> list[float]:
        return [b.time for b in self.beats if not b.strong]

    def envelope_plot(self, points: int = 900) -> list[float]:
        """The onset envelope reduced to a fixed number of points, for drawing.

        Downsampled by taking the peak of each bucket rather than the mean, so a short
        sharp hit still shows up as a spike instead of being averaged into the noise.
        """
        if self.envelope is None or not len(self.envelope):
            return []
        chunks = np.array_split(self.envelope, min(points, len(self.envelope)))
        return [round(float(chunk.max()), 4) for chunk in chunks if len(chunk)]


def load_audio(path: str | Path, duration: float | None = None) -> np.ndarray:
    """Decode any audio or video file to mono float samples via ffmpeg."""
    command = [config.FFMPEG, "-v", "error", "-i", str(path)]
    if duration:
        command += ["-t", f"{duration:.3f}"]
    command += ["-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"]
    kwargs: dict = dict(capture_output=True)
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    result = subprocess.run(command, **kwargs)
    if result.returncode != 0 or not result.stdout:
        detail = result.stderr.decode("utf-8", "replace")[:300]
        raise OSError(f"could not decode audio from {path}: {detail}")
    return np.frombuffer(result.stdout, dtype=np.float32).copy()


def _stft_magnitude(samples: np.ndarray) -> np.ndarray:
    """Magnitude spectrogram, shaped (frames, bins)."""
    if len(samples) < FRAME:
        samples = np.pad(samples, (0, FRAME - len(samples)))
    frame_count = 1 + (len(samples) - FRAME) // HOP
    window = np.hanning(FRAME).astype(np.float32)

    # A strided view avoids copying the signal once per frame.
    strides = (samples.strides[0] * HOP, samples.strides[0])
    frames = np.lib.stride_tricks.as_strided(
        samples, shape=(frame_count, FRAME), strides=strides, writeable=False
    )
    spectra = np.fft.rfft(frames * window, axis=1)
    return np.abs(spectra).astype(np.float32)


def onset_envelope(samples: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Spectral-flux onset strength over time."""
    magnitude = _stft_magnitude(samples)
    # Compress the dynamic range so quiet passages still register onsets.
    # A higher multiplier lifts quiet hits closer to loud ones, making fills
    # and ghost notes easier for the peak picker to catch.
    magnitude = np.log1p(magnitude * 12.0)

    difference = np.diff(magnitude, axis=0)
    # Only increases matter: energy falling away is not a new note.
    flux = np.maximum(difference, 0.0).sum(axis=1)

    # Normalise so thresholds are meaningful regardless of level.
    if flux.max() > 0:
        flux = flux / flux.max()

    times = (np.arange(len(flux)) + 1) * HOP / SAMPLE_RATE
    return flux.astype(np.float32), times.astype(np.float32)


def _moving_average(values: np.ndarray, width: int) -> np.ndarray:
    if width < 2:
        return values
    kernel = np.ones(width, dtype=np.float32) / width
    return np.convolve(values, kernel, mode="same")


def pick_peaks(flux: np.ndarray, times: np.ndarray, sensitivity: float = 1.0,
               min_gap: float = 0.06) -> tuple[list[float], list[float]]:
    """Find onset peaks with an adaptive threshold.

    A fixed threshold either floods quiet sections with false onsets or misses hits in
    loud ones. Comparing each point to a local average adapts to the music, and a minimum
    gap stops a single drum hit registering several times.

    The default ``min_gap`` of 60 ms is tight enough to catch sixteenth-note hi-hats at
    fast tempos while still preventing a single transient from registering twice.
    """
    if len(flux) < 3:
        return [], []

    # ~0.20 s of context either side — narrow enough that a quick fill next to a loud
    # downbeat does not have its baseline raised so high that it disappears.
    width = max(3, int(round(0.20 * SAMPLE_RATE / HOP)))
    baseline = _moving_average(flux, width)
    # Scale the required margin by how lively the track is overall.
    # A lower multiplier (0.38 vs the old 0.55) lets quieter ghost notes through.
    delta = float(np.mean(flux) + np.std(flux)) * 0.38 / max(sensitivity, 1e-3)
    threshold = baseline + delta

    onsets: list[float] = []
    strengths: list[float] = []
    last = -1e9
    for index in range(1, len(flux) - 1):
        value = flux[index]
        if value < threshold[index]:
            continue
        if value < flux[index - 1] or value < flux[index + 1]:
            continue                      # not a local maximum
        time_s = float(times[index])
        if time_s - last < min_gap:
            # Keep whichever of the two is stronger.
            if onsets and value > strengths[-1]:
                onsets[-1] = time_s
                strengths[-1] = float(value)
                last = time_s
            continue
        onsets.append(time_s)
        strengths.append(float(value))
        last = time_s

    return onsets, strengths


def estimate_tempo(flux: np.ndarray, low_bpm: float = 60.0,
                   high_bpm: float = 200.0) -> float:
    """Tempo from the autocorrelation of the onset envelope."""
    if len(flux) < 16:
        return 0.0
    centred = flux - flux.mean()
    correlation = np.correlate(centred, centred, mode="full")[len(centred) - 1:]
    if correlation[0] <= 0:
        return 0.0
    correlation = correlation / correlation[0]

    frames_per_second = SAMPLE_RATE / HOP
    low_lag = int(round(frames_per_second * 60.0 / high_bpm))
    high_lag = int(round(frames_per_second * 60.0 / low_bpm))
    high_lag = min(high_lag, len(correlation) - 1)
    if high_lag <= low_lag:
        return 0.0

    window = correlation[low_lag:high_lag]
    if window.max() <= 0:
        return 0.0
    best = int(np.argmax(window)) + low_lag
    return float(60.0 * frames_per_second / best)


def classify(onsets: list[float], strengths: list[float], tempo_bpm: float,
             strong_ratio: float = 0.4) -> list[Beat]:
    """Split onsets into the main pulse and the quieter fills between.

    Two signals are combined. Loudness alone is unreliable, because a fill can be
    momentarily louder than the downbeat it sits between. So a beat also counts as strong
    when it lands near the tempo grid, which is what makes the main pulse regular in the
    first place. A minimum spacing then stops two adjacent hits both claiming to be the
    same pulse.
    """
    if not onsets:
        return []

    values = np.asarray(strengths, dtype=np.float32)
    # Loud relative to the rest of this track, not to some absolute level.
    threshold = float(np.quantile(values, 1.0 - min(0.95, max(0.05, strong_ratio))))

    period = 60.0 / tempo_bpm if tempo_bpm > 1e-6 else 0.0
    # Anchor the grid on the most prominent onset rather than on zero, since a track
    # rarely starts exactly on its first downbeat.
    anchor = float(onsets[int(np.argmax(values))]) if len(onsets) else 0.0

    beats: list[Beat] = []
    for time_s, strength in zip(onsets, values):
        loud = strength >= threshold
        on_grid = False
        if period > 0:
            offset = abs((time_s - anchor) / period)
            on_grid = abs(offset - round(offset)) < 0.18
        beats.append(Beat(time=float(time_s), strength=float(strength),
                          strong=bool(loud or on_grid)))

    # Thin out strong beats that crowd each other; the quieter of a close pair is a fill.
    # A tighter factor (0.42 vs the old 0.55) keeps more closely-spaced strong beats so
    # quick accented fills are not demoted to weak.
    if period > 0:
        min_gap = period * 0.42
        last_strong = -1e9
        for beat in beats:
            if not beat.strong:
                continue
            if beat.time - last_strong < min_gap:
                beat.strong = False
            else:
                last_strong = beat.time

    # A track with no strong beats at all would make reveal-style templates impossible.
    if not any(b.strong for b in beats):
        for beat in beats:
            beat.strong = True

    return beats


def analyse(path: str | Path, duration: float | None = None,
            sensitivity: float = 1.0, min_gap: float = 0.06,
            strong_ratio: float = 0.4) -> BeatAnalysis:
    """Full analysis of one audio file."""
    samples = load_audio(path, duration)
    flux, times = onset_envelope(samples)
    onsets, strengths = pick_peaks(flux, times, sensitivity, min_gap)
    tempo = estimate_tempo(flux)
    return BeatAnalysis(
        onsets=onsets,
        strengths=strengths,
        tempo_bpm=tempo,
        duration=len(samples) / SAMPLE_RATE,
        envelope=flux,
        envelope_times=times,
        beats=classify(onsets, strengths, tempo, strong_ratio),
    )


def snap(times: list[float], onsets: list[float],
         tolerance: float = 0.12) -> list[float]:
    """Move each time to the nearest detected onset, when one is close enough.

    Useful for tightening a hand-made edit without rebuilding it: cuts that were already
    close to a hit land exactly on it, and cuts that were nowhere near one are left
    alone rather than yanked somewhere wrong.
    """
    if not onsets:
        return list(times)
    grid = np.asarray(onsets, dtype=np.float32)
    snapped = []
    for value in times:
        index = int(np.argmin(np.abs(grid - value)))
        nearest = float(grid[index])
        snapped.append(nearest if abs(nearest - value) <= tolerance else value)
    return snapped


def cuts_from_onsets(onsets: list[float], start: float = 0.0,
                     end: float | None = None,
                     min_duration: float = 0.16,
                     max_clips: int | None = None) -> list[float]:
    """Turn onset times into clip durations.

    Onsets closer together than ``min_duration`` are merged, because a clip shorter than
    roughly a sixth of a second reads as a flicker rather than a picture.
    """
    chosen = [t for t in onsets if t >= start and (end is None or t <= end)]
    if not chosen:
        return []
    if chosen[0] > start + 1e-3:
        chosen.insert(0, start)

    merged: list[float] = [chosen[0]]
    for time_s in chosen[1:]:
        if time_s - merged[-1] >= min_duration:
            merged.append(time_s)

    if end is not None and (not merged or merged[-1] < end - 1e-3):
        merged.append(end)

    durations = [round(merged[i + 1] - merged[i], 4) for i in range(len(merged) - 1)]
    if max_clips is not None:
        durations = durations[:max_clips]
    return durations
