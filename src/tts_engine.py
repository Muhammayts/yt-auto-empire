"""
TTS Engine — generates voiceover MP3 and ASS captions file.
Supports English (en-US-AndrewNeural) and Russian (ru-RU-DmitryNeural).
Uses DejaVu Sans for Ubuntu compatibility with Cyrillic + Latin scripts.
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
Style: Default,DejaVu Sans,52,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,30,30,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def seconds_to_ass(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


async def _generate_tts(text: str, voice: str, mp3_path: str, ass_path: str):
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

            # Build ASS captions — group ~6 words per line
            lines = []
            group_size = 6
            for i in range(0, len(words), group_size):
                group = words[i:i + group_size]
                start = group[0]["start"]
                end = group[-1]["start"] + group[-1]["duration"]
                caption = " ".join(w["word"] for w in group)
                lines.append(
                    f"Dialogue: 0,{seconds_to_ass(start)},{seconds_to_ass(end)},"
                    f"Default,,0,0,0,,{caption}"
                )

            with open(ass_path, "w", encoding="utf-8") as f:
                f.write(ASS_HEADER)
                f.write("\n".join(lines))

            print(f"[TTS] ✅ Generated {len(audio_chunks)} audio chunks, {len(words)} words")
            return True

        except Exception as e:
            last_error = e
            print(f"[TTS] Attempt {attempt + 1}/3 failed: {e}")
            if attempt < 2:
                time.sleep(3 + attempt * 2)

    raise RuntimeError(f"[TTS] All 3 attempts failed. Last error: {last_error}")


def generate_tts(text: str, lang: str = "en", output_dir: str = "output") -> dict:
    os.makedirs(output_dir, exist_ok=True)
    voice = VOICE_MAP.get(lang, VOICE_MAP["en"])
    mp3_path = os.path.join(output_dir, "voiceover.mp3")
    ass_path = os.path.join(output_dir, "captions.ass")

    print(f"[TTS] Generating voiceover — voice: {voice}")
    asyncio.run(_generate_tts(text, voice, mp3_path, ass_path))
    print(f"[TTS] Done → {mp3_path}, {ass_path}")
    return {"mp3": mp3_path, "ass": ass_path}


if __name__ == "__main__":
    sample = "Breaking: Russia launches major offensive. The situation is developing rapidly."
    result = generate_tts(sample, lang="en")
    print(result)
