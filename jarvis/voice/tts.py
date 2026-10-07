"""Синтез на реч: Microsoft Edge невронни гласове или офлайн pyttsx3."""

from __future__ import annotations

import asyncio
from typing import Callable, Protocol

from jarvis.voice import audio
from jarvis.voice.config import VoiceConfig


class TextToSpeech(Protocol):
    def speak(self, text: str, language: str | None = None) -> None: ...


class EdgeTTS:
    """edge-tts: естествени гласове, включително български (Борислав, Калина).

    Нужна е интернет връзка, но не и API ключ.
    """

    def __init__(self, config: VoiceConfig):
        self.config = config

    def synthesize(self, text: str, language: str | None = None) -> bytes:
        import edge_tts

        async def run() -> bytes:
            communicate = edge_tts.Communicate(
                text, self.config.voice_for(language), rate=self.config.tts_rate
            )
            chunks = []
            async for item in communicate.stream():
                if item["type"] == "audio":
                    chunks.append(item["data"])
            return b"".join(chunks)

        return asyncio.run(run())

    def speak(self, text: str, language: str | None = None) -> None:
        data = self.synthesize(text, language)
        if data:
            audio.play_mp3(data)


class OfflineTTS:
    """pyttsx3: работи без интернет през гласовете на операционната система.

    Български звучи само ако в системата има инсталиран български глас.
    """

    def __init__(self, config: VoiceConfig):
        import pyttsx3

        self.config = config
        self.engine = pyttsx3.init()
        self._voices = self.engine.getProperty("voices") or []

    def _voice_id(self, language: str) -> str | None:
        for v in self._voices:
            langs = [
                (lang.decode(errors="ignore") if isinstance(lang, bytes) else str(lang)).lower()
                for lang in (getattr(v, "languages", None) or [])
            ]
            haystack = " ".join([v.id.lower(), (v.name or "").lower(), *langs])
            if language in haystack:
                return v.id
        return None

    def speak(self, text: str, language: str | None = None) -> None:
        lang = (language or self.config.whisper_language or "bg").split("-")[0]
        voice_id = self.config.voice or self._voice_id(lang)
        if voice_id:
            self.engine.setProperty("voice", voice_id)
        self.engine.say(text)
        self.engine.runAndWait()


class FallbackTTS:
    """Пробва основния глас, а при грешка (напр. няма интернет) минава на резервния."""

    def __init__(self, primary: TextToSpeech, make_fallback: Callable[[], TextToSpeech],
                 log: Callable[[str], None] = print):
        self.primary = primary
        self.make_fallback = make_fallback
        self.log = log
        self._fallback: TextToSpeech | None = None

    def speak(self, text: str, language: str | None = None) -> None:
        if self._fallback is None:
            try:
                self.primary.speak(text, language)
                return
            except Exception as exc:
                self.log(f"[Гласът не работи ({exc}), минавам на офлайн глас]")
                self._fallback = self.make_fallback()
        self._fallback.speak(text, language)


def create_tts(config: VoiceConfig) -> TextToSpeech:
    if config.tts_backend == "edge":
        return FallbackTTS(EdgeTTS(config), lambda: OfflineTTS(config))
    if config.tts_backend == "offline":
        return OfflineTTS(config)
    raise ValueError(f"Непознат TTS: {config.tts_backend!r} (edge или offline)")
