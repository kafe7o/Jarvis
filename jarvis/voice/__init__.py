"""Гласов слой на Jarvis: разпознаване на реч (Whisper) и синтез на реч (TTS).

Модулът е самостоятелен: работи с всяко чат ядро, което може да се извика
като ``respond(text) -> str``. Вижте ``docs/voice.md``.
"""

from jarvis.voice.config import VoiceConfig
from jarvis.voice.session import VoiceSession

__all__ = ["VoiceConfig", "VoiceSession"]
