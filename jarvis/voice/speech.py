"""Speech-to-text (local Whisper) and text-to-speech (neural voices, offline fallback)."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np

log = logging.getLogger("jarvis.voice")


class Transcriber:
    """Local speech recognition with faster-whisper; supports Bulgarian and ~100 other languages."""

    def __init__(self, model: str = "small", language: str | None = "bg"):
        from faster_whisper import WhisperModel

        self.language = language
        self.model = WhisperModel(model, device="auto", compute_type="int8")

    def __call__(self, audio: np.ndarray) -> str:
        samples = audio.astype(np.float32) / 32768.0
        segments, _ = self.model.transcribe(samples, language=self.language, vad_filter=True, beam_size=1)
        return " ".join(seg.text.strip() for seg in segments).strip()

    def file(self, path: str) -> str:
        segments, _ = self.model.transcribe(path, language=self.language, vad_filter=True, beam_size=1)
        return " ".join(seg.text.strip() for seg in segments).strip()


def clean_for_speech(text: str) -> str:
    text = re.sub(r"```.*?```", " (кодът е на екрана) ", text, flags=re.S)
    text = re.sub(r"https?://\S+", " (линкът е на екрана) ", text)
    return re.sub(r"[*_#`>|]+", "", text).strip()


class Speaker:
    """Speaks with Microsoft neural voices via edge-tts (needs internet), else pyttsx3 offline."""

    def __init__(self, voice: str = "bg-BG-BorislavNeural"):
        self.voice = voice

    def __call__(self, text: str) -> None:
        text = clean_for_speech(text)
        if not text:
            return
        try:
            self._edge(text)
        except Exception as exc:
            log.debug("edge-tts failed (%s); using offline voice", exc)
            self._offline(text)

    def _edge(self, text: str) -> None:
        import edge_tts

        fd, path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        try:
            asyncio.run(edge_tts.Communicate(text, self.voice).save(path))
            play_file(path)
        finally:
            os.unlink(path)

    def _offline(self, text: str) -> None:
        import pyttsx3

        engine = pyttsx3.init()
        engine.say(text)
        engine.runAndWait()


def play_file(path: str) -> None:
    if sys.platform == "darwin":
        subprocess.run(["afplay", path], check=True)
        return
    for player in (["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"], ["mpg123", "-q"], ["mpv", "--really-quiet"]):
        if shutil.which(player[0]):
            subprocess.run([*player, path], check=True)
            return
    if sys.platform.startswith("win"):
        script = (
            "Add-Type -AssemblyName presentationCore; $p = New-Object System.Windows.Media.MediaPlayer; "
            f"$p.Open('{path}'); $p.Play(); Start-Sleep -Milliseconds 300; "
            "while ($p.NaturalDuration.HasTimeSpan -eq $false) { Start-Sleep -Milliseconds 100 }; "
            "Start-Sleep -Seconds $p.NaturalDuration.TimeSpan.TotalSeconds; $p.Close()"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True)
        return
    raise RuntimeError("No audio player found; install ffmpeg or mpg123.")
