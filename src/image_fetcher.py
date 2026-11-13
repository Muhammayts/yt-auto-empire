"""
Image Fetcher — downloads high-res images via DuckDuckGo.
Includes retry logic and fallback news-card generator so
80+ scene 10-minute videos never crash due to missing images.
"""

import os
import time
import hashlib
import requests
from PIL import Image, ImageDraw, ImageFont
from duckduckgo_search import DDGS

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 11) AppleWebKit/537.36"
}
TIMEOUT = 12
MAX_RETRIES = 3
MIN_WIDTH = 400


def _download_image(url: str, dest: str) -> bool:
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True)
            if r.status_code == 200:
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
                img = Image.open(dest)
                if img.width >= MIN_WIDTH:
                    img.close()
                    return True
                img.close()
                os.remove(dest)
        except Exception as e:
            print(f"[IMG] Attempt {attempt+1} failed for {url}: {e}")
            time.sleep(1 + attempt)
    return False


def _make_fallback_card(query: str, dest: str, size: tuple = (1920, 1080)):
    """Generate a plain dark news-card image as a fallback."""
    img = Image.new("RGB", size, color=(15, 15, 30))
    draw = ImageDraw.Draw(img)
    # Draw a red banner
    draw.rectangle([0, size[1]//2 - 80, size[0], size[1]//2 + 80], fill=(180, 20, 20))
    # Try to load a font; fall back to default
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 60)
    except Exception:
        font = ImageFont.load_default()
    text = query[:80]
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size[0] - tw) // 2, size[1]//2 - th//2), text, fill="white", font=font)
    img.save(dest)
    print(f"[IMG] Fallback card created → {dest}")


def fetch_images(query: str, count: int, output_dir: str, size_hint: str = "landscape") -> list:
    """
    Fetch `count` images for `query`. Returns list of local file paths.
    Always returns exactly `count` paths — uses fallback cards for any failures.
    """
    os.makedirs(output_dir, exist_ok=True)
    paths = []
    fetched = 0

    print(f"[IMG] Searching DuckDuckGo: '{query}' × {count}")
    try:
        with DDGS() as ddgs:
            results = list(ddgs.images(
                query,
                max_results=count * 3,  # over-fetch to allow failures
                size="Large",
            ))
    except Exception as e:
        print(f"[IMG] DDG search failed: {e}")
        results = []

    for r in results:
        if fetched >= count:
            break
        url = r.get("image", "")
        if not url:
            continue
        slug = hashlib.md5(url.encode()).hexdigest()[:8]
        dest = os.path.join(output_dir, f"img_{fetched:03d}_{slug}.jpg")
        if _download_image(url, dest):
            paths.append(dest)
            fetched += 1
            print(f"[IMG] ✓ {fetched}/{count}")

    # Fill any gaps with fallback cards
    while len(paths) < count:
        idx = len(paths)
        dest = os.path.join(output_dir, f"img_{idx:03d}_fallback.jpg")
        card_size = (1080, 1920) if size_hint == "portrait" else (1920, 1080)
        _make_fallback_card(query, dest, card_size)
        paths.append(dest)

    return paths


if __name__ == "__main__":
    imgs = fetch_images("Russia Ukraine war 2025", count=3, output_dir="/tmp/test_imgs")
    for p in imgs:
        print(p)
