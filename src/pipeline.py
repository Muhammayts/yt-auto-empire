"""
Master Pipeline Runner.
Reads jobs/current_job.json and orchestrates:
  TTS → Image Fetch → Video Build → YouTube Upload
"""

import json
import os
import sys
import datetime

# Ensure src/ directory is on the path regardless of how this script is invoked
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tts_engine import generate_tts
from image_fetcher import fetch_images
from video_builder import build_video
from youtube_uploader import upload_video

JOB_FILE = os.environ.get("JOB_FILE", "jobs/current_job.json")
OUTPUT_DIR = "output"


def load_job(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        job = json.load(f)
    required = ["title", "script", "search_query"]
    for key in required:
        if key not in job:
            raise ValueError(f"Job missing required field: '{key}'")
    return job


def run():
    print(f"\n{'='*60}")
    print(f"  YouTube Auto Pipeline — {datetime.datetime.utcnow().isoformat()}Z")
    print(f"{'='*60}\n")

    # 1. Load job
    print(f"[Pipeline] Loading job: {JOB_FILE}")
    job = load_job(JOB_FILE)

    title        = job["title"]
    script       = job["script"]
    search_query = job["search_query"]
    lang         = job.get("lang", "en")               # "en" or "ru"
    format_type  = job.get("format", "short")           # "short" or "long"
    tags         = job.get("tags", ["news", "breaking"])
    description  = job.get("description", title)
    category_id  = job.get("category_id", "25")

    # Determine image count based on format
    if format_type == "long":
        n_images = max(10, len(script.split(".")) * 2)   # ~2 images per sentence
        n_images = min(n_images, 80)
    else:
        n_images = 5   # Shorts: 5 images, ~12s each

    print(f"[Pipeline] Title:   {title}")
    print(f"[Pipeline] Lang:    {lang}  |  Format: {format_type}  |  Images: {n_images}")

    # 2. Generate TTS + captions
    tts_result = generate_tts(script, lang=lang, output_dir=OUTPUT_DIR)

    # 3. Fetch images
    img_dir = os.path.join(OUTPUT_DIR, "images")
    size_hint = "portrait" if format_type == "short" else "landscape"
    images = fetch_images(search_query, count=n_images, output_dir=img_dir, size_hint=size_hint)

    # 4. Build video
    video_path = os.path.join(OUTPUT_DIR, "final_video.mp4")
    build_video(
        image_paths=images,
        mp3_path=tts_result["mp3"],
        ass_path=tts_result["ass"],
        output_path=video_path,
        format_type=format_type,
    )

    # 5. Upload to YouTube
    video_id = upload_video(
        video_path=video_path,
        title=title,
        description=description,
        tags=tags,
        category_id=category_id,
        privacy_status="private",
    )

    print(f"\n[Pipeline] 🎉 ALL DONE!")
    print(f"[Pipeline] Video ID : {video_id}")
    print(f"[Pipeline] Studio   : https://studio.youtube.com/video/{video_id}/edit")
    print(f"[Pipeline] ⚠️  Video is PRIVATE — publish manually in YouTube Studio.\n")

    # Save result summary
    result_path = os.path.join(OUTPUT_DIR, "result.json")
    with open(result_path, "w") as f:
        json.dump({
            "video_id": video_id,
            "title": title,
            "studio_url": f"https://studio.youtube.com/video/{video_id}/edit",
            "privacy": "private",
        }, f, indent=2)
    print(f"[Pipeline] Result saved → {result_path}")


if __name__ == "__main__":
    run()
