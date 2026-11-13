"""
Image Fetcher — scene-aware image downloader.

Primary source: Pixabay API (free key, works from GitHub Actions CI).
  - Sign up free at pixabay.com/api/docs/ and add PIXABAY_API_KEY secret.
  - 100 requests/minute, 500 results/query — plenty for any video.

Fallback: Wikimedia Commons (no API key required at all).

Scene-aware: splits script into scenes, extracts keywords per scene,
fetches 2 relevant images per scene for dynamic, matched visuals.
"""

import hashlib
import os
import re
import time
import requests
from PIL import Image, ImageDraw, ImageFont

HEADERS  = {"User-Agent": "EurasiaBreaking/1.0 (news automation)"}
TIMEOUT  = 15
MIN_DIM  = 400
IMGS_PER = 2   # images per scene


# ── Scene splitting ───────────────────────────────────────────────────────────

def split_into_scenes(script: str) -> list:
    sentences = re.split(r'(?<=[.!?])\s+', script.strip())
    sentences = [s.strip() for s in sentences if len(s.strip()) > 15]
    scenes = []
    for i in range(0, len(sentences), 2):
        scenes.append(" ".join(sentences[i:i+2]))
    return scenes or [script[:200]]


def scene_to_query(scene: str) -> str:
    stopwords = {
        "the","a","an","is","are","was","were","has","have","had",
        "will","would","could","should","their","there","this","that",
        "also","just","now","today","new","latest","breaking","says",
        "said","amid","over","with","from","after","before","during",
        "на","в","и","с","по","к","из","за","о","от","до","что","как",
        "для","при","его","они","она","мы","вы","он","не","по","же",
    }
    words = re.findall(r'\b[A-Za-zА-Яа-яЁё]{4,}\b', scene)
    filtered = [w for w in words if w.lower() not in stopwords][:5]
    # Translate common Russian war terms to English for better image results
    translations = {
        "российские": "Russian", "войска": "military troops",
        "украинские": "Ukrainian", "фронт": "frontline",
        "наступление": "offensive", "удары": "strikes",
        "оборона": "defense", "артиллерия": "artillery",
        "президент": "president", "переговоры": "negotiations",
        "санкции": "sanctions", "нато": "NATO",
    }
    translated = []
    for w in filtered:
        translated.append(translations.get(w.lower(), w))
    return " ".join(translated) if translated else "Russia news"


# ── Downloaders ───────────────────────────────────────────────────────────────

def _download(url: str, dest: str) -> bool:
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True)
            if r.status_code != 200:
                return False
            with open(dest, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
            img = Image.open(dest)
            ok  = img.width >= MIN_DIM and img.height >= MIN_DIM
            img.close()
            if ok:
                return True
            os.remove(dest)
        except Exception as e:
            print(f"[IMG] Download attempt {attempt+1} failed: {e}")
            time.sleep(1 + attempt)
    return False


# ── Pixabay API ───────────────────────────────────────────────────────────────

def _pixabay_search(query: str, count: int = 10) -> list:
    """
    Search Pixabay. Returns list of image URLs.
    Requires PIXABAY_API_KEY environment variable (GitHub Secret).
    Free tier: 100 req/min, 500 results/query.
    """
    key = os.environ.get("PIXABAY_API_KEY", "")
    if not key:
        print("[IMG] PIXABAY_API_KEY not set — skipping Pixabay")
        return []
    try:
        r = requests.get(
            "https://pixabay.com/api/",
            params={
                "key": key,
                "q": query,
                "image_type": "photo",
                "orientation": "horizontal",
                "category": "news",
                "min_width": 800,
                "per_page": min(count * 3, 50),
                "safesearch": "true",
            },
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            print(f"[IMG] Pixabay returned {r.status_code} for '{query}'")
            return []
        hits = r.json().get("hits", [])
        # Return large image URLs
        urls = [h.get("largeImageURL") or h.get("webformatURL") for h in hits]
        print(f"[IMG] Pixabay: {len(urls)} results for '{query}'")
        return [u for u in urls if u]
    except Exception as e:
        print(f"[IMG] Pixabay search failed: {e}")
        return []


def _pixabay_search_broad(query: str, count: int = 10) -> list:
    """Second attempt with no category filter."""
    key = os.environ.get("PIXABAY_API_KEY", "")
    if not key:
        return []
    try:
        r = requests.get(
            "https://pixabay.com/api/",
            params={
                "key": key,
                "q": query,
                "image_type": "photo",
                "min_width": 600,
                "per_page": min(count * 3, 50),
                "safesearch": "true",
            },
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        hits = r.json().get("hits", []) if r.status_code == 200 else []
        return [h.get("largeImageURL") or h.get("webformatURL") for h in hits if h.get("largeImageURL") or h.get("webformatURL")]
    except Exception:
        return []


# ── Wikimedia Commons fallback (no API key) ───────────────────────────────────

def _wikimedia_search(query: str, count: int = 6) -> list:
    """
    Search Wikimedia Commons for images. No API key required.
    Good for news/political subjects.
    """
    try:
        r = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "generator": "search",
                "gsrnamespace": "6",
                "gsrsearch": query,
                "gsrlimit": str(count * 3),
                "prop": "imageinfo",
                "iiprop": "url|size",
                "iiurlwidth": "1200",
                "format": "json",
            },
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        pages = r.json().get("query", {}).get("pages", {}).values()
        urls = []
        for page in pages:
            for ii in page.get("imageinfo", []):
                url = ii.get("thumburl") or ii.get("url", "")
                w   = ii.get("thumbwidth", 0) or ii.get("width", 0)
                if url and w >= MIN_DIM:
                    urls.append(url)
        print(f"[IMG] Wikimedia: {len(urls)} results for '{query}'")
        return urls
    except Exception as e:
        print(f"[IMG] Wikimedia search failed: {e}")
        return []


# ── Fallback card ─────────────────────────────────────────────────────────────

def _fallback_card(label: str, dest: str, size=(1920, 1080)):
    img  = Image.new("RGB", size, (10, 10, 25))
    draw = ImageDraw.Draw(img)
    bh   = size[1] // 7
    draw.rectangle([0, size[1]//2-bh, size[0], size[1]//2+bh], fill=(150, 15, 15))
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 54
        )
    except Exception:
        font = ImageFont.load_default()
    text = label[:70]
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
    draw.text(((size[0]-tw)//2, (size[1]-th)//2), text, fill="white", font=font)
    img.save(dest, "JPEG", quality=85)


# ── Core fetch function ───────────────────────────────────────────────────────

def _fetch_n_images(query: str, count: int, output_dir: str, start_idx: int) -> list:
    """Fetch `count` images for `query`, save to output_dir, return paths."""
    paths = []

    # 1. Try Pixabay (news category)
    urls = _pixabay_search(query, count)

    # 2. Try Pixabay broad
    if len(urls) < count:
        urls += _pixabay_search_broad(query, count)

    # 3. Try Wikimedia
    if len(urls) < count:
        urls += _wikimedia_search(query, count)

    seen = set()
    for url in urls:
        if len(paths) >= count:
            break
        if url in seen:
            continue
        seen.add(url)
        slug = hashlib.md5(url.encode()).hexdigest()[:8]
        dest = os.path.join(output_dir, f"img_{start_idx+len(paths):04d}_{slug}.jpg")
        if _download(url, dest):
            paths.append(dest)

    # 4. Fallback cards for any remaining
    while len(paths) < count:
        dest = os.path.join(output_dir, f"img_{start_idx+len(paths):04d}_card.jpg")
        _fallback_card(query, dest)
        paths.append(dest)

    return paths


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_images_for_scenes(
    script: str,
    fallback_query: str,
    output_dir: str,
    images_per_scene: int = IMGS_PER,
) -> list:
    os.makedirs(output_dir, exist_ok=True)
    scenes    = split_into_scenes(script)
    all_paths = []
    print(f"[IMG] {len(scenes)} scenes → ~{len(scenes)*images_per_scene} images")

    for idx, scene in enumerate(scenes):
        query = scene_to_query(scene)
        print(f"[IMG] Scene {idx+1}/{len(scenes)}: '{query}'")
        imgs  = _fetch_n_images(query, images_per_scene, output_dir, len(all_paths))
        # If we got all fallbacks, retry with the global fallback query
        if all(p.endswith("_card.jpg") for p in imgs):
            print(f"[IMG] All fallbacks — retrying with '{fallback_query}'")
            imgs = _fetch_n_images(fallback_query, images_per_scene, output_dir, len(all_paths))
        all_paths.extend(imgs)

    print(f"[IMG] Total images: {len(all_paths)}")
    return all_paths


def fetch_images(query: str, count: int, output_dir: str, size_hint: str = "landscape") -> list:
    os.makedirs(output_dir, exist_ok=True)
    return _fetch_n_images(query, count, output_dir, 0)
