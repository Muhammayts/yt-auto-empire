"""
Master Pipeline Runner.
Reads jobs/current_job.json and orchestrates:
  TTS → Scene-aware Image Fetch → Video Build → YouTube Upload
"""

import json
import os
import sys
import datetime

# Ensure src/ is on path regardless of invocation directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tts_engine import generate_tts
from image_fetcher import fetch_images_for_scenes, fetch_images
from video_builder import build_video
from youtube_uploader import upload_video, upload_thumbnail
from thumbnail_generator import generate_thumbnail

JOB_FILE    = os.environ.get("JOB_FILE", "jobs/current_job.json")
OUTPUT_DIR  = "output"
AUTO_NEWS   = os.environ.get("AUTO_NEWS", "false").lower() == "true"
NEWS_LANG   = os.environ.get("NEWS_LANG", "ja")


def load_job(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        job = json.load(f)
    for key in ["title", "script", "search_query"]:
        if key not in job:
            raise ValueError(f"Job missing required field: '{key}'")
    return job


def run():
    print(f"\n{'='*60}")
    print(f"  SugoidesJapan Pipeline — {datetime.datetime.utcnow().isoformat()}Z")
    print(f"{'='*60}\n")

    # Auto-fetch latest news if AUTO_NEWS=true
    if AUTO_NEWS:
        print(f"[Pipeline] AUTO_NEWS=true — fetching latest trending Japan content...")
        from news_fetcher import generate_job
        generate_job(lang=NEWS_LANG, output_path=JOB_FILE)

    print(f"[Pipeline] Loading job: {JOB_FILE}")
    job = load_job(JOB_FILE)

    title        = job["title"]
    script       = job["script"]
    search_query = job["search_query"]
    lang         = job.get("lang", "ja")            # default Japanese
    format_type  = job.get("format", "long")       # default long-form
    tags         = job.get("tags", ["Japan", "viral", "SugoidesJapan", "trending"])
    description  = job.get("description", title)
    category_id  = job.get("category_id", "24")   # 24 = Entertainment

    print(f"[Pipeline] Title  : {title}")
    print(f"[Pipeline] Lang   : {lang}  |  Format: {format_type}")

    # ── 1. TTS + captions ──────────────────────────────────────────────────
    tts = generate_tts(script, lang=lang, output_dir=OUTPUT_DIR)

    # ── 2. Scene-aware image fetch ─────────────────────────────────────────
    img_dir = os.path.join(OUTPUT_DIR, "images")

    if format_type == "long":
        # 1 image per scene — avoids timeout on GitHub Actions free runners
        images = fetch_images_for_scenes(
            script=script,
            fallback_query=search_query,
            output_dir=img_dir,
            images_per_scene=1,
        )
    else:
        # Shorts: simple 5-image fetch
        images = fetch_images(search_query, count=5, output_dir=img_dir, size_hint="portrait")

    print(f"[Pipeline] Images collected: {len(images)}")

    # ── 3. Build video ─────────────────────────────────────────────────────
    video_path = os.path.join(OUTPUT_DIR, "final_video.mp4")
    build_video(
        image_paths=images,
        mp3_path=tts["mp3"],
        ass_path=tts["ass"],
        output_path=video_path,
        format_type=format_type,
    )

    # ── 4. Generate thumbnail ──────────────────────────────────────────────
    print(f"[Pipeline] Generating thumbnail...")
    thumb_path = generate_thumbnail(
        title=title,
        search_query=search_query,
        output_dir=OUTPUT_DIR,
    )

    # ── 5. Upload to YouTube ───────────────────────────────────────────────
    video_id = upload_video(
        video_path=video_path,
        title=title,
        description=description,
        tags=tags,
        category_id=category_id,
        privacy_status="private",
    )

    # ── 6. Upload thumbnail ────────────────────────────────────────────────
    upload_thumbnail(video_id, thumb_path)

    print(f"\n[Pipeline] 🎉 DONE!")
    print(f"[Pipeline] Video ID : {video_id}")
    print(f"[Pipeline] Studio   : https://studio.youtube.com/video/{video_id}/edit")
    print(f"[Pipeline] ⚠️  Video is PRIVATE — publish manually in YouTube Studio.\n")

    with open(os.path.join(OUTPUT_DIR, "result.json"), "w") as f:
        json.dump({
            "video_id": video_id,
            "title": title,
            "studio_url": f"https://studio.youtube.com/video/{video_id}/edit",
            "thumbnail": thumb_path,
            "privacy": "public",
        }, f, indent=2)


if __name__ == "__main__":
    run()
