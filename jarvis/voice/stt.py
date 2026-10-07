"""Разпознаване на реч с Whisper (локално или през OpenAI API)."""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from jarvis.voice.config import VoiceConfig


@dataclass
class Transcript:
    text: str
    language: str | None = None


class SpeechToText(Protocol):
    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> Transcript: ...


class LocalWhisper:
    """faster-whisper: работи офлайн, поддържа български.

    За по-добро разпознаване на български ползвайте модел ``medium`` или
    ``large-v3`` (по-бавни, но по-точни от ``small``).
    """

    def __init__(self, model: str = "small", device: str = "auto",
                 language: str | None = "bg"):
        from faster_whisper import WhisperModel

        self.language = language
        self.model = WhisperModel(model, device=device, compute_type="default")

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> Transcript:
        if sample_rate != 16000:
            raise ValueError("faster-whisper очаква звук с 16 kHz")
        segments, info = self.model.transcribe(
            audio.astype(np.float32), language=self.language, beam_size=5, vad_filter=True
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        return Transcript(text=text, language=info.language)


def to_wav_bytes(audio: np.ndarray, sample_rate: int) -> bytes:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


class OpenAIWhisper:
    """Whisper през OpenAI API: без локален модел, нужен е OPENAI_API_KEY."""

    def __init__(self, language: str | None = "bg", model: str = "whisper-1"):
        from openai import OpenAI

        self.client = OpenAI()
        self.language = language
        self.model = model

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> Transcript:
        wav = to_wav_bytes(audio, sample_rate)
        kwargs = {"language": self.language} if self.language else {}
        result = self.client.audio.transcriptions.create(
            model=self.model, file=("speech.wav", wav, "audio/wav"),
            response_format="verbose_json", **kwargs,
        )
        language = getattr(result, "language", None) or self.language
        return Transcript(text=result.text.strip(), language=_iso_code(language))


_LANGUAGE_NAMES = {"bulgarian": "bg", "english": "en", "german": "de", "russian": "ru"}


def _iso_code(language: str | None) -> str | None:
    # Whisper API връща пълно име на езика ("bulgarian"), а локалният модел код ("bg").
    if not language:
        return None
    return _LANGUAGE_NAMES.get(language.lower(), language.lower())


def create_stt(config: VoiceConfig) -> SpeechToText:
    if config.stt_backend == "local":
        return LocalWhisper(config.whisper_model, config.whisper_device, config.whisper_language)
    if config.stt_backend == "openai":
        return OpenAIWhisper(config.whisper_language)
    raise ValueError(f"Непознат STT: {config.stt_backend!r} (local или openai)")
