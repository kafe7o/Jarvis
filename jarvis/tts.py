"""Jarvis's voice for the app: Microsoft neural voices through edge-tts (free), or ElevenLabs when
ELEVENLABS_API_KEY is set (any voice, including your own clone)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import urllib.request

log = logging.getLogger("jarvis.tts")

ENGLISH_VOICE = "en-GB-RyanNeural"  # British male, the film Jarvis
ELEVEN_DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"  # "George", warm British male
MAX_CHARS = 1500

# The voices offered in Settings > Глас, each with „Чуй“: the two Bulgarian ones, then Microsoft's multilingual
# voices, which read Bulgarian text too (with a slight accent) and English like a native.
VOICES = [
    ("bg-BG-BorislavNeural", "Борислав", "български, мъжки"),
    ("bg-BG-KalinaNeural", "Калина", "български, женски"),
    ("en-US-AndrewMultilingualNeural", "Andrew", "многоезичен, мъжки, топъл"),
    ("en-US-BrianMultilingualNeural", "Brian", "многоезичен, мъжки, спокоен"),
    ("en-AU-WilliamMultilingualNeural", "William", "многоезичен, мъжки"),
    ("de-DE-FlorianMultilingualNeural", "Florian", "многоезичен, мъжки, нисък"),
    ("fr-FR-RemyMultilingualNeural", "Rémy", "многоезичен, мъжки"),
    ("it-IT-GiuseppeMultilingualNeural", "Giuseppe", "многоезичен, мъжки"),
    ("en-US-AvaMultilingualNeural", "Ava", "многоезичен, женски"),
    ("en-US-EmmaMultilingualNeural", "Emma", "многоезичен, женски"),
    ("fr-FR-VivienneMultilingualNeural", "Vivienne", "многоезичен, женски"),
    ("de-DE-SeraphinaMultilingualNeural", "Seraphina", "многоезичен, женски"),
]
# How the owner may say the foreign names in Bulgarian ("сложи гласа на Андрю").
SPOKEN = {"Andrew": "андрю", "Brian": "брайън", "William": "уилям", "Florian": "флориан", "Rémy": "реми",
          "Giuseppe": "джузепе", "Ava": "ава", "Emma": "ема", "Vivienne": "вивиен", "Seraphina": "серафина"}
SAMPLE = "Здравей, аз съм Джарвис. Така ще звуча, когато ти отговарям. Hello, sir."


def voice_ok(voice: str) -> bool:
    """A Microsoft neural voice name, e.g. one from VOICES (or any other edge-tts voice typed in Settings)."""
    return bool(re.fullmatch(r"[a-z]{2,3}-[A-Z]{2}-[A-Za-z]+Neural", voice or ""))


def clean(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_#>`|]+", "", text)
    return re.sub(r"\s+", " ", text).strip()[:MAX_CHARS]


def pick_voice(text: str, configured: str) -> str:
    """The configured voice. Only a non-Bulgarian voice switches to the British one for English answers:
    a Bulgarian or multilingual voice reads everything, so Jarvis never changes voice in the middle of a conversation."""
    if configured.lower().startswith("bg-") or "multilingual" in configured.lower():
        return configured
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


EDGE_MIN = (7, 3, 1)  # Microsoft changed its sign-in in 2026; older edge-tts gets no audio, so every voice fell
FRESHENED = {"done": False}  # back to the browser's flat one, whatever the owner picked


def edge_version() -> tuple:
    from importlib.metadata import version

    try:
        return tuple(int(p) for p in re.findall(r"\d+", version("edge-tts"))[:3])
    except Exception:
        return ()


def freshen_edge() -> bool:
    """Once per run: when edge-tts is older than EDGE_MIN, update it and load the new one. True if it did."""
    if FRESHENED["done"] or edge_version() >= EDGE_MIN:
        return False
    FRESHENED["done"] = True
    import subprocess
    import sys

    log.warning("edge-tts %s is too old for Microsoft's voices now; updating it", ".".join(map(str, edge_version())))
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-U", "edge-tts>=%d.%d.%d" % EDGE_MIN],
                       capture_output=True, timeout=300, check=True)
    except Exception as exc:
        log.warning("could not update edge-tts: %s", exc)
        return False
    for name in [m for m in sys.modules if m == "edge_tts" or m.startswith("edge_tts.")]:
        del sys.modules[name]
    return True


def edge(text: str, voice: str) -> bytes:
    import edge_tts

    async def go() -> bytes:
        chunks = []
        async for part in edge_tts.Communicate(text, voice).stream():
            if part["type"] == "audio":
                chunks.append(part["data"])
        return b"".join(chunks)

    return asyncio.run(go())


def synthesize(text: str, configured_voice: str = "bg-BG-BorislavNeural", sample: str | None = None,
               exact: bool = False) -> bytes:
    """MP3 audio of ``text``; raises if no voice engine is available (the app then uses the browser's).

    The app reads long answers in parts; ``sample`` is the whole answer, so every part gets the same voice.
    ``exact`` (the „Чуй“ button in Settings > Глас) plays exactly ``configured_voice``."""
    text = clean(text)
    if not text:
        raise ValueError("Няма текст за четене.")
    if not exact and os.environ.get("ELEVENLABS_API_KEY"):
        try:
            return elevenlabs(text)
        except Exception:
            pass  # fall back to the free voice
    voice = configured_voice if exact else pick_voice(clean(sample) if sample else text, configured_voice)
    for attempt in range(3):  # a hiccup on the way to Microsoft must not hand the answer to the browser's flat voice
        try:
            return edge(text, voice)
        except Exception as exc:
            if attempt == 0 and freshen_edge():
                continue
            if attempt == 2:
                log.warning("Jarvis's voice %s failed, the app uses the browser's: %s", voice, exc)
                raise
    raise RuntimeError("unreachable")


def find_voice(asked: str, current: str) -> tuple[str, str, str] | None:
    """The voice in VOICES that ``asked`` names ("Калина", "kalina", "Andrew", "андрю", a voice id), or for
    "друг", "мъжки", "женски" or nothing the next one after ``current`` (of that kind). None if nothing fits."""
    from .router import bg

    said = re.sub(r"[\s.,!?]+", " ", (asked or "").strip().lower()).strip()
    for voice in VOICES:
        vid, name, _about = voice
        spellings = {vid.lower(), name.lower(), SPOKEN.get(name, "")} - {""}
        if said in spellings or any(re.fullmatch(bg(w), said, re.I) for w in spellings if not w.isascii()):
            return voice
    kind = ("женски" if re.search(bg(r"женск|жена"), said, re.I) else
            "мъжки" if re.search(bg(r"мъжк|мъж"), said, re.I) else "")
    if said and not kind and not re.fullmatch(bg(r"(?:друг|друго|следващ|нов|по-добър|по-хубав|хубав)\S*(?: глас)?"), said, re.I):
        return None
    at = next((i for i, v in enumerate(VOICES) if v[0] == current), -1)
    after = VOICES[at + 1:] + VOICES[:at + 1]
    return next((v for v in after if v[0] != current and kind in v[2]), None)
