"""
Video Builder — assembles final video using direct FFmpeg commands.

Default: 1920x1080 long-form landscape.
Shorts (1080x1920) also supported via format_type="short".

Image framing: scale-to-fit with blur-padded letterbox/pillarbox
so every image is fully visible — no cropping, no black bars.
Zoompan adds a slow ken-burns zoom on each image.
Captions are burned in from an ASS file.
"""

import json
import os
import subprocess
import tempfile


def _run(cmd: list, label: str):
    print(f"[FFmpeg] {label}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[FFmpeg] STDERR (last 3000 chars):\n{result.stderr[-3000:]}")
        raise RuntimeError(f"FFmpeg failed at: {label}")


def _get_audio_duration(mp3_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", mp3_path],
        capture_output=True, text=True,
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def _scale_pad_vf(W: int, H: int, fps: int, n_frames: int) -> str:
    """
    Build a video filter that:
    1. Scales image to fit within WxH keeping aspect ratio
    2. Pads with a blurred/darkened copy of the image (no black bars)
    3. Applies a slow ken-burns zoom
    """
    # Background: scale up, blur, darken
    bg = (
        f"scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},"
        f"boxblur=20:5,"
        f"eq=brightness=-0.3"
    )
    # Foreground: scale to fit
    fg = f"scale={W}:{H}:force_original_aspect_ratio=decrease,setsar=1"

    # Zoompan on foreground
    zoom_expr = "min(zoom+0.0003,1.4)"
    zp = (
        f"zoompan=z='{zoom_expr}':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={n_frames}:s={W}x{H}:fps={fps}"
    )

    # Overlay fg centered on bg
    vf = (
        f"[0:v]split=2[bg][fg];"
        f"[bg]{bg}[bg_out];"
        f"[fg]{fg}[fg_scaled];"
        f"[fg_scaled]{zp}[fg_zoom];"
        f"[bg_out][fg_zoom]overlay=(W-w)/2:(H-h)/2,setsar=1"
    )
    return vf


def build_video(
    image_paths: list,
    mp3_path: str,
    ass_path: str,
    output_path: str,
    format_type: str = "long",
) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    W, H = (1080, 1920) if format_type == "short" else (1920, 1080)
    fps = 25

    total_duration = _get_audio_duration(mp3_path)
    n_images = len(image_paths)
    img_duration = total_duration / n_images
    n_frames = max(int(img_duration * fps), 1)

    print(f"[VB] {format_type.upper()} {W}x{H} | {total_duration:.1f}s | {n_images} clips × {img_duration:.1f}s")

    clip_paths = []
    with tempfile.TemporaryDirectory() as tmpdir:

        # ── Step 1: render each image as a zoompan clip ──────────────────────
        for i, img_path in enumerate(image_paths):
            clip_out = os.path.join(tmpdir, f"clip_{i:04d}.mp4")
            vf = _scale_pad_vf(W, H, fps, n_frames)
            _run([
                "ffmpeg", "-y",
                "-loop", "1", "-i", img_path,
                "-filter_complex", vf,
                "-t", str(img_duration),
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-pix_fmt", "yuv420p",
                "-an",
                clip_out,
            ], f"clip {i+1}/{n_images}: {os.path.basename(img_path)}")
            clip_paths.append(clip_out)

        # ── Step 2: concat all clips ─────────────────────────────────────────
        concat_list = os.path.join(tmpdir, "concat.txt")
        with open(concat_list, "w") as f:
            for cp in clip_paths:
                f.write(f"file '{cp}'\n")

        concat_out = os.path.join(tmpdir, "concat.mp4")
        _run([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_list,
            "-c", "copy",
            concat_out,
        ], "concat clips")

        # ── Step 3: burn captions + merge audio ─────────────────────────────
        _run([
            "ffmpeg", "-y",
            "-i", concat_out,
            "-i", mp3_path,
            "-vf", f"ass={ass_path}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "21",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            output_path,
        ], "burn captions + merge audio → final video")

    print(f"[VB] ✅ Done → {output_path}")
    return output_path
