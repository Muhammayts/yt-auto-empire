"""
Image Fetcher — scene-aware image downloader.

For long-form videos, splits the script into scenes and fetches
2 images per scene using the actual sentence text as the search query.
This gives far more relevant, dynamic visuals than a single query.

Falls back to a news-card generator so the pipeline never crashes.
"""

import hashlib
import os
import re
import time
import requests
from PIL import Image, ImageDraw, ImageFont
from duckduckgo_search import DDGS

HEADERS = {"User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36"}
TIMEOUT = 14
MIN_WIDTH = 600
IMAGES_PER_SCENE = 2


# ── Scene splitting ───────────────────────────────────────────────────────────

def split_into_scenes(script: str) -> list:
    """
    Split script into meaningful scenes (1–2 sentences each).
    Returns list of scene strings.
    """
    # Split on sentence boundaries
    sentences = re.split(r'(?<=[.!?])\s+', script.strip())
    sentences = [s.strip() for s in sentences if len(s.strip()) > 20]

    # Group every 2 sentences into one scene
    scenes = []
    for i in range(0, len(sentences), 2):
        scene = " ".join(sentences[i:i+2])
        scenes.append(scene)
    return scenes if scenes else [script[:200]]


def scene_to_query(scene: str) -> str:
    """
    Extract the best 4–5 word image search query from a scene sentence.
    Strips common filler words, keeps nouns and proper nouns.
    """
    stopwords = {
        "the","a","an","is","are","was","were","has","have","had",
        "will","would","could","should","may","might","their","there",
        "this","that","these","those","and","but","or","for","with",
        "from","into","onto","about","after","before","during","while",
        "also","just","now","today","new","latest","breaking","says",
        "said","say","amid","over","under","more","less","very","much",
        # Russian stopwords transliterated
        "на","в","и","с","по","к","из","за","о","от","до",
    }
    words = re.findall(r'\b[A-Za-zА-Яа-яЁё]{3,}\b', scene)
    filtered = [w for w in words if w.lower() not in stopwords][:6]
    return " ".join(filtered) if filtered else scene[:60]


# ── Downloader ────────────────────────────────────────────────────────────────

def _download(url: str, dest: str) -> bool:
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True)
            if r.status_code == 200:
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
                img = Image.open(dest)
                ok = img.width >= MIN_WIDTH
                img.close()
                if ok:
                    return True
                os.remove(dest)
        except Exception as e:
            print(f"[IMG] Download attempt {attempt+1} failed: {e}")
            time.sleep(1 + attempt)
    return False


def _fallback_card(label: str, dest: str, size=(1920, 1080)):
    img = Image.new("RGB", size, (10, 10, 25))
    draw = ImageDraw.Draw(img)
    # Red banner
    bh = size[1] // 7
    draw.rectangle([0, size[1]//2 - bh, size[0], size[1]//2 + bh], fill=(160, 15, 15))
    # Text
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 54
        )
    except Exception:
        font = ImageFont.load_default()
    text = label[:80]
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
    draw.text(((size[0]-tw)//2, (size[1]-th)//2), text, fill="white", font=font)
    img.save(dest)


def _search_ddg(query: str, max_results: int = 8) -> list:
    try:
        with DDGS() as ddgs:
            return list(ddgs.images(query, max_results=max_results, size="Large"))
    except Exception as e:
        print(f"[IMG] DDG search failed for '{query}': {e}")
        return []


# ── Main API ──────────────────────────────────────────────────────────────────

def fetch_images_for_scenes(
    script: str,
    fallback_query: str,
    output_dir: str,
    images_per_scene: int = IMAGES_PER_SCENE,
) -> list:
    """
    Fetch images scene-by-scene from the script.
    Returns a flat list of local image paths — one per video clip.
    """
    os.makedirs(output_dir, exist_ok=True)
    scenes = split_into_scenes(script)
    print(f"[IMG] {len(scenes)} scenes → {len(scenes)*images_per_scene} images target")

    all_paths = []
    img_index = 0

    for scene_idx, scene in enumerate(scenes):
        query = scene_to_query(scene)
        print(f"[IMG] Scene {scene_idx+1}/{len(scenes)}: '{query}'")

        results = _search_ddg(query, max_results=images_per_scene * 3)
        # Fallback to broader query if no results
        if not results:
            results = _search_ddg(fallback_query, max_results=images_per_scene * 3)

        fetched_this_scene = 0
        for r in results:
            if fetched_this_scene >= images_per_scene:
                break
            url = r.get("image", "")
            if not url:
                continue
            slug = hashlib.md5(url.encode()).hexdigest()[:8]
            dest = os.path.join(output_dir, f"img_{img_index:04d}_{slug}.jpg")
            if _download(url, dest):
                all_paths.append(dest)
                img_index += 1
                fetched_this_scene += 1
                print(f"[IMG] ✓ scene {scene_idx+1} img {fetched_this_scene}")

        # Fill any gap with fallback cards
        while fetched_this_scene < images_per_scene:
            dest = os.path.join(output_dir, f"img_{img_index:04d}_card.jpg")
            _fallback_card(query, dest, (1920, 1080))
            all_paths.append(dest)
            img_index += 1
            fetched_this_scene += 1

    print(f"[IMG] Total images collected: {len(all_paths)}")
    return all_paths


def fetch_images(query: str, count: int, output_dir: str, size_hint: str = "landscape") -> list:
    """Simple fetch by count — used as a fallback or for Shorts."""
    os.makedirs(output_dir, exist_ok=True)
    card_size = (1080, 1920) if size_hint == "portrait" else (1920, 1080)
    paths = []
    results = _search_ddg(query, max_results=count * 4)

    for r in results:
        if len(paths) >= count:
            break
        url = r.get("image", "")
        if not url:
            continue
        slug = hashlib.md5(url.encode()).hexdigest()[:8]
        dest = os.path.join(output_dir, f"img_{len(paths):04d}_{slug}.jpg")
        if _download(url, dest):
            paths.append(dest)

    while len(paths) < count:
        dest = os.path.join(output_dir, f"img_{len(paths):04d}_card.jpg")
        _fallback_card(query, dest, card_size)
        paths.append(dest)

    return paths
