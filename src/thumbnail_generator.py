"""
Thumbnail Generator — creates a YouTube-ready 1280x720 thumbnail.

Pipeline:
  1. Generate a vivid background via:
       - Gemini API (google-generativeai) if GEMINI_API_KEY is set
       - Pollinations.ai as free fallback (watermark cropped out)
  2. Composite Japan Viral overlay using PIL:
       - Dark semi-transparent gradient bar across the bottom
       - "🔥 TRENDING" badge (top-left, red/orange)
       - Headline text (white, bold, word-wrapped)
       - Channel name lower-left
  3. Save as 1280x720 JPEG (YouTube recommended size)

Usage:
  thumb_path = generate_thumbnail(title, search_query, output_dir)
"""

import hashlib
import os
import re
import textwrap
import urllib.parse

import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter

HEADERS = {"User-Agent": "JapanViral/1.0"}
TIMEOUT = 45
THUMB_W, THUMB_H = 1280, 720


# ── Colour palette ─────────────────────────────────────────────────────────────
RED     = (220, 30,  10)
ORANGE  = (255, 90,  0)
DARK_BG = (10,  5,   20,  210)
WHITE   = (255, 255, 255)
YELLOW  = (255, 215, 0)


# ── Font helpers ───────────────────────────────────────────────────────────────

def _load_font(size: int, bold: bool = True):
    paths = [
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Bold.otf",
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
            f"Ultra-realistic vibrant Japan photograph, {prompt}, "
            f"dramatic cinematic lighting, vivid colors, "
            f"Tokyo skyline anime food technology nature, "
            f"NO people NO human faces, 16:9 aspect ratio, "
            f"no text, no watermarks, photorealistic 4K"
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
                final = cropped.resize((THUMB_W, THUMB_H), Image.LANCZOS)
                final.save(dest, "JPEG", quality=95)
                print(f"[THUMB/Pollinations] ✅ Background ready")
                return True
        except Exception as e:
            print(f"[THUMB/Pollinations] Attempt {attempt+1}: {e}")
        import time as _t; _t.sleep(3)
    return False


def _dark_fallback_background(dest: str, keyword: str = ""):
    """Generate a simple dark gradient background as last resort."""
    img  = Image.new("RGB", (THUMB_W, THUMB_H), (10, 5, 25))
    draw = ImageDraw.Draw(img)
    # Rising sun motif diagonal lines (Japan theme)
    for x in range(0, THUMB_W + THUMB_H, 55):
        draw.line([(x, 0), (x - THUMB_H, THUMB_H)], fill=(30, 10, 10), width=1)
    # Subtle grid
    for y in range(0, THUMB_H, 80):
        draw.line([(0, y), (THUMB_W, y)], fill=(20, 15, 40), width=1)
    for x in range(0, THUMB_W, 80):
        draw.line([(x, 0), (x, THUMB_H)], fill=(20, 15, 40), width=1)
    img.save(dest, "JPEG", quality=92)


# ── Text overlay ───────────────────────────────────────────────────────────────

def _add_japan_overlay(img_path: str, headline: str, search_query: str):
    """
    Add a Japan Viral branded overlay onto the background image.
    Red/orange trending badge, clean white headline.
    """
    img  = Image.open(img_path).convert("RGBA")
    over = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(over)

    W, H = img.size

    # ── Bottom dark gradient bar ───────────────────────────────────────────────
    bar_top = int(H * 0.52)
    for y in range(bar_top, H):
        alpha = int(230 * (y - bar_top) / (H - bar_top))
        draw.rectangle([(0, y), (W, y)], fill=(5, 3, 15, alpha))

    # ── "🔥 TRENDING" badge (top-left) ────────────────────────────────────────
    badge_font = _load_font(36, bold=True)
    badge_text = "TRENDING"
    bbox   = draw.textbbox((0, 0), badge_text, font=badge_font)
    bw, bh = bbox[2] - bbox[0] + 24, bbox[3] - bbox[1] + 14
    # Gradient-style badge: use orange
    draw.rounded_rectangle([(20, 20), (20 + bw, 20 + bh)], radius=6, fill=(*ORANGE, 230))
    draw.text((32, 27), badge_text, font=badge_font, fill=(255, 255, 255, 255))

    # ── "JAPAN VIRAL" channel tag ──────────────────────────────────────────────
    ch_font = _load_font(30, bold=True)
    draw.text((28, H - 46), "JAPAN VIRAL", font=ch_font, fill=(*RED, 240))

    # ── Main headline (word-wrapped, white) ───────────────────────────────────
    # Strip emoji and Japanese punctuation for safe ASCII rendering if needed
    clean_headline = headline.strip()
    # Remove common prefixes added by news_fetcher
    clean_headline = re.sub(r'^(🔥\s*)?(TRENDING:|話題:)\s*', '', clean_headline,
                             flags=re.IGNORECASE).strip()

    # Detect if headline is Japanese (CJK chars)
    has_cjk = bool(re.search(r'[　-鿿豈-﫿]', clean_headline))
    if has_cjk:
        # For CJK text: shorter lines
        max_chars = 18
        line_h    = 72
    else:
        max_chars = 34
        line_h    = 68

    hl_font   = _load_font(60, bold=True)
    wrapped   = textwrap.fill(clean_headline[:120], width=max_chars)
    lines     = wrapped.split("\n")[:3]

    text_block_h = len(lines) * line_h
    text_y = H - 58 - text_block_h

    for i, line in enumerate(lines):
        y = text_y + i * line_h
        # Drop shadow
        draw.text((32, y + 3), line, font=hl_font, fill=(0, 0, 0, 180))
        # Main text
        draw.text((30, y), line, font=hl_font, fill=(255, 255, 255, 255))

    # ── Merge and save ─────────────────────────────────────────────────────────
    final = Image.alpha_composite(img, over).convert("RGB")
    final.save(img_path, "JPEG", quality=95)


# ── Public API ─────────────────────────────────────────────────────────────────

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

    # Vibrant Japan-themed image prompt — NO faces or people
    prompt = (
        f"vibrant Japan photograph, {search_query} Tokyo Japan, "
        f"neon lights cherry blossoms anime technology food culture, "
        f"dramatic cinematic lighting, vivid saturated colors, "
        f"NO people NO faces, no text no watermarks, ultra realistic 4K"
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
    _add_japan_overlay(dest, title, search_query)

    size_kb = os.path.getsize(dest) // 1024
    print(f"[THUMB] ✅ Thumbnail → {dest}  ({size_kb} KB, {THUMB_W}×{THUMB_H})")
    return dest


if __name__ == "__main__":
    path = generate_thumbnail(
        title="🔥 話題: Nintendo Switch 2 発売日が判明！日本中が騒然",
        search_query="Nintendo Switch Tokyo Japan gaming trending",
    )
    print(f"Thumbnail saved: {path}")
