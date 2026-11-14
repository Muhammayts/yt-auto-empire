"""
TTS Engine — generates voiceover MP3 and ASS captions file.

Supports two backends (set TTS_BACKEND env var):
  - "edge"  (default) : edge-tts, completely free, no account needed
  - "azure"           : Azure Cognitive Services, free tier 500K chars/month
                        requires AZURE_TTS_KEY + AZURE_TTS_REGION secrets

Voices:
  English : en-US-AndrewNeural  (edge) / en-US-AndrewNeural (azure)
  Russian : ru-RU-DmitryNeural  (edge) / ru-RU-DmitryNeural (azure)

Captions: DejaVu Sans (Ubuntu-compatible, handles Cyrillic + Latin)
"""

import asyncio
import os
import time
import edge_tts

VOICE_MAP = {
    "en": "en-US-AndrewNeural",
    "ru": "ru-RU-DmitryNeural",
}

ASS_HEADER = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
Timer: 100.0000

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,62,&H00FFFFFF,&H000000FF,&H00000000,&HCC000000,-1,0,0,0,100,100,1,0,3,0,0,2,80,80,90,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
# Style breakdown:
#   Fontsize 62      — large, readable on any screen
#   PrimaryColour    &H00FFFFFF  — white text
#   BackColour       &HCC000000 — 80% opaque black box (news lower-third)
#   Bold -1          — bold
#   BorderStyle 3    — opaque box background (not outline)
#   Outline 0        — no separate outline (box handles it)
#   Alignment 2      — bottom-center
#   MarginV 90       — 90px from bottom edge


def seconds_to_ass(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


# ── Edge TTS backend ──────────────────────────────────────────────────────────

async def _edge_tts(text: str, voice: str, mp3_path: str, ass_path: str):
    last_error = None
    for attempt in range(3):
        try:
            communicate = edge_tts.Communicate(text, voice)
            words = []
            audio_chunks = []

            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_chunks.append(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    words.append({
                        "word": chunk["text"],
                        "start": chunk["offset"] / 1e7,
                        "duration": chunk["duration"] / 1e7,
                    })

            with open(mp3_path, "wb") as f:
                for c in audio_chunks:
                    f.write(c)

            _write_ass(words, ass_path)
            print(f"[TTS/edge] ✅ {len(words)} words, audio saved")
            return

        except Exception as e:
            last_error = e
            print(f"[TTS/edge] Attempt {attempt+1}/3 failed: {e}")
            if attempt < 2:
                time.sleep(4 + attempt * 3)

    raise RuntimeError(f"[TTS/edge] All attempts failed: {last_error}")


# ── Azure TTS backend ─────────────────────────────────────────────────────────

def _azure_tts(text: str, voice: str, mp3_path: str, ass_path: str):
    """
    Azure Cognitive Services TTS.
    Free tier: 500,000 neural chars/month — no expiry.
    Set GitHub Secrets: AZURE_TTS_KEY and AZURE_TTS_REGION
    """
    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError:
        raise ImportError(
            "azure-cognitiveservices-speech not installed. "
            "Add it to requirements.txt or use TTS_BACKEND=edge"
        )

    key    = os.environ.get("AZURE_TTS_KEY")
    region = os.environ.get("AZURE_TTS_REGION", "eastus")
    if not key:
        raise EnvironmentError("AZURE_TTS_KEY secret not set")

    config   = speechsdk.SpeechConfig(subscription=key, region=region)
    config.speech_synthesis_voice_name = voice
    config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio16Khz128KBitRateMonoMp3
    )
    audio_config = speechsdk.audio.AudioOutputConfig(filename=mp3_path)
    synthesizer  = speechsdk.SpeechSynthesizer(
        speech_config=config, audio_config=audio_config
    )

    words = []

    def on_word(evt):
        words.append({
            "word": evt.text,
            "start": evt.audio_offset / 1e7,
            "duration": evt.duration.total_seconds(),
        })

    synthesizer.synthesis_word_boundary.connect(on_word)
    result = synthesizer.speak_text_async(text).get()

    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        raise RuntimeError(f"Azure TTS failed: {result.cancellation_details}")

    _write_ass(words, ass_path)
    print(f"[TTS/azure] ✅ {len(words)} words, audio saved")


# ── Shared helpers ────────────────────────────────────────────────────────────

def _write_ass(words: list, ass_path: str):
    lines = []
    group_size = 7
    for i in range(0, len(words), group_size):
        group = words[i:i + group_size]
        start = group[0]["start"]
        end   = group[-1]["start"] + group[-1]["duration"]
        caption = " ".join(w["word"] for w in group)
        lines.append(
            f"Dialogue: 0,{seconds_to_ass(start)},{seconds_to_ass(end)},"
            f"Default,,0,0,0,,{caption}"
        )
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER)
        f.write("\n".join(lines))


def generate_tts(text: str, lang: str = "ru", output_dir: str = "output") -> dict:
    os.makedirs(output_dir, exist_ok=True)
    voice    = VOICE_MAP.get(lang, VOICE_MAP["ru"])
    mp3_path = os.path.join(output_dir, "voiceover.mp3")
    ass_path = os.path.join(output_dir, "captions.ass")
    backend  = os.environ.get("TTS_BACKEND", "edge").lower()

    print(f"[TTS] Backend={backend}  Voice={voice}")

    if backend == "azure":
        _azure_tts(text, voice, mp3_path, ass_path)
    else:
        asyncio.run(_edge_tts(text, voice, mp3_path, ass_path))

    return {"mp3": mp3_path, "ass": ass_path}


if __name__ == "__main__":
    sample = "Срочно: российские войска начали новое наступление на востоке Украины."
    result = generate_tts(sample, lang="ru")
    print(result)
