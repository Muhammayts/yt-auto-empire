"""
Video Builder — professional quality FFmpeg pipeline.

Quality features:
  - Smooth 0.6s dissolve/crossfade transitions between every clip
  - Blur-padded letterbox framing (images fully visible, no black bars)
  - Ken-burns slow zoom on each image
  - Color grading: slight contrast + saturation boost per clip
  - Fade-in on first clip, fade-out on last clip
  - ASS captions burned in (news lower-third style)
  - High-quality h264 encoding (crf 18)

Formats: 1920x1080 long-form (default) | 1080x1920 Shorts
"""

import json
import os
import subprocess
import tempfile


TRANSITION_DURATION = 0.5   # seconds of crossfade between clips
FADE_DURATION       = 0.3   # fade in/out at very start and end


def _run(cmd: list, label: str):
    print(f"[FFmpeg] {label}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[FFmpeg] STDERR:\n{result.stderr[-3000:]}")
        raise RuntimeError(f"FFmpeg failed: {label}")


def _get_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
        capture_output=True, text=True,
    )
    return float(json.loads(r.stdout)["format"]["duration"])


def _render_clip(img_path: str, duration: float, W: int, H: int,
                 fps: int, out_path: str, clip_idx: int, total_clips: int):
    """
    Render one image as a video clip.
    Fast pipeline (no zoompan) — fits within GitHub Actions 60-min limit
    even with 40+ clips:
      - blur-padded letterbox background
      - colour grading
      - fade-in on first / fade-out on last clip
    """
    # ── Background: scale-fill + strong blur + darken ────────────────────────
    bg_vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},"
        f"boxblur=luma_radius=20:luma_power=2,"
        f"eq=brightness=-0.3:saturation=0.35"
    )

    # ── Foreground: fit inside frame (letterbox/pillarbox) ───────────────────
    fg_vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black@0,"
        f"setsar=1"
    )

    # ── Colour grading ────────────────────────────────────────────────────────
    grade = "eq=contrast=1.10:saturation=1.15:brightness=0.01"

    # ── Fade in / out ─────────────────────────────────────────────────────────
    fade = ""
    if clip_idx == 0:
        fade = f",fade=t=in:st=0:d={FADE_DURATION}"
    if clip_idx == total_clips - 1:
        fade += f",fade=t=out:st={max(duration - FADE_DURATION, 0):.3f}:d={FADE_DURATION}"

    fc = (
        f"[0:v]split=2[raw_bg][raw_fg];"
        f"[raw_bg]{bg_vf}[bg];"
        f"[raw_fg]{fg_vf}[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,"
        f"{grade}{fade}[out]"
    )

    _run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", img_path,
        "-filter_complex", fc,
        "-map", "[out]",
        "-t", str(duration + TRANSITION_DURATION),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-an",
        out_path,
    ], f"clip {clip_idx+1}/{total_clips}")


def _build_xfade_chain(clip_paths: list, durations: list,
                        concat_out: str, W: int, H: int):
    """
    Chain all clips with xfade dissolve transitions.
    For N clips builds: [0][1]xfade→[x01]; [x01][2]xfade→[x02]; ...
    """
    n = len(clip_paths)
    if n == 1:
        # Single clip — just copy
        _run(["ffmpeg", "-y", "-i", clip_paths[0], "-c", "copy", concat_out],
             "single clip passthrough")
        return

    # Build inputs
    inputs = []
    for p in clip_paths:
        inputs += ["-i", p]

    # Build filter_complex
    fc_parts = []
    offset = 0.0
    prev_label = "[0:v]"

    for i in range(1, n):
        offset += durations[i-1] - TRANSITION_DURATION
        offset = max(offset, 0.01)
        out_label = f"[x{i:03d}]" if i < n - 1 else "[vout]"
        fc_parts.append(
            f"{prev_label}[{i}:v]xfade=transition=fade"
            f":duration={TRANSITION_DURATION}:offset={offset:.3f}{out_label}"
        )
        prev_label = out_label

    fc = ";".join(fc_parts)

    _run([
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", fc,
        "-map", "[vout]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p",
        concat_out,
    ], f"xfade chain ({n} clips)")


def build_video(
    image_paths: list,
    mp3_path: str,
    ass_path: str,
    output_path: str,
    format_type: str = "long",
) -> str:

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    W, H = (1080, 1920) if format_type == "short" else (1920, 1080)
    fps  = 25

    total_audio = _get_duration(mp3_path)
    n           = len(image_paths)
    img_dur     = total_audio / n

    print(f"[VB] {format_type.upper()} {W}×{H} | {total_audio:.1f}s | "
          f"{n} clips × {img_dur:.1f}s | transitions: {TRANSITION_DURATION}s dissolve")

    with tempfile.TemporaryDirectory() as tmpdir:

        # ── Step 1: render each clip ─────────────────────────────────────────
        clip_paths = []
        durations  = []
        for i, img in enumerate(image_paths):
            out = os.path.join(tmpdir, f"clip_{i:04d}.mp4")
            _render_clip(img, img_dur, W, H, fps, out, i, n)
            clip_paths.append(out)
            durations.append(img_dur)

        # ── Step 2: xfade all clips together ─────────────────────────────────
        xfade_out = os.path.join(tmpdir, "xfade.mp4")
        _build_xfade_chain(clip_paths, durations, xfade_out, W, H)

        # ── Step 3: burn captions + merge audio ──────────────────────────────
        _run([
            "ffmpeg", "-y",
            "-i", xfade_out,
            "-i", mp3_path,
            "-vf", f"ass={ass_path}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            output_path,
        ], "burn captions + merge audio → final")

    size_mb = os.path.getsize(output_path) / 1024 / 1024
    print(f"[VB] ✅ Done → {output_path}  ({size_mb:.1f} MB)")
    return output_path
