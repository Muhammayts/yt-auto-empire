"""
Image Fetcher — scene-aware, multi-source image downloader.

Sources tried in order (all free, no API keys required):
  1. Pollinations.ai  — AI-generated realistic images, scene-specific prompts
  2. Openverse        — millions of CC-licensed real photos (no key needed)
  3. Wikimedia Commons — encyclopedia photos (no key needed)
  4. Fallback card    — generated news card (last resort)

Scene-aware: splits script into scenes, extracts keywords per scene,
fetches 2 matched images per scene for dynamic visuals.
"""

import hashlib
import os
import re
import time
import urllib.parse
import requests
from PIL import Image, ImageDraw, ImageFont

HEADERS = {"User-Agent": "EurasiaBreaking/1.0 (news automation bot)"}
TIMEOUT = 30   # Pollinations can be slow first call
MIN_DIM = 400
IMGS_PER_SENTENCE = 1   # 1 image per sentence = maximum visual variety


# ── Scene splitting ───────────────────────────────────────────────────────────

def split_into_scenes(script: str) -> list:
    """Split script into individual sentences — 1 image per sentence."""
    sentences = re.split(r'(?<=[.!?])\s+', script.strip())
    sentences = [s.strip() for s in sentences if len(s.strip()) > 15]
    return sentences or [script[:200]]


# Russian → English translations for better image generation
_RU_EN = {
    "российские": "Russian", "войска": "military troops", "украинские": "Ukrainian",
    "фронт": "frontline", "наступление": "military offensive", "удары": "airstrikes",
    "оборона": "defense line", "артиллерия": "artillery", "президент": "president",
    "переговоры": "peace negotiations", "санкции": "economic sanctions",
    "нато": "NATO", "солдаты": "soldiers", "танки": "tanks", "взрывы": "explosions",
    "город": "city", "мирные": "civilians", "жители": "residents",
    "срочно": "breaking news", "сообщение": "news report", "украина": "Ukraine",
    "россия": "Russia", "москва": "Moscow", "киев": "Kyiv", "война": "war",
    "силы": "forces", "позиции": "military positions", "новости": "news",
}

STOPWORDS = {
    "the","a","an","is","are","was","were","has","have","had","will","would",
    "could","should","their","there","this","that","also","just","now","today",
    "new","latest","breaking","says","said","amid","over","with","from","after",
    "before","during","and","but","for","not","они","она","мы","вы","он","не",
    "на","в","и","с","по","к","из","за","о","от","до","что","как","для","при",
    "его","что","это","так","уже","ещё","где","там","тут","всё","все",
}


def scene_to_english_query(scene: str) -> str:
    """Extract keywords and translate Russian → English for image searches."""
    words = re.findall(r'\b[A-Za-zА-Яа-яЁё]{4,}\b', scene)
    result = []
    for w in words:
        lower = w.lower()
        if lower in STOPWORDS:
            continue
        result.append(_RU_EN.get(lower, w))
        if len(result) >= 5:
            break
    return " ".join(result) if result else "Russia news geopolitics"


def scene_to_image_prompt(scene: str, scene_idx: int) -> str:
    """
    Build a high-quality Pollinations.ai prompt for a realistic news photograph.
    Focus: landscapes, cityscapes, military hardware, flags, maps, buildings.
    NO human faces, NO portraits, NO AI-generated people.
    Alternates between different journalistic angles for variety.
    """
    keywords = scene_to_english_query(scene)

    angles = [
        # Wide city / landscape establishing shot
        f"wide angle aerial cityscape photograph, {keywords}, "
        f"dramatic natural lighting, urban skyline, buildings infrastructure, "
        f"no people no faces, no watermarks no text, ultra realistic 8K",
        # Military hardware / equipment
        f"photorealistic documentary photograph, {keywords}, "
        f"military vehicles tanks aircraft equipment, battlefield landscape, "
        f"golden hour lighting, NO people NO faces, no logos no text, cinematic realism",
        # Map / geography / satellite view
        f"satellite imagery aerial view, {keywords} region geography, "
        f"topographic landscape, natural terrain, overhead perspective, "
        f"no people no faces, sharp detail, hyper realistic",
        # Night city / dramatic skyline
        f"dramatic night photograph, {keywords}, "
        f"city lights skyline long exposure, burning horizon smoke, "
        f"moody cinematic atmosphere, NO people NO faces, no text, photorealistic 4K",
        # Flags / symbols / government buildings
        f"documentary photograph, {keywords}, "
        f"flags government buildings monuments architecture, "
        f"dramatic sky overcast moody, NO people NO faces, professional camera, photorealistic",
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
        _fallback_card(query, fc)
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
