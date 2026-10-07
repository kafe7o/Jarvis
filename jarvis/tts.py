"""Jarvis's voice for the app: Microsoft neural voices through edge-tts (free), or ElevenLabs when
ELEVENLABS_API_KEY is set (any voice, including your own clone)."""

from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.request

ENGLISH_VOICE = "en-GB-RyanNeural"  # British male, the film Jarvis
ELEVEN_DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"  # "George", warm British male
MAX_CHARS = 1500


def clean(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_#>`|]+", "", text)
    return re.sub(r"\s+", " ", text).strip()[:MAX_CHARS]


def pick_voice(text: str, configured: str) -> str:
    cyrillic = len(re.findall(r"[А-Яа-я]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    return ENGLISH_VOICE if latin > cyrillic else configured


def elevenlabs(text: str) -> bytes:
    voice = os.environ.get("ELEVENLABS_VOICE_ID") or ELEVEN_DEFAULT_VOICE
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128",
        data=json.dumps({"text": text, "model_id": os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2")}).encode(),
        headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def edge(text: str, voice: str) -> bytes:
    import edge_tts

    async def go() -> bytes:
        chunks = []
        async for part in edge_tts.Communicate(text, voice).stream():
            if part["type"] == "audio":
                chunks.append(part["data"])
        return b"".join(chunks)

    return asyncio.run(go())


def synthesize(text: str, configured_voice: str = "bg-BG-BorislavNeural", sample: str | None = None) -> bytes:
    """MP3 audio of ``text``; raises if no voice engine is available (the app then uses the browser's).

    The app reads long answers in parts; ``sample`` is the whole answer, so every part gets the same voice."""
    text = clean(text)
    if not text:
        raise ValueError("Няма текст за четене.")
    if os.environ.get("ELEVENLABS_API_KEY"):
        try:
            return elevenlabs(text)
        except Exception:
            pass  # fall back to the free voice
    return edge(text, pick_voice(clean(sample) if sample else text, configured_voice))
