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
        self._executor = ThreadPoolExecutor(max_workers=4)
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
                    can_start = self._active_count < 4
                while can_start:
                    job = self.store.next_queued()
                    if not job:
                        break
                    with self._active_lock:
                        self._active_count += 1
                        can_start = self._active_count < 4
                    self._executor.submit(self._process_job, job)
            except Exception:
                traceback.print_exc()
                time.sleep(2)
                continue

    def _process_job(self, job: store.Job) -> None:
        try:
            self._run(job)
        except Exception as exc:
            err = traceback.format_exc(limit=8)
            print(f"Job {job.id} failed:\n{err}")
            self.store.update(job.id, status=store.STATUS_FAILED, stage="failed", error=err)
        finally:
            import shutil
            dropped_folder = Path(job.options.get("photo_folder", "")).parent
            if dropped_folder.exists() and "_dropped" in str(dropped_folder):
                shutil.rmtree(dropped_folder, ignore_errors=True)
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

        if auto:
            pictures = self._photos_for(options)
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

            pictures = self._photos_for(options)

        if not auto and template.music_mode == "any":
            template = self._build_timing(job, template, options, len(pictures))
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
        wanted = (float(requested) if requested is not None
                  else float(template.max_seconds))
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
            # Analyse a little past the intended length so the last clip has a boundary.
            analysis = beats.analyse(
                music,
                duration=(wanted + 6.0) if wanted else None,
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
        target = out_dir / f"{job.name}.mp4"

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

