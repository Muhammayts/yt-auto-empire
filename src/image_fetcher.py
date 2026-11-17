"""
Image Fetcher — scene-aware, multi-source image downloader.

Sources tried in order (all free, no API keys required):
  1. Pollinations.ai  — AI-generated realistic images, scene-specific prompts
  2. Openverse        — millions of CC-licensed real photos (no key needed)
  3. Wikimedia Commons — encyclopedia photos (no key needed)
  4. Fallback card    — generated card (last resort)

Scene-aware: splits script into scenes, extracts keywords per scene,
fetches 1 matched image per scene for dynamic visuals.
Channel: SugoidesJapan — Tokyo cityscapes, anime, tech, food, pop culture.
"""

import hashlib
import os
import re
import time
import urllib.parse
import requests
from PIL import Image, ImageDraw, ImageFont

HEADERS = {"User-Agent": "SugoidesJapan/1.0 (content automation bot)"}
TIMEOUT = 30   # Pollinations can be slow first call
MIN_DIM = 400
IMGS_PER_SENTENCE = 1   # 1 image per sentence = maximum visual variety


# ── Scene splitting ───────────────────────────────────────────────────────────

def split_into_scenes(script: str) -> list:
    """Split script into individual sentences — 1 image per sentence."""
    sentences = re.split(r'(?<=[.!?])\s+', script.strip())
    sentences = [s.strip() for s in sentences if len(s.strip()) > 15]
    return sentences or [script[:200]]


# Japanese → English translations for better image generation
_JA_EN = {
    "東京": "Tokyo", "大阪": "Osaka", "京都": "Kyoto", "日本": "Japan",
    "アニメ": "anime", "マンガ": "manga", "ゲーム": "game", "任天堂": "Nintendo",
    "ソニー": "Sony", "トヨタ": "Toyota", "ラーメン": "ramen", "寿司": "sushi",
    "桜": "cherry blossoms sakura", "富士山": "Mount Fuji", "相撲": "sumo",
    "侍": "samurai", "忍者": "ninja", "かわいい": "kawaii cute",
    "アイドル": "idol jpop", "コスプレ": "cosplay", "ロボット": "robot",
    "テクノロジー": "technology", "地震": "earthquake", "台風": "typhoon",
    "渋谷": "Shibuya", "秋葉原": "Akihabara", "原宿": "Harajuku",
    "新幹線": "bullet train shinkansen", "経済": "economy",
}

STOPWORDS = {
    "the","a","an","is","are","was","were","has","have","had","will","would",
    "could","should","their","there","this","that","also","just","now","today",
    "new","latest","trending","says","said","amid","over","with","from","after",
    "before","during","and","but","for","not","very","been","they","what",
    "about","which","when","more","some","than","into","only","here",
}


def scene_to_english_query(scene: str) -> str:
    """Extract keywords and translate Japanese → English for image searches."""
    # First translate any Japanese characters
    translated = scene
    for ja, en in _JA_EN.items():
        translated = translated.replace(ja, " " + en + " ")
    words = re.findall(r'\b[A-Za-z]{4,}\b', translated)
    result = []
    for w in words:
        lower = w.lower()
        if lower in STOPWORDS:
            continue
        result.append(w)
        if len(result) >= 5:
            break
    return " ".join(result) if result else "Japan Tokyo viral trending"


def scene_to_image_prompt(scene: str, scene_idx: int) -> str:
    """
    Build a high-quality Pollinations.ai prompt for SugoidesJapan channel.
    Themes: Tokyo cityscapes, anime art, Japanese food, tech, pop culture,
    nature (cherry blossoms, Mount Fuji), architecture.
    NO human faces (keeps AI weirdness away).
    Alternates between angles for visual variety.
    """
    keywords = scene_to_english_query(scene)

    angles = [
        # Tokyo/city establishing shot
        f"wide angle aerial photograph of Tokyo Japan, {keywords}, "
        f"neon lights city skyline skyscrapers, golden hour sunset, "
        f"no people no faces, hyper realistic 8K photography, vibrant colors",
        # Japanese culture / aesthetic
        f"beautiful Japanese aesthetic photograph, {keywords} Japan, "
        f"cherry blossoms sakura street traditional shrine torii gate architecture, "
        f"soft natural light, NO people NO faces, ultra realistic cinematic",
        # Japanese food / product close-up
        f"professional food product photography, {keywords} Japan, "
        f"Japanese cuisine ramen sushi bento colorful presentation, "
        f"restaurant kitchen setting, NO people, sharp macro detail, vivid colors",
        # Technology / anime / gaming
        f"digital art illustration, {keywords} Japan technology, "
        f"anime style Tokyo futuristic cyberpunk neon, robot gadget console, "
        f"no people no faces no text, vibrant dramatic lighting, 4K render",
        # Nature / landscape Japan
        f"stunning landscape photography Japan, {keywords}, "
        f"Mount Fuji cherry blossoms bamboo forest temple pagoda, "
        f"dramatic sky natural lighting, NO people, travel photography 8K",
    ]
    return angles[scene_idx % len(angles)]


# ── Pollinations.ai (AI image generation, completely free) ───────────────────

def _remove_watermark(img_path: str, target_w: int, target_h: int):
    """
    Crop away the Pollinations watermark (bottom-right corner)
    then resize back to target dimensions.
    Works even if nologo=true is not respected by the model.
    """
    try:
        img = Image.open(img_path).convert("RGB")
        w, h = img.size
        # Crop bottom 85px where the watermark sits
        crop_px = max(85, int(h * 0.075))
        cropped = img.crop((0, 0, w, h - crop_px))
        # Resize back to target — slightly stretched but watermark gone
        final = cropped.resize((target_w, target_h), Image.LANCZOS)
        final.save(img_path, "JPEG", quality=92)
    except Exception as e:
        print(f"[IMG/AI] Watermark removal failed (non-fatal): {e}")


def _pollinations(prompt: str, dest: str, width=1920, height=1080) -> bool:
    """
    Generate a realistic image via Pollinations.ai.
    Free, no API key, works from CI. Returns True on success.
    Watermark is cropped out after download.
    """
    encoded = urllib.parse.quote(prompt)
    # Add a seed based on dest hash for variety
    seed = int(hashlib.md5(dest.encode()).hexdigest()[:8], 16) % 99999
    # nologo=true + nofeed=true both required; also try flux-realism for better quality
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width={width}&height={height}&seed={seed}"
        f"&nologo=true&nofeed=true&enhance=true&model=flux-realism"
    )
    for attempt in range(3):
        try:
            print(f"[IMG/AI] Generating: '{prompt[:60]}...' (attempt {attempt+1})")
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True)
            if r.status_code == 200 and len(r.content) > 10000:
                with open(dest, "wb") as f:
                    f.write(r.content)
                img = Image.open(dest)
                ok  = img.width >= MIN_DIM
                img.close()
                if ok:
                    print(f"[IMG/AI] ✅ Generated {img.width}x{img.height} — removing watermark")
                    _remove_watermark(dest, width, height)
                    return True
                os.remove(dest)
            else:
                print(f"[IMG/AI] Got {r.status_code}, size={len(r.content)}")
        except Exception as e:
            print(f"[IMG/AI] Attempt {attempt+1} failed: {e}")
        time.sleep(3 + attempt * 2)
    return False


# ── Openverse (CC-licensed real photos, no key needed) ───────────────────────

def _openverse_search(query: str, count: int = 8) -> list:
    """
    Search Openverse (openverse.org) — millions of CC-licensed photos.
    No API key required for basic use.
    """
    try:
        r = requests.get(
            "https://api.openverse.org/v1/images/",
            params={
                "q": query,
                "license_type": "commercial",
                "size": "large",
                "page_size": min(count * 3, 20),
            },
            headers={**HEADERS, "Accept": "application/json"},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            print(f"[IMG/OV] Openverse {r.status_code} for '{query}'")
            return []
        results = r.json().get("results", [])
        urls = [res.get("url", "") for res in results if res.get("url")]
        print(f"[IMG/OV] {len(urls)} results for '{query}'")
        return urls
    except Exception as e:
        print(f"[IMG/OV] Failed: {e}")
        return []


# ── Wikimedia Commons (no key needed) ────────────────────────────────────────

def _wikimedia_search(query: str, count: int = 6) -> list:
    try:
        r = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query", "generator": "search",
                "gsrnamespace": "6", "gsrsearch": query,
                "gsrlimit": str(count * 3), "prop": "imageinfo",
                "iiprop": "url|size", "iiurlwidth": "1200", "format": "json",
            },
            headers=HEADERS, timeout=TIMEOUT,
        )
        pages = r.json().get("query", {}).get("pages", {}).values()
        urls  = []
        for page in pages:
            for ii in page.get("imageinfo", []):
                url = ii.get("thumburl") or ii.get("url", "")
                w   = ii.get("thumbwidth", 0) or ii.get("width", 0)
                if url and w >= MIN_DIM:
                    urls.append(url)
        print(f"[IMG/WM] {len(urls)} results for '{query}'")
        return urls
    except Exception as e:
        print(f"[IMG/WM] Failed: {e}")
        return []


# ── Download helper ───────────────────────────────────────────────────────────

def _download(url: str, dest: str) -> bool:
    for attempt in range(2):
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
            time.sleep(1)
    return False


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


# ── Core fetch for one scene ──────────────────────────────────────────────────

def _fetch_for_scene(scene: str, scene_idx: int, count: int,
                     output_dir: str, start_idx: int) -> list:
    query  = scene_to_english_query(scene)
    prompt = scene_to_image_prompt(scene, scene_idx)
    paths  = []

    for i in range(count):
        dest = os.path.join(output_dir, f"img_{start_idx+i:04d}_s{scene_idx}.jpg")

        # 1. Try AI generation (Pollinations) — most scene-specific
        if _pollinations(prompt, dest):
            paths.append(dest)
            continue

        # 2. Try Openverse real photos
        urls = _openverse_search(query, count=6)
        fetched = False
        for url in urls:
            slug = hashlib.md5(url.encode()).hexdigest()[:6]
            d2   = os.path.join(output_dir, f"img_{start_idx+i:04d}_ov{slug}.jpg")
            if _download(url, d2):
                paths.append(d2)
                fetched = True
                break
        if fetched:
            continue

        # 3. Try Wikimedia
        wm_urls = _wikimedia_search(query, count=6)
        for url in wm_urls:
            slug = hashlib.md5(url.encode()).hexdigest()[:6]
            d3   = os.path.join(output_dir, f"img_{start_idx+i:04d}_wm{slug}.jpg")
            if _download(url, d3):
                paths.append(d3)
                fetched = True
                break
        if fetched:
            continue

        # 4. Fallback card
        fc = os.path.join(output_dir, f"img_{start_idx+i:04d}_card.jpg")
        _fallback_card(f"SugoidesJapan — {query}", fc)
        paths.append(fc)

    return paths


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_images_for_scenes(
    script: str,
    fallback_query: str,
    output_dir: str,
    images_per_scene: int = 2,
) -> list:
    os.makedirs(output_dir, exist_ok=True)
    scenes    = split_into_scenes(script)
    all_paths = []
    print(f"[IMG] {len(scenes)} scenes × {images_per_scene} = {len(scenes)*images_per_scene} images")

    for idx, scene in enumerate(scenes):
        print(f"\n[IMG] ── Scene {idx+1}/{len(scenes)} ──")
        imgs = _fetch_for_scene(scene, idx, images_per_scene, output_dir, len(all_paths))
        all_paths.extend(imgs)

    print(f"\n[IMG] Total images collected: {len(all_paths)}")
    return all_paths


def fetch_images(query: str, count: int, output_dir: str, size_hint: str = "landscape") -> list:
    os.makedirs(output_dir, exist_ok=True)
    paths = []
    for i in range(count):
        dest   = os.path.join(output_dir, f"img_{i:04d}.jpg")
        prompt = f"photorealistic news photo, {query}, journalism, sharp focus"
        if _pollinations(prompt, dest):
            paths.append(dest)
        else:
            _fallback_card(query, dest)
            paths.append(dest)
    return paths
