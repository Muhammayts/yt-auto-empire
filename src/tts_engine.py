"""
TTS Engine — voiceover + ASS captions.

Backend priority (automatic fallback chain):
  1. edge-tts  — neural voices, word-boundary captions (best quality)
  2. gTTS      — Google TTS, free, no key, rock-solid from any CI server
  3. Azure     — premium, requires AZURE_TTS_KEY secret

Set TTS_BACKEND env var to force: "edge", "gtts", or "azure"

Languages:
  en  →  en-US-AndrewNeural   (edge) | en  (gTTS)
  ja  →  ja-JP-NanamiNeural   (edge) | ja  (gTTS)
  ru  →  ru-RU-SvetlanaNeural (edge) | ru  (gTTS)
"""

import asyncio
import json
import os
import re
import subprocess
import time

import edge_tts
from gtts import gTTS

# ── Voice / language maps ─────────────────────────────────────────────────────
EDGE_VOICES = {
    "en": ["en-US-AndrewNeural", "en-US-GuyNeural"],
    "ja": ["ja-JP-NanamiNeural", "ja-JP-KeitaNeural"],
    "ru": ["ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural"],
}
GTTS_LANG = {"en": "en", "ja": "ja", "ru": "ru"}

# ── ASS header — news lower-third style ──────────────────────────────────────
ASS_HEADER = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
Timer: 100.0000

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Noto Sans CJK JP,56,&H00FFFFFF,&H000000FF,&H00000000,&HCC000000,-1,0,0,0,100,100,1,0,3,0,0,2,80,80,90,1
Style: English,DejaVu Sans,62,&H00FFFFFF,&H000000FF,&H00000000,&HCC000000,-1,0,0,0,100,100,1,0,3,0,0,2,80,80,90,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
# BorderStyle 3 = filled box background, Alignment 2 = bottom-centre
# Noto Sans CJK JP handles Japanese kanji/kana characters correctly


def _ass_time(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _write_ass_from_words(words: list, ass_path: str):
    """Word-boundary–based captions (accurate, from edge-tts)."""
    lines = []
    group = 7
    for i in range(0, len(words), group):
        g     = words[i:i + group]
        start = g[0]["start"]
        end   = g[-1]["start"] + g[-1]["duration"]
        text  = " ".join(w["word"] for w in g)
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},"
            f"Default,,0,0,0,,{text}"
        )
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER)
        f.write("\n".join(lines))


def _write_ass_estimated(text: str, total_duration: float, ass_path: str):
    """
    Time-estimated captions — used when word boundaries aren't available (gTTS).
    Groups words evenly across the audio duration.
    """
    words = text.split()
    group_size = 7
    groups = [words[i:i + group_size] for i in range(0, len(words), group_size)]
    if not groups:
        return

    slot = total_duration / len(groups)
    lines = []
    for i, grp in enumerate(groups):
        start = i * slot
        end   = start + slot
        caption = " ".join(grp)
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},"
            f"Default,,0,0,0,,{caption}"
        )
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER)
        f.write("\n".join(lines))


def _get_mp3_duration(mp3_path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", mp3_path],
        capture_output=True, text=True,
    )
    return float(json.loads(r.stdout)["format"]["duration"])


# ── Backend 1: edge-tts ───────────────────────────────────────────────────────

async def _try_edge_voice(text: str, voice: str, mp3_path: str) -> list:
    """Try one edge-tts voice. Returns word list or raises."""
    communicate = edge_tts.Communicate(text, voice)
    words, chunks = [], []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])
        elif chunk["type"] == "WordBoundary":
            words.append({
                "word":     chunk["text"],
                "start":    chunk["offset"] / 1e7,
                "duration": chunk["duration"] / 1e7,
            })
    if not chunks:
        raise RuntimeError("No audio received")
    with open(mp3_path, "wb") as f:
        for c in chunks:
            f.write(c)
    return words


async def _edge_tts_backend(text: str, lang: str, mp3_path: str, ass_path: str) -> bool:
    voices = EDGE_VOICES.get(lang, EDGE_VOICES["en"])
    for voice in voices:
        for attempt in range(2):
            try:
                print(f"[TTS/edge] Trying {voice} (attempt {attempt+1})")
                words = await _try_edge_voice(text, voice, mp3_path)
                _write_ass_from_words(words, ass_path)
                print(f"[TTS/edge] ✅ {voice} — {len(words)} words")
                return True
            except Exception as e:
                print(f"[TTS/edge] {voice} attempt {attempt+1} failed: {e}")
                await asyncio.sleep(3)
    return False


# ── Backend 2: gTTS (Google TTS) ─────────────────────────────────────────────

def _gtts_backend(text: str, lang: str, mp3_path: str, ass_path: str) -> bool:
    """
    Google TTS via gTTS library — free, no API key, works from any server.
    Uses estimated caption timing (no word boundaries available).
    """
    gtts_lang = GTTS_LANG.get(lang, "en")
    try:
        print(f"[TTS/gTTS] Generating with lang={gtts_lang}")
        tts = gTTS(text=text, lang=gtts_lang, slow=False)
        tts.save(mp3_path)
        # Estimate caption timing from audio duration
        duration = _get_mp3_duration(mp3_path)
        _write_ass_estimated(text, duration, ass_path)
        print(f"[TTS/gTTS] ✅ Audio {duration:.1f}s, estimated captions written")
        return True
    except Exception as e:
        print(f"[TTS/gTTS] Failed: {e}")
        return False


# ── Backend 3: Azure (optional premium) ──────────────────────────────────────

def _azure_backend(text: str, lang: str, mp3_path: str, ass_path: str) -> bool:
    voice = EDGE_VOICES.get(lang, EDGE_VOICES["en"])[0]
    key    = os.environ.get("AZURE_TTS_KEY")
    region = os.environ.get("AZURE_TTS_REGION", "eastus")
    if not key:
        return False
    try:
        import azure.cognitiveservices.speech as sdk
        cfg  = sdk.SpeechConfig(subscription=key, region=region)
        cfg.speech_synthesis_voice_name = voice
        cfg.set_speech_synthesis_output_format(
            sdk.SpeechSynthesisOutputFormat.Audio16Khz128KBitRateMonoMp3
        )
        audio_cfg  = sdk.audio.AudioOutputConfig(filename=mp3_path)
        synth      = sdk.SpeechSynthesizer(speech_config=cfg, audio_config=audio_cfg)
        words      = []

        def on_word(evt):
            words.append({
                "word":     evt.text,
                "start":    evt.audio_offset / 1e7,
                "duration": evt.duration.total_seconds(),
            })

        synth.synthesis_word_boundary.connect(on_word)
        result = synth.speak_text_async(text).get()
        if result.reason != sdk.ResultReason.SynthesizingAudioCompleted:
            raise RuntimeError(result.cancellation_details)
        _write_ass_from_words(words, ass_path)
        print(f"[TTS/azure] ✅ {len(words)} words")
        return True
    except Exception as e:
        print(f"[TTS/azure] Failed: {e}")
        return False


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_tts(text: str, lang: str = "ja", output_dir: str = "output") -> dict:
    os.makedirs(output_dir, exist_ok=True)
    mp3_path = os.path.join(output_dir, "voiceover.mp3")
    ass_path = os.path.join(output_dir, "captions.ass")
    forced   = os.environ.get("TTS_BACKEND", "").lower()

    backends = []
    if forced == "azure":
        backends = [("azure", lambda: _azure_backend(text, lang, mp3_path, ass_path))]
    elif forced == "gtts":
        backends = [("gTTS", lambda: _gtts_backend(text, lang, mp3_path, ass_path))]
    elif forced == "edge":
        backends = [("edge", lambda: asyncio.run(_edge_tts_backend(text, lang, mp3_path, ass_path)))]
    else:
        # Auto: try edge first, fall back to gTTS
        backends = [
            ("edge", lambda: asyncio.run(_edge_tts_backend(text, lang, mp3_path, ass_path))),
            ("gTTS", lambda: _gtts_backend(text, lang, mp3_path, ass_path)),
        ]
        if os.environ.get("AZURE_TTS_KEY"):
            backends.insert(0, ("azure", lambda: _azure_backend(text, lang, mp3_path, ass_path)))

    for name, fn in backends:
        print(f"[TTS] Trying backend: {name}")
        try:
            if fn():
                print(f"[TTS] ✅ Done via {name} → {mp3_path}")
                return {"mp3": mp3_path, "ass": ass_path}
        except Exception as e:
            print(f"[TTS] Backend {name} raised: {e}")

    raise RuntimeError("[TTS] All backends failed — cannot generate audio")
