"""
Video Builder — assembles final video using direct FFmpeg commands.
Supports:
  - 1080x1920  Shorts  (60-second, portrait)
  - 1920x1080  Long    (up to 10 minutes, landscape)
Burns in ASS captions and merges voiceover.
Uses zoompan filter — fast, no MoviePy dependency.
"""

import json
import math
import os
import subprocess
import tempfile


def _run(cmd: list, label: str):
    print(f"[FFmpeg] {label}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[FFmpeg] STDERR:\n{result.stderr[-2000:]}")
        raise RuntimeError(f"FFmpeg failed at: {label}")


def _get_audio_duration(mp3_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", mp3_path],
        capture_output=True, text=True
    )
    info = json.loads(result.stdout)
    return float(info["format"]["duration"])


def build_video(
    image_paths: list,
    mp3_path: str,
    ass_path: str,
    output_path: str,
    format_type: str = "short",  # "short" | "long"
) -> str:
    """
    Build final video from images + audio + captions.
    Returns path to output video file.
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    if format_type == "short":
        W, H = 1080, 1920
    else:
        W, H = 1920, 1080

    total_duration = _get_audio_duration(mp3_path)
    n_images = len(image_paths)
    img_duration = total_duration / n_images

    print(f"[VB] Format={format_type} ({W}x{H}), duration={total_duration:.1f}s, images={n_images}")

    # Step 1: render each image as a zoompan clip
    clip_paths = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for i, img_path in enumerate(image_paths):
            clip_out = os.path.join(tmpdir, f"clip_{i:03d}.mp4")
            d = img_duration
            fps = 25
            n_frames = int(d * fps)
            # zoompan: slow zoom-in effect
            zoom_expr = "min(zoom+0.0004,1.5)"
            vf = (
                f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase,"
                f"crop={W*2}:{H*2},"
                f"zoompan=z='{zoom_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                f":d={n_frames}:s={W}x{H}:fps={fps},"
                f"setsar=1"
            )
            _run([
                "ffmpeg", "-y",
                "-loop", "1", "-i", img_path,
                "-vf", vf,
                "-t", str(d),
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-an",
                clip_out
            ], f"zoompan clip {i+1}/{n_images}")
            clip_paths.append(clip_out)

        # Step 2: concatenate clips
        concat_list = os.path.join(tmpdir, "concat.txt")
        with open(concat_list, "w") as f:
            for cp in clip_paths:
                f.write(f"file '{cp}'\n")

        concat_out = os.path.join(tmpdir, "concat.mp4")
        _run([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_list,
            "-c", "copy",
            concat_out
        ], "concat clips")

        # Step 3: burn captions + merge audio
        _run([
            "ffmpeg", "-y",
            "-i", concat_out,
            "-i", mp3_path,
            "-vf", f"ass={ass_path}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            output_path
        ], "burn captions + merge audio")

    print(f"[VB] ✅ Video ready → {output_path}")
    return output_path


if __name__ == "__main__":
    # Quick test (needs test assets)
    build_video(
        image_paths=["test.jpg"],
        mp3_path="output/voiceover.mp3",
        ass_path="output/captions.ass",
        output_path="output/test_video.mp4",
        format_type="short",
    )
