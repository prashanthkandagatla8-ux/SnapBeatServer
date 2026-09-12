"""Background worker.

Runs in a daemon thread so submitting a job returns immediately. One job at a time: the
renderer already uses the machine's two cores, so running two would make both slower
without finishing either sooner.

There is no preview stage. A full render takes well under a minute, so a preview only
added a click; the finished file is opened in Explorer instead.
"""
from __future__ import annotations

import threading
import time
import shutil
from concurrent.futures import ThreadPoolExecutor
import traceback
from pathlib import Path

import numpy as np

from . import (analysis, animations, beats, choreography, config, effects, explain,
               photos as photo_mod, renderer, rhythm, store)
from .template import FILL_MODES, MIN_PHOTOS, REVEAL_SHAPES, Template

DIRECTIONS = {"up": "-y", "down": "y", "right": "x", "left": "-x"}

#: Stands in place of a template path when the edit is to be designed from the music
#: instead of applying a saved style.
AUTO_TEMPLATE = "auto"


class Worker:
    def __init__(self, job_store: store.JobStore):
        self.store = job_store
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._cancels: set[int] = set()
        self._lock = threading.Lock()
        self.current_job_id: int | None = None
        self._executor = ThreadPoolExecutor(max_workers=config.MAX_WORKERS)
        self._active_count = 0
        self._active_lock = threading.Lock()
        self._last_progress_commit = {}

    # -- control ---------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="beatcanvas-worker",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def notify(self) -> None:
        self._wake.set()

    def request_cancel(self, job_id: int) -> None:
        with self._lock:
            self._cancels.add(job_id)

    def _cancelled(self, job_id: int) -> bool:
        with self._lock:
            return job_id in self._cancels

    def _clear_cancel(self, job_id: int) -> None:
        with self._lock:
            self._cancels.discard(job_id)

    # -- loop ------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._wake.wait(timeout=2.0)
                self._wake.clear()
                with self._active_lock:
                    can_start = self._active_count < config.MAX_WORKERS
                while can_start:
                    # Fix: Use atomic claim that marks job as rendering immediately
                    job = self.store.claim_next_queued()
                    if not job:
                        break
                    with self._active_lock:
                        self._active_count += 1
                        can_start = self._active_count < config.MAX_WORKERS
                    self._executor.submit(self._process_job, job)
            except Exception:
                traceback.print_exc()
                time.sleep(2)
                continue

    def _process_job(self, job: store.Job) -> None:
        self.current_job_id = job.id
        try:
            self._run(job)
        except Exception as exc:
            err = traceback.format_exc(limit=8)
            print(f"Job {job.id} failed:\n{err}")
            self.store.update(job.id, status=store.STATUS_FAILED, stage="failed", error=err)
        finally:
            self.current_job_id = None
            import shutil
            dropped_folder = Path(job.options.get("photo_folder", "")).parent
            # Fix: Only delete folders strictly inside ROOT/_dropped — no substring match
            safe_root = config.ROOT / "_dropped"
            try:
                resolved_dropped = dropped_folder.resolve()
                resolved_safe = safe_root.resolve()
                if (resolved_dropped != resolved_safe
                    and resolved_dropped.is_relative_to(resolved_safe)
                    and dropped_folder.exists()):
                    shutil.rmtree(dropped_folder, ignore_errors=True)
            except (ValueError, OSError):
                pass  # is_relative_to can raise on some edge cases
            # Fix: Clean up _last_progress_commit to prevent memory leak
            self._last_progress_commit.pop(job.id, None)
            with self._active_lock:
                self._active_count -= 1
            self.notify()

    # -- preparing a job -------------------------------------------------

    def _photos_for(self, options: dict) -> list[Path]:
        explicit = options.get("photo_order") or []
        if explicit:
            pictures = [Path(p) for p in explicit if Path(p).exists()]
        else:
            folder = Path(options.get("photo_folder", ""))
            pictures = photo_mod.collect(folder) if folder.exists() else []
        if len(pictures) < MIN_PHOTOS:
            raise ValueError(
                f"pick at least {MIN_PHOTOS} photos, found {len(pictures)}")
        return pictures

    def _prepare(self, job: store.Job) -> tuple[Template, list[Path], dict]:
        options = job.options or {}

        # The automatic mode has no template behind it: the edit is designed from the
        # music itself, so there is nothing to load.
        auto = str(job.template) == AUTO_TEMPLATE

        width, height = config.FRAME_SIZES.get(
            options.get("frame", ""), (0, 0))

        pictures = self._photos_for(options)
        if options.get("auto_arrange") and len(pictures) >= 2:
            try:
                self.store.update(job.id, stage="arranging photos")
                from . import smart_arranger
                audio_p = options.get("music_path")
                reordered = smart_arranger.auto_arrange_photos(
                    [str(p) for p in pictures],
                    audio_path=str(audio_p) if audio_p else None,
                )
                if reordered:
                    pictures = [Path(p) for p in reordered]
                    options["photo_order"] = [str(p) for p in pictures]
            except Exception as exc:
                print(f"Auto-arrange failed in worker: {exc}")

        if auto:
            template = self._choreograph(job, options, width or 1080, height or 1920)
        else:
            template_path = Path(job.template)
            if not template_path.exists():
                template_path = config.TEMPLATE_DIR / job.template
            if not template_path.exists():
                raise FileNotFoundError("that template is no longer available")
            template = Template.load(template_path)

            # Applied before the timing is built so the aspect ratio the animations are
            # evaluated against is the one actually being rendered.
            if width and height:
                template.width, template.height = width, height

        if not auto and template.music_mode == "any":
            template = self._build_timing(job, template, options, len(pictures))
            # "Drop It" effect: inject burst montage at detected bass drops
            if options.get("drop_it"):
                template = self._apply_drop_it(template, options, len(pictures))
        elif not auto and not Path(template.audio_path).exists():
            raise FileNotFoundError(
                f"this template's music is missing: {template.audio_path}")

        # A choreographed edit chose its own movement, cover and intensity per photo from
        # the music. Letting the manual overrides flatten all of that back to one setting
        # would undo the point of it, so they only apply to a template.
        if not auto:
            axis = DIRECTIONS.get(options.get("direction", ""), "")
            intensity = options.get("intensity")
            cover = options.get("cover_mode")
            for clip in template.clips:
                if axis:
                    clip.axis = axis
                if intensity is not None:
                    clip.intensity = float(intensity)
                if cover in animations.COVER_MODES:
                    clip.cover_mode = cover

        fill = options.get("fill", "repeat")
        if fill not in FILL_MODES:
            fill = "repeat"
        return template.adapt(len(pictures), fill), pictures, options

    def _choreograph(self, job: store.Job, options: dict,
                     width: int, height: int) -> Template:
        """Design an edit from the music rather than applying a fixed style.

        This is the automatic mode. The track is analysed for its metre, structure and
        energy, then the choreographer decides what the picture does about each of those,
        so the result differs from song to song instead of imposing one look on all of them.
        """
        music = Path(options.get("music_path", ""))
        if not music.exists():
            raise FileNotFoundError(
                "the automatic mode designs the edit from the music, so choose a track")

        self.store.update(job.id, stage="listening to the music", progress=0.0)

        requested = options.get("max_seconds")
        wanted = float(requested) if requested is not None else 0.0
        fps = float(options.get("fps") or 30.0)

        # Analysing a little past the intended length gives the final photo a boundary to
        # end on instead of being cut off mid-phrase.
        # How the track is read is a choice, not a fact. The preset carries both halves of
        # it -- what the tempo and metre are taken to be, and how much difference between
        # two bars counts as a different figure -- so it has to reach the figure finding as
        # well as the listening, which means building the map here instead of letting the
        # choreographer fall back to its defaults.
        reading = analysis.preset(str(options.get("analysis") or "auto"))
        study = analysis.analyse(
            music, duration=(wanted + 6.0) if wanted else 0.0, fps=fps,
            sensitivity=float(options.get("sensitivity", 1.0)), preset=reading.name)

        self.store.update(job.id, stage="designing the edit", progress=0.0)
        look = effects.look(str(options.get("look") or "mix"))
        figures = rhythm.analyse(study, subdivisions=reading.subdivisions,
                                 threshold=reading.same_figure,
                                 use_loop=reading.use_loop, cohere=reading.cohere,
                                 look=look.name)
        pace = options.get("pace", "medium")
        plan = choreography.compose(study, pace=pace, width=width, height=height,
                                    fps=fps, max_seconds=wanted, rhythm_map=figures)

        built = plan.template

        # The look has the last word on the clips as well as on the figures. The piece shape
        # and the transitions are not part of a figure's treatment, so restricting them has
        # to happen here, on the finished edit, or a grids look would still slide open with
        # a round wipe somewhere in the middle.
        if look.shape:
            built.reveal_shape = look.shape
            for clip in built.clips:
                clip.reveal_shape = look.shape
        if look.transitions:
            for clip in built.clips:
                if clip.transition not in look.transitions:
                    clip.transition = look.transitions[0]

        built.audio_path = str(music)
        built.audio_name = music.stem
        sections = ", ".join(dict.fromkeys(s.label for s in study.sections))
        built.notes = list(built.notes) + [
            f"designed from {music.name}: {study.bpm:.0f} BPM in {study.meter}/4, "
            f"heard as {sections}",
            f"read as {reading.name}: {reading.note}",
            f"look {look.name}: {look.note}",
            f"{len(built.clips)} photo changes over {built.duration:.1f}s at "
            f"{pace} pace",
        ] + plan.notes
        # The reasoning is the only way to tell, after the fact, why a photo got the
        # treatment it did, so all of it is kept rather than the first few lines. A
        # complaint about the result is otherwise impossible to trace.
        built.notes.append("plan: " + " | ".join(plan.explain))

        # The full account of the track, written beside the render. Asked for directly, and
        # the only practical way to argue with a result: a video that feels wrong is hard to
        # reason about, a page describing what was heard is not.
        if plan.rhythm is not None:
            try:
                account = explain.describe(study, plan.rhythm, name=music.name,
                                           pace=pace)
                report = config.LOG_DIR / f"tune_{music.stem[:40]}.txt"
                report.write_text(account, encoding="utf-8")
                built.notes.append(f"analysis written to {report}")
                if plan.rhythm.looped:
                    built.notes.append(
                        f"this track loops every {plan.rhythm.loop_bars} bar(s), so one "
                        f"treatment per figure was used throughout rather than varying "
                        f"by section")
            except Exception as exc:
                # A failure to explain must never stop a render.
                built.notes.append(f"could not write the analysis: {type(exc).__name__}")
        return built

    # -- Drop It ---------------------------------------------------------

    def _apply_drop_it(self, template: Template, options: dict,
                       photo_count: int) -> Template:
        """Detect bass drops and inject burst montage clips at those moments.

        A bass drop is found by analysing low-frequency energy (< 150 Hz) in the
        audio. When a sudden spike exceeds 3× the running average, it is tagged as
        a drop. At each drop, the existing clip is replaced by a rapid-fire burst
        of 5 micro-clips (0.06s each) cycling through different photos, followed by
        a "slam" clip with Punch Cut animation at high intensity.
        """
        music = Path(options.get("music_path", ""))
        if not music.exists() or not template.clips:
            return template

        # Load audio and compute a low-frequency onset envelope for bass detection
        try:
            import librosa
            y, sr = librosa.load(str(music), sr=22050, mono=True,
                                 duration=template.duration + 2.0)
            # Isolate bass frequencies (< 150 Hz) using a short-time FFT
            stft = np.abs(librosa.stft(y, n_fft=2048, hop_length=512))
            freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
            bass_mask = freqs < 150.0
            bass_energy = stft[bass_mask, :].sum(axis=0)

            # Smooth with a running average, then find spikes > 3× the average
            window = max(1, int(sr / 512 * 0.5))  # ~0.5 second window
            if len(bass_energy) < window * 2:
                return template

            avg = np.convolve(bass_energy, np.ones(window) / window, mode='same')
            avg = np.maximum(avg, 1e-6)  # avoid division by zero
            ratio = bass_energy / avg

            hop_duration = 512 / sr
            drop_times = []
            min_gap = 2.0  # at least 2 seconds between drops
            for i in range(window, len(ratio)):
                t = i * hop_duration
                if ratio[i] > 3.0 and bass_energy[i] > np.percentile(bass_energy, 85):
                    if not drop_times or (t - drop_times[-1]) > min_gap:
                        drop_times.append(t)

            if not drop_times:
                template.notes = list(template.notes) + [
                    "Drop It: no bass drops detected in this track"]
                return template

            # Limit to top 4 strongest drops to avoid overdoing it
            if len(drop_times) > 4:
                # Keep the ones with highest energy
                energies = []
                for dt in drop_times:
                    idx = int(dt / hop_duration)
                    idx = min(idx, len(bass_energy) - 1)
                    energies.append(bass_energy[idx])
                ranked = sorted(zip(energies, drop_times), reverse=True)[:4]
                drop_times = sorted(t for _, t in ranked)

        except Exception as exc:
            template.notes = list(template.notes) + [
                f"Drop It: audio analysis failed ({type(exc).__name__})"]
            return template

        # For each drop time, find the clip that contains it and replace with burst
        from dataclasses import replace
        from .template import Clip
        new_clips = []
        drop_map = {}

        # Find which clip indices contain a drop
        for dt in drop_times:
            for i, clip in enumerate(template.clips):
                if clip.start <= dt < clip.start + clip.duration:
                    if i not in drop_map:
                        drop_map[i] = dt
                    break

        burst_dur = 0.06   # duration of each burst micro-clip
        burst_count = 5    # number of rapid photos in the burst
        slam_anim = "Punch In"  # hard zoom slam after burst

        idx_counter = 0
        drops_injected = 0

        for i, clip in enumerate(template.clips):
            if i not in drop_map:
                clip.index = idx_counter
                new_clips.append(clip)
                idx_counter += 1
                continue

            dt = drop_map[i]
            drops_injected += 1
            total_burst_time = burst_count * burst_dur  # 0.3s for burst
            clip_end = clip.start + clip.duration

            if clip.duration < total_burst_time + 0.1:
                # Clip too short for burst, just use it as-is with boosted intensity
                clip.index = idx_counter
                clip.animation = slam_anim
                clip.intensity = 2.0
                new_clips.append(clip)
                idx_counter += 1
                continue

            # Split clip so burst aligns with the actual bass drop at dt.
            # Burst starts at dt - total_burst_time (0.3s) so it builds up to the drop.
            burst_start = max(clip.start, min(dt - total_burst_time, clip_end - total_burst_time - 0.1))
            pre_dur = burst_start - clip.start

            # Keep the clip portion preceding the burst if it has significant duration
            if pre_dur >= 0.05:
                pre_clip = replace(
                    clip,
                    index=idx_counter,
                    duration=round(pre_dur, 4),
                    animation_duration=min(clip.animation_duration, pre_dur),
                )
                new_clips.append(pre_clip)
                idx_counter += 1
            else:
                burst_start = clip.start

            # Inject burst micro-clips: rapid-fire cycling through photos
            t = burst_start
            for b in range(burst_count):
                burst_slot = (clip.slot + b + 1) % max(1, photo_count)
                micro = Clip(
                    index=idx_counter,
                    slot=burst_slot,
                    start=round(t, 4),
                    duration=burst_dur,
                    animation="Cut",
                    animation_duration=burst_dur,
                    axis=clip.axis,
                    intensity=1.5,
                    cover_mode="zoom",
                )
                new_clips.append(micro)
                idx_counter += 1
                t += burst_dur

            # Slam clip: the hero photo with punch animation + high intensity
            remaining = clip_end - t
            slam = Clip(
                index=idx_counter,
                slot=clip.slot,
                start=round(t, 4),
                duration=round(remaining, 4),
                animation=slam_anim,
                animation_duration=min(remaining, 0.4),
                axis=clip.axis,
                intensity=2.0,
                cover_mode="zoom",
            )
            new_clips.append(slam)
            idx_counter += 1

        template.clips = new_clips
        template.notes = list(template.notes) + [
            f"Drop It: {drops_injected} bass drop(s) detected, "
            f"burst montage injected at {', '.join(f'{t:.1f}s' for t in drop_times)}"
        ]
        return template

    def _build_timing(self, job: store.Job, template: Template, options: dict,
                      photo_count: int) -> Template:
        """Cut a music-agnostic template to the track the user supplied."""
        music = Path(options.get("music_path", ""))
        if not music.exists():
            raise FileNotFoundError(
                "this template works with any music, so choose a music file")

        self.store.update(job.id, stage="listening to the music", progress=0.0)

        # A requested length of 0 means "the whole song", which is a real choice rather
        # than a missing value, so it has to be distinguished from absent explicitly.
        # Treating 0 as falsy here is what previously capped every render at 30 seconds.
        requested = options.get("max_seconds")
        audio_start = float(options.get("audio_start", 0.0))
        # 0.0 means full song. Default to 0.0 (full song) instead of capping at 30 seconds
        wanted = float(requested) if requested is not None else 0.0
        template.max_seconds = wanted

        # Every detected beat should trigger a template action.
        template.beats_per_clip = 1
        template.min_clip_duration = 0.05
        # Piece settings apply only to a style that reveals in pieces already. Applying
        # them to any style would turn them all into the same reveal edit.
        if template.reveal_tiles > 0:
            if options.get("reveal_tiles") is not None:
                template.reveal_tiles = max(2, int(options["reveal_tiles"]))
            shape = options.get("reveal_shape", "")
            if shape in REVEAL_SHAPES:
                template.reveal_shape = shape

        edited = options.get("beat_markers")
        if edited:
            # The user adjusted the markers by hand, so those win outright.
            onsets = sorted(float(t) for t in edited)
            strong = sorted(float(t) for t in (options.get("strong_markers") or onsets))
            duration = float(options.get("audio_duration") or 0.0)
            if duration <= 0:
                duration = max(onsets) + 2.0 if onsets else 30.0
            tempo = 0.0
            detected = len(onsets)
        else:
            # Analyse starting from audio_start to end (or to wanted duration)
            analysis = beats.analyse(
                music,
                duration=(wanted + 6.0) if wanted > 0.0 else None,
                start=audio_start,
                sensitivity=float(options.get("sensitivity", 1.0)),
                min_gap=max(0.05, template.min_clip_duration * 0.45),
                strong_ratio=float(options.get("strong_ratio", 0.4)),
            )
            onsets = analysis.onsets
            strong = analysis.strong_times()
            duration = analysis.duration
            tempo = analysis.tempo_bpm
            detected = len(onsets)

        built = template.with_beats(onsets, duration, strong=strong)
        built.audio_path = str(music)
        built.audio_name = music.stem
        built.audio_start = float(options.get("audio_start", 0.0))
        described = (f"{detected} beats" if not edited
                     else f"{detected} beats you set by hand")
        built.notes = list(built.notes) + [
            f"cut to {music.name}: {described}"
            + (f" at {tempo:.0f} BPM" if tempo else "")
            + f", {len(built.clips)} clips over {built.duration:.1f}s"
        ]
        return built

    # -- rendering -------------------------------------------------------

    def _run(self, job: store.Job) -> None:
        template, pictures, options = self._prepare(job)

        out_dir = Path(options.get("output_dir") or config.OUTPUT_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{job.name}_{job.id}.mp4"

        self.store.update(job.id, status=store.STATUS_RENDERING,
                          stage="rendering", progress=0.0, error="",
                          frame_total=template.frame_count)
        self._clear_cancel(job.id)

        def progress(current: int, total: int, message: str) -> None:
            now = time.monotonic()
            if now - self._last_progress_commit.get(job.id, 0.0) >= 1.0 or current == total:
                self.store.update(job.id, progress=current / max(1, total),
                                  frame_current=current, frame_total=total,
                                  stage=f"rendering frame {current} of {total}")
                self._last_progress_commit[job.id] = now

        headroom = renderer.required_headroom(template)
        photo_set = photo_mod.PhotoSet(pictures, (template.width, template.height),
                                       headroom)
        result = renderer.render(template, photo_set, target, progress=progress,
                                 scale=1.0,
                                 options=options,
                                 should_cancel=lambda: self._cancelled(job.id))

        if self._cancelled(job.id):
            if target.exists():
                target.unlink(missing_ok=True)
            self._clear_cancel(job.id)
            self.store.update(job.id, status=store.STATUS_CANCELLED,
                              stage="cancelled", progress=0.0)
            return

        self.store.update(
            job.id, status=store.STATUS_DONE, stage="finished", progress=1.0,
            result={
                "video": str(target),
                "folder": str(out_dir),
                "clips": len(template.clips),
                "photos": len(pictures),
                "duration": round(template.duration, 2),
                "frames": result.frames,
                "elapsed_seconds": round(result.elapsed_seconds, 1),
                "audio": result.audio_muxed,
                "size_mb": round(target.stat().st_size / (1024 * 1024), 1)
                if target.exists() else 0,
                "notes": template.notes[-1:],
                "warnings": result.warnings,
            },
        )


