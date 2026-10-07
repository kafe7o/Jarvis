"""Настройки на гласа, четат се от променливи на средата (JARVIS_*)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Гласове на Microsoft Edge (edge-tts) по език. Борислав е мъжки български глас,
# Райън е британски, най-близо до филмовия Jarvis.
DEFAULT_VOICES = {
    "bg": "bg-BG-BorislavNeural",
    "en": "en-GB-RyanNeural",
    "de": "de-DE-ConradNeural",
    "ru": "ru-RU-DmitryNeural",
}


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


@dataclass
class VoiceConfig:
    # Език, на който се очаква да говорите. "auto" оставя Whisper да познае.
    language: str = "bg"

    # Разпознаване на реч
    stt_backend: str = "local"  # local (faster-whisper) или openai (Whisper API)
    whisper_model: str = "small"  # tiny/base/small/medium/large-v3
    whisper_device: str = "auto"

    # Синтез на реч
    tts_backend: str = "edge"  # edge (онлайн, естествен глас) или offline (pyttsx3)
    voice: str | None = None  # конкретен глас; иначе се избира по езика
    voices: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_VOICES))
    tts_rate: str = "+0%"

    # Микрофон
    sample_rate: int = 16000
    silence_seconds: float = 1.0  # колко тишина приключва изречението
    max_record_seconds: float = 30.0
    start_timeout_seconds: float = 10.0  # колко чакаме да започнете да говорите

    @classmethod
    def from_env(cls) -> "VoiceConfig":
        cfg = cls(
            language=_env("JARVIS_LANGUAGE", "bg"),
            stt_backend=_env("JARVIS_STT", "local"),
            whisper_model=_env("JARVIS_WHISPER_MODEL", "small"),
            whisper_device=_env("JARVIS_WHISPER_DEVICE", "auto"),
            tts_backend=_env("JARVIS_TTS", "edge"),
            voice=os.environ.get("JARVIS_VOICE") or None,
            tts_rate=_env("JARVIS_TTS_RATE", "+0%"),
            silence_seconds=_env_float("JARVIS_SILENCE_SECONDS", 1.0),
            max_record_seconds=_env_float("JARVIS_MAX_RECORD_SECONDS", 30.0),
        )
        return cfg

    @property
    def whisper_language(self) -> str | None:
        return None if self.language in ("", "auto") else self.language

    def voice_for(self, language: str | None) -> str:
        """Гласът за отговор: изрично зададен, иначе според езика на говорещия."""
        if self.voice:
            return self.voice
        lang = (language or self.whisper_language or "bg").split("-")[0].lower()
        return self.voices.get(lang, self.voices["en"])
