"""End-to-end check of the pieces the app depends on.

Exercises the real code on the real template and music, but only a few frames, so it
finishes quickly and still proves every stage connects.

    tools\\run.bat tools\\smoke_test.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatcanvas import animations, beats, capcut, config, photos as photo_mod  # noqa: E402
from beatcanvas import renderer, store  # noqa: E402
from beatcanvas.easing import ease, ease_velocity  # noqa: E402
from beatcanvas.template import FILL_MODES, MIN_PHOTOS, Template, list_templates  # noqa: E402

SCRATCH = config.ROOT / "_smoke"
failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def main() -> int:
    print("1. easing (ported from CapCut Transform.lua)")
    check("ease(0) == 0", abs(ease((0.21, 0.93, 0.48, 0.97), 0.0)) < 1e-9)
    check("ease(1) == 1", abs(ease((0.21, 0.93, 0.48, 0.97), 1.0) - 1.0) < 1e-9)
    mid = ease((0.21, 0.93, 0.48, 0.97), 0.5)
    check("ease-out curve is ahead of linear at the midpoint", mid > 0.5,
          f"{mid:.4f}")
    slow = ease((0.73, 0.02, 0.86, 0.1), 0.5)
    check("ease-in curve is behind linear at the midpoint", slow < 0.5, f"{slow:.4f}")
    check("linear easing is the identity",
          abs(ease((0.0, 0.0, 1.0, 1.0), 0.37) - 0.37) < 1e-6)
    monotonic = all(
        ease((0.21, 0.93, 0.48, 0.97), i / 50)
        <= ease((0.21, 0.93, 0.48, 0.97), (i + 1) / 50) + 1e-9
        for i in range(50)
    )
    check("easing never goes backwards", monotonic)
    check("velocity is highest early on an ease-out",
          ease_velocity((0.21, 0.93, 0.48, 0.97), 0.05)
          > ease_velocity((0.21, 0.93, 0.48, 0.97), 0.95))

    print("\n2. animation library")
    pendulum = animations.get("Pendulum 1")
    check("Pendulum 1 is present", pendulum.name == "Pendulum 1")
    check("mangled names still resolve",
          animations.get(" pendulum1 ").name == "Pendulum 1")
    check("unknown names fall back to static",
          animations.get("Nope").name == "Static")
    check("has 4 actions", len(pendulum.actions) == 4,
          ", ".join(a.kind for a in pendulum.actions))

    up = animations.evaluate(pendulum, 0.0, 0.4, 0.5625, axis="-y")
    down = animations.evaluate(pendulum, 0.0, 0.4, 0.5625, axis="y")
    check("'-y' starts below centre (rises)", up.offset_y > 0, f"{up.offset_y:.3f}")
    check("'y' starts above centre (falls)", down.offset_y < 0, f"{down.offset_y:.3f}")
    check("the two directions are opposite",
          abs(up.offset_y + down.offset_y) < 1e-6)
    settled = animations.evaluate(pendulum, 0.5, 0.4, 0.5625, axis="-y")
    check("moves toward centre by mid-animation",
          abs(settled.offset_y) < abs(up.offset_y),
          f"{up.offset_y:.3f} -> {settled.offset_y:.3f}")
    check("blur fades as it settles",
          settled.blur_pixels < animations.evaluate(
              pendulum, 0.05, 0.4, 0.5625, axis="-y").blur_pixels)

    print("\n3. CapCut import")
    drafts = capcut.find_drafts()
    check("found CapCut drafts", len(drafts) > 0, f"{len(drafts)}")
    target = next((d for d in drafts if "0817" in d.name and "(1)" in d.name),
                  drafts[0] if drafts else None)
    if target:
        template = capcut.import_draft(target)
        check("import produced clips", len(template.clips) > 0,
              f"{len(template.clips)} clips")
        check("canvas is portrait after undoing the rotate hack",
              template.height > template.width, f"{template.width}x{template.height}")
        check("audio path recovered", bool(template.audio_path),
              Path(template.audio_path).name)
        filled = capcut.fill_animation(template, "Pendulum 1")
        check("fill_animation applied to the unanimated clips", filled > 0, str(filled))

    print("\n4. templates on disk")
    available = list_templates()
    check("at least one template saved", len(available) > 0, f"{len(available)}")
    if not available:
        return 1

    loaded = [Template.load(p) for p in available]
    fixed = [t for t in loaded if t.music_mode == "fixed" and t.clips]
    flexible = [t for t in loaded if t.music_mode == "any"]
    check("has fixed templates with baked timing", len(fixed) >= 1, f"{len(fixed)}")
    check("has any-music templates", len(flexible) >= 3, f"{len(flexible)}")
    check("any-music templates carry no clips",
          all(not t.clips for t in flexible))
    check("any-music templates carry no bundled music",
          all(not t.audio_path for t in flexible))
    for t in loaded:
        print(f"        [{t.music_mode:5s}] {t.name}")

    if not fixed:
        print("\nskipping the clip-based checks: no fixed template available")
        return 1 if failures else 0
    # The clip-based checks need baked timing, so they use a fixed template.
    template = fixed[0]

    print("\n5. fill modes")
    check("repeat keeps every clip",
          len(template.adapt(2, "repeat").clips) == len(template.clips))
    trimmed = template.adapt(2, "trim")
    check("trim cuts the render down", len(trimmed.clips) < len(template.clips),
          f"{len(template.clips)} -> {len(trimmed.clips)} clips, "
          f"{trimmed.duration:.2f}s")
    check("trim renumbers slots from zero",
          sorted({c.slot for c in trimmed.clips}) == list(
              range(len({c.slot for c in trimmed.clips}))))
    try:
        template.adapt(1, "repeat")
        check(f"fewer than {MIN_PHOTOS} photos is rejected", False)
    except ValueError:
        check(f"fewer than {MIN_PHOTOS} photos is rejected", True)
    check("plan_for describes both modes",
          all(template.plan_for(3, mode) for mode in FILL_MODES))

    print("\n6. beat detection")
    audio = Path(template.audio_path) if template.audio_path else Path()
    if audio.exists():
        analysis = beats.analyse(audio, duration=14.0)
        check("onsets detected", len(analysis.onsets) > 5, f"{len(analysis.onsets)}")
        check("tempo is plausible", 50 < analysis.tempo_bpm < 220,
              f"{analysis.tempo_bpm:.1f} BPM")
        check("onsets are in ascending order",
              all(b > a for a, b in zip(analysis.onsets, analysis.onsets[1:])))
        snapped = beats.snap([0.40, 5.20], analysis.onsets)
        check("snap moves a near cut onto a hit", snapped != [0.40, 5.20],
              str([round(s, 3) for s in snapped]))
        far = beats.snap([999.0], analysis.onsets)
        check("snap leaves a far cut alone", far == [999.0])
        durations = beats.cuts_from_onsets(analysis.onsets, min_duration=0.18)
        check("cuts_from_onsets respects the minimum",
              all(d >= 0.18 - 1e-6 for d in durations), f"{len(durations)} clips")
    else:
        check("audio present for beat detection", False,
              f"template audio missing: {template.audio_path or '(none)'}")

    print("\n6b. generating timing for an any-music template")
    if flexible and audio.exists():
        analysis = beats.analyse(audio, duration=20.0)
        built = flexible[0].with_beats(analysis.onsets, analysis.duration)
        check("clips generated from arbitrary music", len(built.clips) >= 4,
              f"{len(built.clips)} clips over {built.duration:.1f}s")
        check("generated clips are contiguous",
              all(abs(built.clips[i].end - built.clips[i + 1].start) < 1e-3
                  for i in range(len(built.clips) - 1)))
        off = sum(
            1 for clip in built.clips[1:]
            if abs(clip.start - min(analysis.onsets,
                                    key=lambda t: abs(t - clip.start))) > 0.03
        )
        check("generated cuts land on detected beats", off == 0,
              f"{off} off the beat")
        check("an empty beat list still produces a usable timeline",
              len(flexible[0].with_beats([], 12.0).clips) >= 2)

    print("\n7. render")
    SCRATCH.mkdir(parents=True, exist_ok=True)
    cards = photo_mod.collect(config.ROOT / "_placeholders") \
        if (config.ROOT / "_placeholders").exists() else []
    if len(cards) < MIN_PHOTOS:
        check("placeholder cards available", False,
              "run tools\\make_placeholders.py first")
    else:
        short = Template.from_dict(template.to_dict())
        short.clips = short.clips[:3]

        # Headroom is now about load resolution, not framing. Mirror mode fills gaps by
        # reflecting, so it needs barely any oversizing and therefore barely any crop.
        # Zoom mode scales up during the bounce, so it must be loaded larger.
        for mode, expect_low, expect_high in (("mirror", 1.0, 1.25),
                                              ("zoom", 1.5, 2.2)):
            probe = Template.from_dict(short.to_dict())
            for clip in probe.clips:
                clip.cover_mode = mode
            value = renderer.required_headroom(probe)
            check(f"{mode} load headroom is in range",
                  expect_low <= value <= expect_high, f"{value:.2f}x")

        headroom = renderer.required_headroom(short)

        # render() makes its own scaled copy internally, so it takes the full-size
        # template. render_frame() does not, so it needs one already at output size.
        scale = 0.25
        width = int(round(short.width * scale))
        height = int(round(short.height * scale))
        photo_set = photo_mod.PhotoSet(cards, (width, height), headroom)
        out = SCRATCH / "smoke.mp4"
        result = renderer.render(short, photo_set, out, scale=scale)
        check("video written", out.exists() and out.stat().st_size > 1000,
              f"{out.stat().st_size // 1024}KB" if out.exists() else "missing")
        check("all frames rendered", result.frames == short.frame_count,
              f"{result.frames}/{short.frame_count}")
        check("audio muxed", result.audio_muxed)
        print(f"        {result.seconds_per_frame * 1000:.0f} ms/frame at "
              f"{width}x{height}")

        scaled = Template.from_dict(short.to_dict())
        scaled.width, scaled.height = width, height
        frame = renderer.render_frame(scaled, photo_set, scaled.clips[0].start + 0.01)
        check("frame is the expected size", frame.shape[:2] == (height, width),
              f"{frame.shape} vs ({height}, {width}, 3)")
        check("frame is not blank", int(frame.max()) > 30, f"max {int(frame.max())}")
        # The real requirement: whichever mode is in use, and at whatever point in the
        # bounce, no black gap appears at the frame edge. Checked at the extremes of the
        # swing, which is where a gap would show if the maths were wrong.
        for mode in animations.COVER_MODES:
            probe = Template.from_dict(short.to_dict())
            probe.width, probe.height = width, height
            for clip in probe.clips:
                clip.cover_mode = mode
            probe_photos = photo_mod.PhotoSet(
                cards, (width, height), renderer.required_headroom(probe))
            worst = 0.0
            clip = probe.clips[1] if len(probe.clips) > 1 else probe.clips[0]
            for fraction in (0.0, 0.02, 0.25, 0.5, 0.75, 0.98, 0.999):
                shot = renderer.render_frame(
                    probe, probe_photos,
                    clip.start + clip.duration * fraction)
                worst = max(worst, float((shot.max(axis=2) < 8).mean()))
            check(f"{mode} leaves no black gap across the whole bounce",
                  worst < 0.005, f"worst {worst * 100:.2f}% near-black")

    print("\n8. app and job store")
    try:
        from beatcanvas.app import app
        routes = {getattr(r, "path", "") for r in app.routes}
        check("FastAPI app imports", True)
        for expected in ("/", "/api/jobs", "/api/photos", "/api/music", "/thumb",
                         "/api/reveal", "/api/health"):
            check(f"route {expected}", expected in routes)
        # The single-page rework should have removed the per-job and per-template pages.
        for gone in ("/job/{job_id}", "/template", "/file"):
            check(f"route {gone} removed", gone not in routes)
    except Exception as exc:
        check("FastAPI app imports", False, str(exc))

    db = SCRATCH / "jobs.db"
    db.unlink(missing_ok=True)
    js = store.JobStore(db)
    job = js.create("smoke", str(available[0]), {"fill": "repeat", "approved": False})
    check("job created", job.id > 0)
    js.update(job.id, status=store.STATUS_AWAITING, progress=0.5)
    again = js.get(job.id)
    check("job round-trips", again is not None
          and again.status == store.STATUS_AWAITING
          and abs(again.progress - 0.5) < 1e-9)
    check("awaiting jobs are not picked up by the worker", js.next_queued() is None)
    js.close()

    shutil.rmtree(SCRATCH, ignore_errors=True)

    print("\n" + "=" * 62)
    if failures:
        print(f"{len(failures)} check(s) FAILED:")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
