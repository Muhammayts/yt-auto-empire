"""
Thumbnail Generator — creates a YouTube-ready 1280x720 thumbnail.

Pipeline:
  1. Generate a photorealistic background via:
       - Gemini API (google-generativeai) if GEMINI_API_KEY is set
       - Pollinations.ai as free fallback (watermark cropped out)
  2. Composite breaking-news lower-third overlay using PIL:
       - Dark semi-transparent gradient bar across the bottom
       - "EURASIA BREAKING" red badge (top-left)
       - Headline text (white, bold, word-wrapped)
       - 🔴 LIVE/BREAKING badge
  3. Save as 1280x720 JPEG (YouTube recommended size)

Usage:
  thumb_path = generate_thumbnail(title, search_query, output_dir)
"""

import hashlib
import os
import textwrap
import urllib.parse

import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter

HEADERS = {"User-Agent": "EurasiaBreaking/1.0"}
TIMEOUT = 45
THUMB_W, THUMB_H = 1280, 720


# ── Colour palette ─────────────────────────────────────────────────────────────
RED     = (220, 20,  20)
DARK_BG = (10,  10,  20,  210)   # near-black, 82% opaque
WHITE   = (255, 255, 255)
YELLOW  = (255, 215,  0)


# ── Font helpers ───────────────────────────────────────────────────────────────

def _load_font(size: int, bold: bool = True):
    paths = [
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'−Bold' if bold else ''}.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


# ── Background generation ──────────────────────────────────────────────────────

def _gemini_background(prompt: str, dest: str) -> bool:
    """
    Generate background image using Gemini 2.0 Flash image generation.
    Requires GEMINI_API_KEY environment variable.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return False

    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)

        model = genai.GenerativeModel("gemini-2.0-flash-exp-image-generation")
        full_prompt = (
            f"Ultra-realistic news photograph, {prompt}, "
            f"dramatic cinematic lighting, shallow depth of field, "
            f"Reuters wire photo style, 16:9 aspect ratio, no text, "
            f"no watermarks, professional photojournalism"
        )
        response = model.generate_content(
            full_prompt,
            generation_config={"response_modalities": ["IMAGE"]},
        )
        for part in response.candidates[0].content.parts:
            if hasattr(part, "inline_data") and part.inline_data:
                import base64
                img_bytes = base64.b64decode(part.inline_data.data)
                with open(dest, "wb") as f:
                    f.write(img_bytes)
                # Resize to exact thumbnail dimensions
                img = Image.open(dest).convert("RGB")
                img = img.resize((THUMB_W, THUMB_H), Image.LANCZOS)
                img.save(dest, "JPEG", quality=95)
                print(f"[THUMB/Gemini] ✅ Generated background")
                return True
    except Exception as e:
        print(f"[THUMB/Gemini] Failed: {e}")
    return False


def _pollinations_background(prompt: str, dest: str) -> bool:
    """Generate background via Pollinations.ai (free, watermark cropped)."""
    encoded = urllib.parse.quote(prompt)
    seed    = int(hashlib.md5(dest.encode()).hexdigest()[:8], 16) % 99999
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width={THUMB_W}&height={THUMB_H}&seed={seed}"
        f"&nologo=true&nofeed=true&enhance=true&model=flux-realism"
    )
    for attempt in range(3):
        try:
            print(f"[THUMB/Pollinations] Generating background (attempt {attempt+1})")
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True)
            if r.status_code == 200 and len(r.content) > 15000:
                with open(dest, "wb") as f:
                    f.write(r.content)
                img = Image.open(dest).convert("RGB")
                # Crop bottom watermark area (bottom 7%)
                crop_px  = int(img.height * 0.075)
                cropped  = img.crop((0, 0, img.width, img.height - crop_px))
                # Zoom-crop to fill exact thumbnail size (removes black bars)
                final = cropped.resize((THUMB_W, THUMB_H), Image.LANCZOS)
                final.save(dest, "JPEG", quality=95)
                print(f"[THUMB/Pollinations] ✅ Background ready")
                return True
        except Exception as e:
            print(f"[THUMB/Pollinations] Attempt {attempt+1}: {e}")
        import time; import time as _t; _t.sleep(3)
    return False


def _dark_fallback_background(dest: str, keyword: str = ""):
    """Generate a simple dark gradient background as last resort."""
    img  = Image.new("RGB", (THUMB_W, THUMB_H), (8, 8, 20))
    draw = ImageDraw.Draw(img)
    # Subtle red diagonal line pattern for news feel
    for x in range(0, THUMB_W + THUMB_H, 60):
        draw.line([(x, 0), (x - THUMB_H, THUMB_H)], fill=(25, 5, 5), width=2)
    # Faint world-map style grid
    for y in range(0, THUMB_H, 80):
        draw.line([(0, y), (THUMB_W, y)], fill=(20, 20, 40), width=1)
    for x in range(0, THUMB_W, 80):
        draw.line([(x, 0), (x, THUMB_H)], fill=(20, 20, 40), width=1)
    img.save(dest, "JPEG", quality=92)


# ── Text overlay ───────────────────────────────────────────────────────────────

def _add_news_overlay(img_path: str, headline: str, search_query: str):
    """
    Add a professional breaking-news overlay onto the background image.
    Returns the modified image (saved back to img_path).
    """
    img  = Image.open(img_path).convert("RGBA")
    over = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(over)

    W, H = img.size

    # ── Bottom dark gradient bar ───────────────────────────────────────────────
    bar_top = int(H * 0.55)
    for y in range(bar_top, H):
        alpha = int(220 * (y - bar_top) / (H - bar_top))
        draw.rectangle([(0, y), (W, y)], fill=(5, 5, 15, alpha))

    # ── "🔴 BREAKING" red badge (top-left) ───────────────────────────────────
    badge_font = _load_font(36, bold=True)
    badge_text = "● BREAKING"
    bbox   = draw.textbbox((0, 0), badge_text, font=badge_font)
    bw, bh = bbox[2] - bbox[0] + 24, bbox[3] - bbox[1] + 14
    draw.rounded_rectangle([(20, 20), (20 + bw, 20 + bh)], radius=6, fill=(*RED, 230))
    draw.text((32, 27), badge_text, font=badge_font, fill=(255, 255, 255, 255))

    # ── Channel name bottom-left ───────────────────────────────────────────────
    ch_font = _load_font(32, bold=True)
    draw.text((28, H - 48), "EURASIABREAKING", font=ch_font, fill=(*RED, 240))

    # ── Main headline (word-wrapped, white) ───────────────────────────────────
    # Strip emoji from headline for cleaner rendering
    clean_headline = re.sub(r'[^\x20-\x7EЀ-ӿ\s]', '', headline).strip()
    # Remove common prefixes added by news_fetcher
    clean_headline = re.sub(r'^(🔴\s*)?(BREAKING:|СРОЧНО:)\s*', '', clean_headline,
                             flags=re.IGNORECASE).strip()

    hl_font   = _load_font(58, bold=True)
    max_chars = 36
    wrapped   = textwrap.fill(clean_headline[:120], width=max_chars)
    lines     = wrapped.split("\n")[:3]

    # Measure total text block height
    line_h = 70
    text_block_h = len(lines) * line_h
    text_y = H - 55 - text_block_h

    # Shadow + text
    for i, line in enumerate(lines):
        y = text_y + i * line_h
        # Drop shadow
        draw.text((30, y + 3), line, font=hl_font, fill=(0, 0, 0, 180))
        # Main text
        draw.text((28, y), line, font=hl_font, fill=(255, 255, 255, 255))

    # ── Merge and save ─────────────────────────────────────────────────────────
    final = Image.alpha_composite(img, over).convert("RGB")
    final.save(img_path, "JPEG", quality=95)


# ── Public API ─────────────────────────────────────────────────────────────────

import re  # already imported above, here for clarity

def generate_thumbnail(
    title: str,
    search_query: str,
    output_dir: str = "output",
) -> str:
    """
    Generate a YouTube thumbnail for the given video title.
    Returns the path to the saved 1280x720 JPEG.
    """
    os.makedirs(output_dir, exist_ok=True)
    dest = os.path.join(output_dir, "thumbnail.jpg")

    # Build a vivid image prompt
    prompt = (
        f"dramatic news photograph, {search_query}, "
        f"photojournalism, war correspondents, breaking news, "
        f"moody dramatic lighting, cinematic"
    )

    # Try Gemini first (highest quality), then Pollinations, then fallback
    success = (
        _gemini_background(prompt, dest)
        or _pollinations_background(prompt, dest)
    )
    if not success:
        print("[THUMB] Using dark fallback background")
        _dark_fallback_background(dest, search_query)

    # Add text overlay
    _add_news_overlay(dest, title, search_query)

    size_kb = os.path.getsize(dest) // 1024
    print(f"[THUMB] ✅ Thumbnail → {dest}  ({size_kb} KB, {THUMB_W}×{THUMB_H})")
    return dest


if __name__ == "__main__":
    path = generate_thumbnail(
        title="🔴 BREAKING: Russia Launches Massive Drone Strike on Kyiv",
        search_query="Kyiv Ukraine drone strike night explosion",
    )
    print(f"Thumbnail saved: {path}")
