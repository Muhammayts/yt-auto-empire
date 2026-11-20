"""
Thumbnail Generator — creates a YouTube-ready 1280x720 thumbnail.

Design principles (MrBeast / top YouTube style):
  - Huge bold text that fills the frame
  - High-contrast colour blocking (not just a bottom bar)
  - Multiple visual layers: bg image → colour overlay → text → badges
  - Category-specific colour palettes
  - Thick text strokes so text pops on any background
  - Animated-feel composition (diagonal accents, corner badges)
"""

import hashlib
import os
import re
import textwrap
import urllib.parse
import random

import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

HEADERS = {"User-Agent": "SugoidesJapan/1.0"}
TIMEOUT = 45
THUMB_W, THUMB_H = 1280, 720

# ── Category colour palettes ───────────────────────────────────────────────────
PALETTES = {
    "viral":   {"accent": (255, 50,  50),  "badge_bg": (255, 30,  0),   "glow": (255, 100, 0)},
    "anime":   {"accent": (180, 0,  255),  "badge_bg": (120, 0,  220),  "glow": (200, 100, 255)},
    "gaming":  {"accent": (0,   220, 80),  "badge_bg": (0,  170, 50),   "glow": (0,   255, 120)},
    "music":   {"accent": (255, 20,  120), "badge_bg": (200, 0,  100),  "glow": (255, 100, 180)},
    "lifestyle":{"accent":(255, 160, 0),   "badge_bg": (220, 120, 0),   "glow": (255, 200, 50)},
    "tech":    {"accent": (0,   180, 255), "badge_bg": (0,  130, 220),  "glow": (100, 220, 255)},
    "news":    {"accent": (255, 50,  50),  "badge_bg": (180, 0,  0),    "glow": (255, 80,  80)},
    "default": {"accent": (255, 80,  0),   "badge_bg": (200, 40, 0),    "glow": (255, 150, 50)},
}

BADGE_LABELS = {
    "viral":    "🔥 VIRAL",
    "anime":    "🎌 ANIME",
    "gaming":   "🎮 GAMING",
    "music":    "🎵 J-POP",
    "lifestyle":"🌸 JAPAN",
    "tech":     "⚡ TECH",
    "news":     "📢 NEWS",
    "default":  "🔥 TRENDING",
}


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
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[THUMB/Gemini] No API key — skipping")
        return False

    full_prompt = (
        f"Ultra-vibrant cinematic Japan photograph, {prompt}, "
        f"dramatic backlighting, oversaturated vivid colors, lens flare, "
        f"NO people NO human faces, 16:9 widescreen, no text no watermarks, 4K"
    )

    # ── Try Imagen 3 first (best quality) ─────────────────────────────────────
    try:
        from google import genai as google_genai
        from google.genai import types as genai_types

        client = google_genai.Client(api_key=api_key)
        response = client.models.generate_images(
            model="imagen-3.0-generate-002",
            prompt=full_prompt,
            config=genai_types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio="16:9",
                safety_filter_level="BLOCK_ONLY_HIGH",
                person_generation="DONT_ALLOW",
            ),
        )
        if response.generated_images:
            import base64
            img_bytes = response.generated_images[0].image.image_bytes
            with open(dest, "wb") as f:
                f.write(img_bytes)
            img = Image.open(dest).convert("RGB")
            img = img.resize((THUMB_W, THUMB_H), Image.LANCZOS)
            img.save(dest, "JPEG", quality=95)
            print(f"[THUMB/Gemini] ✅ Imagen 3 background generated")
            return True
    except Exception as e:
        print(f"[THUMB/Gemini] Imagen 3 failed: {e}")

    # ── Fallback: Gemini 2.0 Flash image generation ────────────────────────────
    try:
        from google import genai as google_genai
        from google.genai import types as genai_types

        client = google_genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.0-flash-preview-image-generation",
            contents=full_prompt,
            config=genai_types.GenerateContentConfig(
                response_modalities=["IMAGE"],
            ),
        )
        import base64
        for part in response.candidates[0].content.parts:
            if hasattr(part, "inline_data") and part.inline_data:
                img_bytes = base64.b64decode(part.inline_data.data)
                with open(dest, "wb") as f:
                    f.write(img_bytes)
                img = Image.open(dest).convert("RGB")
                img = img.resize((THUMB_W, THUMB_H), Image.LANCZOS)
                img.save(dest, "JPEG", quality=95)
                print(f"[THUMB/Gemini] ✅ Gemini 2.0 Flash background generated")
                return True
    except Exception as e:
        print(f"[THUMB/Gemini] Flash fallback failed: {e}")

    return False


def _pollinations_background(prompt: str, dest: str) -> bool:
    encoded = urllib.parse.quote(prompt)
    seed    = int(hashlib.md5(dest.encode()).hexdigest()[:8], 16) % 99999
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width={THUMB_W}&height={THUMB_H}&seed={seed}"
        f"&nologo=true&nofeed=true&enhance=true&model=flux-realism"
    )
    for attempt in range(3):
        try:
            print(f"[THUMB/Pollinations] Attempt {attempt+1}")
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True)
            if r.status_code == 200 and len(r.content) > 15000:
                with open(dest, "wb") as f:
                    f.write(r.content)
                img = Image.open(dest).convert("RGB")
                crop_px = int(img.height * 0.075)
                cropped = img.crop((0, 0, img.width, img.height - crop_px))
                final   = cropped.resize((THUMB_W, THUMB_H), Image.LANCZOS)
                final.save(dest, "JPEG", quality=95)
                print(f"[THUMB/Pollinations] ✅ Background ready")
                return True
        except Exception as e:
            print(f"[THUMB/Pollinations] Attempt {attempt+1}: {e}")
        import time as _t; _t.sleep(3)
    return False


def _vivid_fallback_background(dest: str, category: str = "default"):
    """
    Rich gradient fallback — looks like a real thumbnail even without an image.
    Uses category colours with a dramatic diagonal split.
    """
    pal   = PALETTES.get(category, PALETTES["default"])
    acc   = pal["accent"]
    glow  = pal["glow"]

    img  = Image.new("RGB", (THUMB_W, THUMB_H), (8, 8, 20))
    draw = ImageDraw.Draw(img)

    # Dark-to-accent diagonal gradient (top-left → bottom-right)
    for y in range(THUMB_H):
        ratio = y / THUMB_H
        r = int(8   + ratio * acc[0] * 0.5)
        g = int(8   + ratio * acc[1] * 0.5)
        b = int(20  + ratio * acc[2] * 0.3)
        draw.rectangle([(0, y), (THUMB_W, y)], fill=(r, g, b))

    # Diagonal accent stripe (top-right corner)
    for i in range(0, 400, 20):
        draw.line([(THUMB_W - i, 0), (THUMB_W, i)], fill=(*acc, 60), width=8)

    # Glow circle in top-right
    for radius in range(300, 50, -30):
        alpha = int(40 * (1 - radius / 300))
        draw.ellipse(
            [(THUMB_W - radius, -radius // 2),
             (THUMB_W + radius // 2, radius)],
            fill=(*glow, alpha)
        )

    # Horizontal scan lines (subtle)
    for y in range(0, THUMB_H, 4):
        draw.rectangle([(0, y), (THUMB_W, y + 1)], fill=(0, 0, 0, 20))

    img.save(dest, "JPEG", quality=92)


# ── Image enhancement ──────────────────────────────────────────────────────────

def _boost_image(img_path: str):
    """Increase contrast and saturation so background pops under text overlay."""
    img = Image.open(img_path).convert("RGB")
    img = ImageEnhance.Contrast(img).enhance(1.25)
    img = ImageEnhance.Color(img).enhance(1.4)
    img = ImageEnhance.Sharpness(img).enhance(1.2)
    img.save(img_path, "JPEG", quality=95)


# ── Text drawing helpers ───────────────────────────────────────────────────────

def _draw_text_with_stroke(draw, pos, text, font, fill, stroke_color, stroke_width=6):
    """Draw text with a thick stroke/outline — makes text readable on any bg."""
    x, y = pos
    # Draw stroke (8 directions)
    for dx in range(-stroke_width, stroke_width + 1, 2):
        for dy in range(-stroke_width, stroke_width + 1, 2):
            if dx != 0 or dy != 0:
                draw.text((x + dx, y + dy), text, font=font, fill=(*stroke_color, 255))
    # Main text
    draw.text((x, y), text, font=font, fill=(*fill, 255))


def _draw_badge(draw, x, y, text, bg_color, text_color=(255, 255, 255), radius=10, pad_x=20, pad_y=10):
    """Draw a rounded pill badge."""
    font  = _load_font(38, bold=True)
    bbox  = draw.textbbox((0, 0), text, font=font)
    bw    = bbox[2] - bbox[0] + pad_x * 2
    bh    = bbox[3] - bbox[1] + pad_y * 2
    draw.rounded_rectangle([(x, y), (x + bw, y + bh)], radius=radius,
                            fill=(*bg_color, 240))
    # Highlight strip on top of badge
    draw.rounded_rectangle([(x+2, y+2), (x + bw - 2, y + bh // 2)],
                            radius=radius,
                            fill=(255, 255, 255, 30))
    draw.text((x + pad_x, y + pad_y), text, font=font,
              fill=(*text_color, 255))
    return bw, bh


# ── Main overlay ───────────────────────────────────────────────────────────────

def _add_catchy_overlay(img_path: str, headline: str, category: str = "default"):
    """
    Professional YouTube thumbnail overlay:
    - Heavy black vignette on bottom 60% for text contrast
    - Left-side dark panel (semi-transparent) for headline text
    - Huge bold stroked headline fills left 75% of frame
    - Category badge top-left with matching colour
    - Channel name bottom-right corner
    - Diagonal accent line from badge
    """
    pal      = PALETTES.get(category, PALETTES["default"])
    acc      = pal["accent"]
    badge_bg = pal["badge_bg"]
    badge_text = BADGE_LABELS.get(category, "🔥 TRENDING")

    img  = Image.open(img_path).convert("RGBA")
    over = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(over)
    W, H = img.size

    # ── 1. Dark gradient across entire bottom 65% ─────────────────────────────
    for y in range(int(H * 0.3), H):
        t     = (y - H * 0.3) / (H * 0.7)
        alpha = int(220 * min(t * 1.6, 1.0))
        draw.rectangle([(0, y), (W, y)], fill=(0, 0, 0, alpha))

    # ── 2. Left-side semi-dark panel for headline readability ─────────────────
    panel_w = int(W * 0.78)
    for x in range(panel_w):
        t     = 1 - (x / panel_w)
        alpha = int(80 * t)
        draw.rectangle([(x, int(H * 0.35)), (x + 1, H)], fill=(0, 0, 0, alpha))

    # ── 3. Accent diagonal line from top-left ─────────────────────────────────
    draw.line([(0, 0), (int(W * 0.55), H)], fill=(*acc, 25), width=180)

    # ── 4. Category badge — top-left ─────────────────────────────────────────
    bw, bh = _draw_badge(draw, 24, 24, badge_text, badge_bg)

    # ── 5. Main headline — huge, stroked, word-wrapped ───────────────────────
    clean = headline.strip()
    clean = re.sub(r'^[🔥🎌🎮🎵🌸⚡📢\s]*(TRENDING:|VIRAL:|話題:|ANIME:|GAMING:)\s*',
                   '', clean, flags=re.IGNORECASE).strip()
    clean = clean[:100]

    has_cjk = bool(re.search(r'[　-鿿]', clean))

    # Choose font size based on length
    char_count = len(clean)
    if char_count < 30:
        font_size, max_chars, line_h = 88, 22, 96
    elif char_count < 55:
        font_size, max_chars, line_h = 74, 26, 82
    else:
        font_size, max_chars, line_h = 62, 30, 72

    if has_cjk:
        max_chars = max_chars // 2
        line_h    = int(line_h * 1.1)

    hl_font = _load_font(font_size, bold=True)
    wrapped = textwrap.fill(clean, width=max_chars)
    lines   = wrapped.split("\n")[:3]

    total_h = len(lines) * line_h
    text_y  = H - 65 - total_h

    for i, line in enumerate(lines):
        y = text_y + i * line_h
        # Yellow highlight on first line only
        if i == 0:
            _draw_text_with_stroke(draw, (28, y), line, hl_font,
                                   fill=(255, 230, 0),
                                   stroke_color=(0, 0, 0), stroke_width=8)
        else:
            _draw_text_with_stroke(draw, (28, y), line, hl_font,
                                   fill=(255, 255, 255),
                                   stroke_color=(0, 0, 0), stroke_width=8)

    # ── 6. Accent underline below first line ─────────────────────────────────
    try:
        bbox = draw.textbbox((28, text_y), lines[0], font=hl_font)
        line_width = min(bbox[2] - bbox[0], W - 60)
        bar_y = text_y + line_h - 8
        draw.rectangle([(28, bar_y), (28 + line_width, bar_y + 6)],
                        fill=(*acc, 220))
    except Exception:
        pass

    # ── 7. Channel name — bottom-right, small ────────────────────────────────
    ch_font = _load_font(28, bold=True)
    ch_text = "SugoidesJapan"
    ch_bbox = draw.textbbox((0, 0), ch_text, font=ch_font)
    ch_w    = ch_bbox[2] - ch_bbox[0]
    draw.text((W - ch_w - 20, H - 42), ch_text, font=ch_font,
              fill=(*acc, 220))

    # ── 8. Merge ──────────────────────────────────────────────────────────────
    final = Image.alpha_composite(img, over).convert("RGB")
    final.save(img_path, "JPEG", quality=96)


# ── Category-aware background prompt ──────────────────────────────────────────

def _build_prompt(search_query: str, category: str) -> str:
    base = f"{search_query} Japan"
    templates = {
        "viral":    f"dramatic cinematic Japan scene, {base}, neon lights Tokyo street night, vivid oversaturated colors, NO people NO faces, ultra realistic",
        "anime":    f"anime-inspired Japan scene, {base}, Tokyo Akihabara neon signs, manga artwork style background, vivid colorful, NO faces, stunning visual",
        "gaming":   f"gaming setup Japan, {base}, Nintendo Sony PlayStation glowing screens colorful, neon cyberpunk Tokyo, NO people, vivid cinematic",
        "music":    f"Japanese music concert stage, {base}, neon stage lights Tokyo, colorful spotlights bokeh, vivid dramatic, NO people NO faces",
        "lifestyle": f"beautiful Japan travel scene, {base}, cherry blossoms sakura Mount Fuji Kyoto, golden hour warm light, vivid lush, NO people",
        "tech":     f"futuristic Japan technology, {base}, Tokyo skyline holographic neon, Sony robot AI lab, vivid cinematic, NO people NO faces",
        "news":     f"dramatic Japan cityscape, {base}, Tokyo skyline dramatic sky, vivid moody cinematic, NO people NO faces, ultra realistic 4K",
        "default":  f"vibrant Japan scene, {base}, Tokyo neon lights colorful vivid, cinematic dramatic, NO people NO faces, ultra realistic 4K",
    }
    return templates.get(category, templates["default"])


# ── Public API ─────────────────────────────────────────────────────────────────

def generate_thumbnail(
    title: str,
    search_query: str,
    output_dir: str = "output",
    category: str = "default",
) -> str:
    os.makedirs(output_dir, exist_ok=True)
    dest   = os.path.join(output_dir, "thumbnail.jpg")
    prompt = _build_prompt(search_query, category)

    success = (
        _gemini_background(prompt, dest)
        or _pollinations_background(prompt, dest)
    )
    if not success:
        print("[THUMB] Using vivid fallback background")
        _vivid_fallback_background(dest, category)
    else:
        # Boost colours so they pop under the overlay
        _boost_image(dest)

    _add_catchy_overlay(dest, title, category)

    size_kb = os.path.getsize(dest) // 1024
    print(f"[THUMB] ✅ Thumbnail → {dest}  ({size_kb} KB, {THUMB_W}×{THUMB_H})")
    return dest


if __name__ == "__main__":
    for cat in ["viral", "anime", "gaming", "music"]:
        path = generate_thumbnail(
            title="Nintendo Switch 2 Just Dropped — Japan Goes CRAZY",
            search_query="Nintendo Switch Japan gaming",
            output_dir=f"output/test_{cat}",
            category=cat,
        )
        print(f"[{cat}] → {path}")
